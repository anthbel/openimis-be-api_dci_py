"""
Tests for DCISearchClient. The HTTP transport is a mock session; the round trip
test routes the client's request into the real sync_search view.
"""
import json as jsonlib
import os
import uuid
from types import SimpleNamespace
from unittest import mock

import requests
from django.core.serializers.json import DjangoJSONEncoder
from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from api_dci import record_providers
from api_dci.client import (
    DCIClientConfigurationError,
    DCIHTTPError,
    DCIResponseError,
    DCISearchClient,
    DCITransportError,
)
from api_dci.record_providers import register_record_provider
from api_dci.tests.record_provider_fixtures import HOUSEHOLDS, PROVIDER_RIGHT
from api_dci.views.person_viewset import sync_search

BASE_URL = "https://registry.example.org/api/api_dci"
CREDENTIAL_ENV = "TEST_DCI_REGISTRY_TOKEN"
CREDENTIAL = "token-value"


def fake_response(status_code=200, body=None, json_error=None):
    response = mock.Mock(status_code=status_code)
    if json_error is not None:
        response.json.side_effect = json_error
    else:
        response.json.return_value = body
    return response


def record_page(request_json, records, next_cursor, item_status="succ", **overrides):
    item = request_json["message"]["search_request"][0]
    body = {
        "signature": "",
        "header": {"status": "succ", "meta": {"next_cursor": next_cursor}},
        "message": {
            "transaction_id": request_json["message"]["transaction_id"],
            "search_response": [{
                "reference_id": item["reference_id"],
                "status": item_status,
                "data": {
                    "reg_record_type": item["search_criteria"]["reg_record_type"],
                    "reg_records": records,
                },
            }],
        },
    }
    for path, value in overrides.items():
        target = body
        keys = path.split("__")
        for key in keys[:-1]:
            target = target[key] if not isinstance(target, list) else target[int(key)]
        target[keys[-1]] = value
    return body


class ClientTestBase(SimpleTestCase):

    def setUp(self):
        env = mock.patch.dict(os.environ, {CREDENTIAL_ENV: CREDENTIAL})
        env.start()
        self.addCleanup(env.stop)
        self.session = mock.Mock()

    def client_for(self, base_url=BASE_URL, **kwargs):
        return DCISearchClient(base_url, CREDENTIAL_ENV, session=self.session, **kwargs)

    def answer_with(self, records, next_cursor, **overrides):
        def post(url, json=None, **kwargs):
            return fake_response(body=record_page(json, records, next_cursor, **overrides))
        self.session.post.side_effect = post


class DCISearchClientTLSTestCase(ClientTestBase):

    def test_non_https_base_urls_are_refused(self):
        for url in ("http://registry.example.org/api", "ftp://registry.example.org",
                    "registry.example.org/api", "https://", "HTTP://registry.example.org"):
            with self.subTest(url=url), self.assertRaises(DCIClientConfigurationError):
                self.client_for(url)

    def test_https_base_url_is_accepted(self):
        self.client_for("HTTPS://registry.example.org/api/api_dci/")


class DCISearchClientRequestTestCase(ClientTestBase):

    def test_request_carries_type_query_cursor_page_size_and_credential(self):
        self.answer_with(HOUSEHOLDS[:2], "2")
        query = {"type": "openimis:handoff", "value": {"consumer": "mis-1"}}
        self.client_for(sender_id="programme-mis").sync_search(
            "Household", query, cursor="0", limit=2
        )
        self.session.post.assert_called_once()
        call = self.session.post.call_args
        self.assertEqual(call.args[0], f"{BASE_URL}/registry/sync/search")
        self.assertEqual(call.kwargs["headers"]["Authorization"], f"Bearer {CREDENTIAL}")
        self.assertFalse(call.kwargs["allow_redirects"])
        self.assertTrue(call.kwargs["verify"])
        self.assertEqual(call.kwargs["timeout"], (10, 30))
        payload = call.kwargs["json"]
        self.assertEqual(payload["header"]["action"], "search")
        self.assertEqual(payload["header"]["sender_id"], "programme-mis")
        self.assertEqual(payload["header"]["meta"], {"cursor": "0"})
        criteria = payload["message"]["search_request"][0]["search_criteria"]
        self.assertEqual(criteria["reg_record_type"], "Household")
        self.assertEqual(criteria["query"], query)
        self.assertEqual(criteria["pagination"], {"page_size": 2})

    def test_first_page_sends_no_cursor(self):
        self.answer_with(HOUSEHOLDS[:2], "2")
        self.client_for().sync_search("Household", {}, cursor=None, limit=2)
        self.assertNotIn("meta", self.session.post.call_args.kwargs["json"]["header"])

    def test_timeouts_are_configurable(self):
        self.answer_with([], None)
        self.client_for(timeout=(3, 7)).sync_search("Household", {}, None, 1)
        self.assertEqual(self.session.post.call_args.kwargs["timeout"], (3, 7))

    def test_returns_records_and_next_cursor(self):
        self.answer_with(HOUSEHOLDS[:2], "2")
        self.assertEqual(
            self.client_for().sync_search("Household", {}, None, 2), (HOUSEHOLDS[:2], "2")
        )
        self.answer_with(HOUSEHOLDS[4:], None)
        self.assertEqual(
            self.client_for().sync_search("Household", {}, "4", 2), (HOUSEHOLDS[4:], None)
        )

    def test_missing_credential_is_a_configuration_error(self):
        for value in (None, "", "  "):
            with self.subTest(value=value), mock.patch.dict(os.environ):
                os.environ.pop(CREDENTIAL_ENV, None)
                if value is not None:
                    os.environ[CREDENTIAL_ENV] = value
                with self.assertRaises(DCIClientConfigurationError) as raised:
                    self.client_for().sync_search("Household", {}, None, 2)
                self.assertIn(CREDENTIAL_ENV, str(raised.exception))
        self.session.post.assert_not_called()

    def test_invalid_arguments_are_refused_before_sending(self):
        client = self.client_for()
        for args in (("", {}, None, 2), ("Household", {}, 5, 2), ("Household", {}, None, 0),
                     ("Household", {}, None, 101), ("Household", {}, None, True)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                client.sync_search(*args)
        self.session.post.assert_not_called()


class DCISearchClientErrorTestCase(ClientTestBase):

    def _search(self):
        return self.client_for().sync_search("Household", {}, None, 2)

    def test_timeout_and_connection_failures_are_transport_errors(self):
        for error in (requests.Timeout("slow"), requests.ConnectionError("down"),
                      requests.exceptions.SSLError("bad certificate")):
            self.session.post.side_effect = error
            with self.subTest(error=error), self.assertRaises(DCITransportError):
                self._search()

    def test_non_200_status_is_an_http_error_with_its_code(self):
        for code in (301, 302, 400, 401, 403, 500, 503):
            self.session.post.side_effect = None
            self.session.post.return_value = fake_response(status_code=code, body={})
            with self.subTest(code=code), self.assertRaises(DCIHTTPError) as raised:
                self._search()
            self.assertEqual(raised.exception.status_code, code)

    def test_body_that_is_not_json_is_a_response_error(self):
        self.session.post.return_value = fake_response(json_error=ValueError("not json"))
        with self.assertRaises(DCIResponseError):
            self._search()

    def test_malformed_or_rejected_pages_are_response_errors(self):
        cases = {
            "rejected item": {"item_status": "rjct"},
            "no records": {"message__search_response__0__data": {"reg_record_type": "Household"}},
            "records not a list": {
                "message__search_response__0__data__reg_records": {"id": "hh-1"}},
            "other record type": {
                "message__search_response__0__data__reg_record_type": "Person"},
            "other reference": {"message__search_response__0__reference_id": "ref-other"},
            "cursor not a string": {"header__meta": {"next_cursor": 3}},
            "no meta": {"header__meta": None},
            "no search response": {"message__search_response": []},
        }
        for name, overrides in cases.items():
            with self.subTest(case=name):
                self.answer_with(HOUSEHOLDS[:2], "2", **overrides)
                with self.assertRaises(DCIResponseError):
                    self._search()


class DCISearchClientRoundTripTestCase(ClientTestBase):
    """The client's request is served by the real sync_search view and provider."""

    def setUp(self):
        super().setUp()
        registry = mock.patch.dict(record_providers._providers, clear=True)
        registry.start()
        self.addCleanup(registry.stop)
        register_record_provider(
            "Household", "api_dci.tests.record_provider_fixtures.FakeHouseholdProvider"
        )
        factory = APIRequestFactory()
        consumer = SimpleNamespace(pk=uuid.uuid4(), is_authenticated=True,
                                   has_perm=lambda perm: perm == PROVIDER_RIGHT)

        def post(url, json=None, **kwargs):
            request = factory.post(url, json, format="json")
            force_authenticate(request, user=consumer)
            response = sync_search(request)
            body = jsonlib.loads(jsonlib.dumps(response.data, cls=DjangoJSONEncoder))
            return fake_response(status_code=response.status_code, body=body)

        self.session.post.side_effect = post

    def test_client_pages_through_every_record(self):
        client = self.client_for()
        records, cursor, pages = [], None, 0
        while True:
            page, cursor = client.sync_search("Household", {}, cursor, 2)
            records.extend(page)
            pages += 1
            if cursor is None:
                break
        self.assertEqual(records, HOUSEHOLDS)
        self.assertEqual(pages, 3)

    def test_unknown_type_is_refused_by_the_view(self):
        with self.assertRaises(DCIHTTPError) as raised:
            self.client_for().sync_search("Unknown", {}, None, 2)
        self.assertEqual(raised.exception.status_code, 403)


class DCISearchClientEnvironmentTestCase(SimpleTestCase):
    """The client's own session ignores ~/.netrc and proxy settings from the environment."""

    def test_default_session_does_not_trust_the_environment(self):
        client = DCISearchClient(BASE_URL, CREDENTIAL_ENV)
        self.assertIs(client.session.trust_env, False)
