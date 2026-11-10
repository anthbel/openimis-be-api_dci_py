"""
Record providers: search targets other than Person, keyed by reg_record_type.

A provider is reached by dotted path, either from the `record_providers` setting
({reg_record_type: dotted path}) or through `register_record_provider`. The path names
a class (instantiated without arguments) or an instance implementing:

- `required_right() -> str`: the right a caller must hold to search this type;
- `search(user, query, cursor, limit) -> (records, next_cursor)`: one page of records,
  `next_cursor` an opaque string, or None after the last page.

On the wire, the page size is `search_criteria.pagination.page_size`; the cursor travels
in `header.meta.cursor` on the request and `header.meta.next_cursor` on the response.
"""
import logging

from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from .config import REGISTRY_TYPES

logger = logging.getLogger(__name__)

# Record types served by the Individual search; a provider cannot take them.
PERSON_RECORD_TYPES = frozenset(
    {"Person"} | {registry["record_type"] for registry in REGISTRY_TYPES.values()}
)

MAX_PAGE_SIZE = 100

_providers = {}


class UnknownRecordType(Exception):
    """A search names a record type that is neither Person nor a registered provider."""


def register_record_provider(reg_record_type, dotted_path):
    """
    Resolve `dotted_path` and serve `reg_record_type` from it.

    Raises ImproperlyConfigured when the path does not resolve to an object with the
    provider protocol, when the type is a Person type, or when the type is already
    served by another path.
    """
    if not isinstance(reg_record_type, str) or not reg_record_type.strip():
        raise ImproperlyConfigured(f"Invalid record type {reg_record_type!r}")
    if reg_record_type in PERSON_RECORD_TYPES:
        raise ImproperlyConfigured(
            f"Record type {reg_record_type!r} is served by the Person search"
        )
    current = _providers.get(reg_record_type)
    if current is not None:
        if current[0] == dotted_path:
            return
        raise ImproperlyConfigured(
            f"Record type {reg_record_type!r} is already served by {current[0]}"
        )
    _providers[reg_record_type] = (dotted_path, _resolve(reg_record_type, dotted_path))


def load_record_providers(paths):
    """Register every {reg_record_type: dotted path} of the `record_providers` setting."""
    if not isinstance(paths, dict):
        raise ImproperlyConfigured("record_providers must map record types to dotted paths")
    for reg_record_type, dotted_path in paths.items():
        register_record_provider(reg_record_type, dotted_path)


def get_record_provider(reg_record_type):
    """The provider serving `reg_record_type`, or None."""
    entry = _providers.get(reg_record_type)
    return entry[1] if entry else None


def _resolve(reg_record_type, dotted_path):
    try:
        target = import_string(dotted_path)
        provider = target() if isinstance(target, type) else target
    except Exception as exc:
        raise ImproperlyConfigured(
            f"Record provider {dotted_path!r} for {reg_record_type!r} does not resolve: {exc}"
        ) from exc
    for method in ("required_right", "search"):
        if not callable(getattr(provider, method, None)):
            raise ImproperlyConfigured(
                f"Record provider {dotted_path!r} for {reg_record_type!r} has no {method}()"
            )
    return provider


_ABSENT = object()


def _declared_record_types(request_data):
    """Each reg_record_type the message declares, _ABSENT for a criteria without one."""
    message = request_data.get("message") if isinstance(request_data, dict) else None
    if not isinstance(message, dict):
        return []
    criteria_list = []
    if isinstance(message.get("search_criteria"), dict):
        criteria_list.append(message["search_criteria"])
    items = message.get("search_request")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("search_criteria"), dict):
                criteria_list.append(item["search_criteria"])
    return [criteria.get("reg_record_type", _ABSENT) for criteria in criteria_list]


def search_target(request_data):
    """
    The provider serving every search in this message, or None for a Person search.

    A criteria without reg_record_type is a Person search. Raises UnknownRecordType when
    a declared type is neither a Person type nor registered, or when the message mixes
    targets.
    """
    targets = set()
    for record_type in _declared_record_types(request_data):
        if record_type is _ABSENT or (
            isinstance(record_type, str) and record_type in PERSON_RECORD_TYPES
        ):
            targets.add(None)
            continue
        if not isinstance(record_type, str) or record_type not in _providers:
            raise UnknownRecordType(f"Unknown reg_record_type {record_type!r}")
        targets.add(record_type)
    if len(targets) > 1:
        raise UnknownRecordType("One message cannot mix record types")
    record_type = targets.pop() if targets else None
    return get_record_provider(record_type) if record_type else None


def provider_search_args(request_data, search_request):
    """
    The (query, cursor, limit) a provider search takes from this request.

    Raises ValueError when the cursor is not a string or the page size is not an
    integer between 1 and MAX_PAGE_SIZE.
    """
    criteria = search_request.get("search_criteria") or {}
    meta = (request_data.get("header") or {}).get("meta") or {}
    cursor = meta.get("cursor") if isinstance(meta, dict) else None
    if cursor is not None and not isinstance(cursor, str):
        raise ValueError("header.meta.cursor must be a string")
    pagination = criteria.get("pagination") or {}
    limit = pagination.get("page_size", MAX_PAGE_SIZE)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"pagination.page_size must be an integer from 1 to {MAX_PAGE_SIZE}")
    return criteria.get("query"), cursor, limit


def run_provider_search(provider, user, request_data, search_request):
    """Call the provider and check the shape of what it returns."""
    query, cursor, limit = provider_search_args(request_data, search_request)
    records, next_cursor = provider.search(user, query, cursor, limit)
    if not isinstance(records, list):
        raise TypeError("A record provider must return its records as a list")
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise TypeError("A record provider must return next_cursor as a string or None")
    return records, next_cursor
