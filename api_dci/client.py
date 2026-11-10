"""
Outbound DCI search client: pulls pages of a record type from another registry's
api_dci over HTTPS.

The request and response follow api_dci.record_providers: the page size goes in
search_criteria.pagination.page_size, the cursor in header.meta.cursor, and the next
cursor comes back in header.meta.next_cursor.
"""
import os
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

from .configurations import DCIConfig
from .record_providers import MAX_PAGE_SIZE

SEARCH_PATH = "/registry/sync/search"


class DCIClientError(Exception):
    """Base class of every error DCISearchClient raises."""


class DCIClientConfigurationError(DCIClientError):
    """The client cannot be used as configured: base URL not HTTPS, or no credential."""


class DCITransportError(DCIClientError):
    """The request did not complete: timeout, connection or TLS failure."""


class DCIHTTPError(DCIClientError):
    """The registry answered with a status other than 200."""

    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


class DCIResponseError(DCIClientError):
    """The registry answered 200 with a body that is not a successful search page."""


class DCISearchClient:
    """
    Sync search against one registry.

    `credential_env` names the environment key that holds the bearer token of the
    service account; it is read on every call.
    """

    def __init__(self, base_url, credential_env, *, sender_id=None, receiver_id=None,
                 timeout=(10, 30), session=None):
        parts = urlsplit(base_url or "")
        if parts.scheme.lower() != "https" or not parts.netloc:
            raise DCIClientConfigurationError(
                f"DCI registry URL must be an https:// URL, got {base_url!r}"
            )
        self.search_url = base_url.rstrip("/") + SEARCH_PATH
        self.credential_env = credential_env
        self.sender_id = sender_id or DCIConfig.get_registry_id()
        self.receiver_id = receiver_id
        self.timeout = timeout
        if session is None:
            session = requests.Session()
            # ~/.netrc or proxy variables must not replace the Bearer credential.
            session.trust_env = False
        self.session = session

    def sync_search(self, reg_record_type, query, cursor=None, limit=MAX_PAGE_SIZE):
        """
        One page of `reg_record_type`: returns (records, next_cursor), next_cursor None
        after the last page.
        """
        if not isinstance(reg_record_type, str) or not reg_record_type.strip():
            raise ValueError("reg_record_type must be a non-empty string")
        if cursor is not None and not isinstance(cursor, str):
            raise ValueError("cursor must be a string or None")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be an integer from 1 to {MAX_PAGE_SIZE}")

        headers = {
            "Authorization": f"Bearer {self._credential()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = self._payload(reg_record_type, query, cursor, limit)
        try:
            response = self.session.post(
                self.search_url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
                verify=True,
            )
        except requests.Timeout as exc:
            raise DCITransportError(f"DCI search timed out: {exc}") from exc
        except requests.RequestException as exc:
            raise DCITransportError(f"DCI search failed: {exc}") from exc

        if response.status_code != 200:
            raise DCIHTTPError(
                response.status_code, f"DCI search answered HTTP {response.status_code}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise DCIResponseError("DCI search answered a body that is not JSON") from exc
        return self._page(body, payload, reg_record_type)

    def _credential(self):
        credential = os.environ.get(self.credential_env or "", "").strip()
        if not credential:
            raise DCIClientConfigurationError(
                f"No credential in environment key {self.credential_env!r}"
            )
        return credential

    def _payload(self, reg_record_type, query, cursor, limit):
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        header = {
            "version": "1.0.0",
            "message_id": uuid.uuid4().hex,
            "message_ts": now,
            "action": "search",
            "sender_id": self.sender_id,
            "total_count": 1,
            "is_msg_encrypted": False,
        }
        if self.receiver_id:
            header["receiver_id"] = self.receiver_id
        if cursor is not None:
            header["meta"] = {"cursor": cursor}
        return {
            "signature": "",
            "header": header,
            "message": {
                "transaction_id": uuid.uuid4().hex,
                "search_request": [{
                    "reference_id": uuid.uuid4().hex,
                    "timestamp": now,
                    "search_criteria": {
                        "version": "1.0.0",
                        "reg_record_type": reg_record_type,
                        "query_type": "expression",
                        "query": query,
                        "pagination": {"page_size": limit},
                    },
                }],
            },
        }

    @staticmethod
    def _page(body, payload, reg_record_type):
        sent = payload["message"]["search_request"][0]
        try:
            item = body["message"]["search_response"][0]
            meta = body["header"]["meta"]
            next_cursor = meta["next_cursor"]
            data = item["data"]
            records = data["reg_records"]
            item_status = item["status"]
            reference_id = item["reference_id"]
            record_type = data["reg_record_type"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DCIResponseError(f"DCI search page is malformed: missing {exc}") from exc
        if item_status != "succ":
            raise DCIResponseError(
                f"DCI search was not successful: {item_status} "
                f"{item.get('status_reason_code', '')}".strip()
            )
        if reference_id != sent["reference_id"] or record_type != reg_record_type:
            raise DCIResponseError("DCI search page answers another request")
        if not isinstance(records, list):
            raise DCIResponseError("DCI search page records are not a list")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise DCIResponseError("DCI search page next_cursor is not a string")
        return records, next_cursor
