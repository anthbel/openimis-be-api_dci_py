"""
No request content in the logs.

Requests carrying a national id, a name and a signature go through the real views; every
record the api_dci loggers emit at DEBUG and above, traceback included, must hold none of them.
"""
import asyncio
import logging
from unittest import mock

from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITestCase, force_authenticate

from api_dci import record_providers
from api_dci.record_providers import register_record_provider
from api_dci.tests.mixin import LogInMixin
from api_dci.tests.record_provider_fixtures import PROVIDER_RIGHT
from api_dci.views.person_viewset import async_search, sync_search

NATIONAL_ID = "NID-4471-0098-2231"
FAMILY_NAME = "Ndayikengurukiye"
SIGNATURE = "sig-7f3a91c2e8"
REQUEST_CONTENT = (NATIONAL_ID, FAMILY_NAME, SIGNATURE)

SYNC_URL = "/api/api_dci/registry/sync/search"
ASYNC_URL = "/api/api_dci/registry/async/search"
CALLBACK_URI = "https://mis.example.org/registry/on-search"
FAILURES = (
    (ValueError, status.HTTP_400_BAD_REQUEST),
    (RuntimeError, status.HTTP_500_INTERNAL_SERVER_ERROR),
)


class CapturedLogs(logging.Handler):
    """Keeps each record as a formatter renders it, traceback included."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))


class LogCaptureMixin:
    """Captures everything the api_dci loggers emit from DEBUG up during each test."""

    def setUp(self):
        super().setUp()
        self.logs = CapturedLogs()
        api_logger = logging.getLogger("api_dci")
        previous_level = api_logger.level
        api_logger.addHandler(self.logs)
        api_logger.setLevel(logging.DEBUG)
        self.addCleanup(api_logger.setLevel, previous_level)
        self.addCleanup(api_logger.removeHandler, self.logs)

    def assertNoRequestContentLogged(self):
        for line in self.logs.lines:
            for value in REQUEST_CONTENT:
                self.assertNotIn(value, line)


def header(action="search", **extra):
    return {
        "version": "1.0.0",
        "message_id": "msg-log-001",
        "message_ts": "2026-11-11T00:00:00Z",
        "action": action,
        "sender_id": "programme-mis",
        "receiver_id": "registry",
        **extra,
    }


def search_payload(query_type, query, reg_record_type=None, **header_extra):
    criteria = {"query_type": query_type, "query": query}
    if reg_record_type:
        criteria["reg_record_type"] = reg_record_type
    return {
        "signature": SIGNATURE,
        "header": header(**header_extra),
        "message": {
            "transaction_id": "txn-log-001",
            "search_request": [{
                "reference_id": "ref-log-001",
                "timestamp": "2026-11-11T00:00:00Z",
                "search_criteria": criteria,
            }],
        },
    }


def person_message(action):
    return {
        "signature": SIGNATURE,
        "header": header(action=action),
        "message": {
            "transaction_id": "txn-log-person",
            "person": {
                "identifier": [{"identifier_type": "NIN", "identifier_value": NATIONAL_ID}],
                "name": {"given_name": "Aline", "family_name": FAMILY_NAME},
            },
        },
    }


class LogCaptureSanityTestCase(LogCaptureMixin, SimpleTestCase):
    def test_capture_sees_api_dci_records(self):
        logging.getLogger("api_dci.views.person_viewset").debug("probe %s", NATIONAL_ID)
        with self.assertRaises(AssertionError):
            self.assertNoRequestContentLogged()


class PersonViewsLogNoRequestContentTestCase(LogCaptureMixin, LogInMixin, APITestCase):
    """Person search, create, update, subscription, notify and txn status views."""

    def setUp(self):
        super().setUp()
        self.test_user = self.get_or_create_user_api()
        self.login()

    def test_sync_search_by_national_id(self):
        payload = search_payload("idtype-value", {"type": "NIN", "value": NATIONAL_ID})
        payload["message"]["search_request"][0]["search_criteria"]["query"]["family_name"] = FAMILY_NAME
        response = self.client.post(SYNC_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNoRequestContentLogged()

    def test_sync_search_rejected_by_validation(self):
        payload = search_payload("idtype-value", {"type": "NIN", "value": NATIONAL_ID,
                                                  "family_name": FAMILY_NAME})
        del payload["message"]["search_request"][0]["timestamp"]
        response = self.client.post(SYNC_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNoRequestContentLogged()

    def test_async_search_whose_query_fails_in_the_background(self):
        # A national id under dob makes the date lookup fail with a message quoting it.
        from api_dci.tasks import BackgroundSearchTask

        payload = search_payload("sync", {"dob": NATIONAL_ID, "lastName": FAMILY_NAME},
                                 sender_uri=CALLBACK_URI)
        with mock.patch.object(BackgroundSearchTask, "start", BackgroundSearchTask.run), \
                mock.patch("api_dci.tasks.connection"), \
                mock.patch("api_dci.tasks.requests.post") as post:
            response = self.client.post(ASYNC_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        post.assert_not_called()
        self.assertTrue(self.logs.lines)
        self.assertNoRequestContentLogged()

    def test_async_search_rejected_by_validation(self):
        payload = search_payload("sync", {"nid": NATIONAL_ID, "lastName": FAMILY_NAME})
        del payload["header"]["message_ts"]
        response = self.client.post(ASYNC_URL, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNoRequestContentLogged()

    def test_person_create_failures_whose_message_repeats_the_identifier(self):
        # Any step of the create can fail with a message quoting the person; the registry
        # type lookup is the first step after validation.
        for error_class, expected_status in FAILURES:
            with self.subTest(error=error_class.__name__), mock.patch(
                "api_dci.views.person_create_view.get_registry_type",
                side_effect=error_class(f"identifier {NATIONAL_ID} of {FAMILY_NAME} refused"),
            ):
                response = self.client.post("/api/api_dci/registry/person",
                                            person_message("create"), format="json")
                self.assertEqual(response.status_code, expected_status)
        self.assertNoRequestContentLogged()

    def test_person_create_rejected_by_validation(self):
        payload = person_message("create")
        payload["message"]["person"]["birth_date"] = NATIONAL_ID
        response = self.client.post("/api/api_dci/registry/person", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNoRequestContentLogged()

    def test_person_update_addressed_by_a_national_id(self):
        response = self.client.put(f"/api/api_dci/registry/person/{NATIONAL_ID}/update",
                                   person_message("update"), format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNoRequestContentLogged()

    def test_person_update_failures_whose_message_repeats_the_identifier(self):
        from individual.models import Individual

        individual = Individual(first_name="Aline", last_name="Log", dob="1990-01-15", json_ext={})
        individual.save(user=self.test_user)
        person_id = f"openimis:individual:{individual.uuid}"
        for error_class, expected_status in FAILURES:
            with self.subTest(error=error_class.__name__), mock.patch(
                "api_dci.views.person_create_view.get_registry_type",
                side_effect=error_class(f"identifier {NATIONAL_ID} of {FAMILY_NAME} refused"),
            ):
                response = self.client.put(f"/api/api_dci/registry/person/{person_id}/update",
                                           person_message("update"), format="json")
                self.assertEqual(response.status_code, expected_status)
        self.assertNoRequestContentLogged()

    def test_subscribe_with_a_national_id_filter(self):
        payload = {
            "signature": SIGNATURE,
            "header": header(action="subscribe", sender_uri=CALLBACK_URI),
            "message": {
                "transaction_id": "txn-log-sub",
                "subscribe_request": [{
                    "reference_id": "ref-log-sub",
                    "timestamp": "2026-11-11T00:00:00Z",
                    "event_type": "UPDATE",
                    "filter": {"idtype-value": {"id_type": "NIN", "id_value": NATIONAL_ID},
                               "family_name": FAMILY_NAME},
                }],
            },
        }
        response = self.client.post("/api/api_dci/registry/subscribe", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        del payload["message"]["subscribe_request"][0]["timestamp"]
        response = self.client.post("/api/api_dci/registry/subscribe", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNoRequestContentLogged()

    def test_unsubscribe_naming_a_national_id(self):
        payload = {
            "signature": SIGNATURE,
            "header": header(action="unsubscribe"),
            "message": {
                "transaction_id": "txn-log-unsub",
                "unsubscribe_request": [{
                    "reference_id": "ref-log-unsub",
                    "timestamp": "2026-11-11T00:00:00Z",
                    "subscription_id": NATIONAL_ID,
                }],
            },
        }
        response = self.client.post("/api/api_dci/registry/unsubscribe", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNoRequestContentLogged()

    def test_notify_carrying_a_person_record(self):
        notify_item = {
            "reference_id": "ref-log-notify",
            "timestamp": "2026-11-11T00:00:00Z",
            "subscription_id": "sub-log-notify",
            "data": {
                "reg_type": "SOCIAL_REGISTRY",
                "reg_record_type": "Person",
                "event_type": "UPDATE",
                "reg_record": {"identifier": NATIONAL_ID, "family_name": FAMILY_NAME},
            },
        }
        payload = {
            "signature": SIGNATURE,
            "header": header(action="notify"),
            "message": {"transaction_id": "txn-log-notify", "notify_request": [notify_item]},
        }
        response = self.client.post("/api/api_dci/registry/notify", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        del payload["message"]["notify_request"][0]["timestamp"]
        response = self.client.post("/api/api_dci/registry/notify", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNoRequestContentLogged()

    def test_txn_status_by_national_id(self):
        payload = {
            "signature": SIGNATURE,
            "header": header(action="txn-status"),
            "message": {
                "transaction_id": "txn-log-status",
                "txnstatus_request": {
                    "reference_id": "ref-log-status",
                    "txn_type": "search",
                    "attribute_type": "NIN",
                    "attribute_value": NATIONAL_ID,
                },
            },
        }
        response = self.client.post("/api/api_dci/registry/txn/status", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        del payload["message"]["txnstatus_request"]["txn_type"]
        payload["message"]["txnstatus_request"]["attribute_type"] = FAMILY_NAME * 4
        response = self.client.post("/api/api_dci/registry/txn/status", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNoRequestContentLogged()


class ProviderSearchLogsNoRequestContentTestCase(LogCaptureMixin, SimpleTestCase):
    """A record provider whose error repeats the query, on sync and async search."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(record_providers._providers, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        register_record_provider(
            "Beneficiary", "api_dci.tests.record_provider_fixtures.ProviderEchoingQueryInError"
        )
        self.factory = APIRequestFactory()
        self.payload = search_payload(
            "idtype-value", {"type": "NIN", "value": NATIONAL_ID, "family_name": FAMILY_NAME},
            reg_record_type="Beneficiary", sender_uri=CALLBACK_URI,
        )

    def _post(self, view, url):
        request = self.factory.post(url, self.payload, format="json")
        consumer = mock.Mock(is_authenticated=True, has_perm=lambda right: right == PROVIDER_RIGHT)
        force_authenticate(request, user=consumer)
        return view(request)

    def test_sync_search(self):
        response = self._post(sync_search, SYNC_URL)
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertTrue(self.logs.lines)
        self.assertNoRequestContentLogged()

    def test_async_search(self):
        from api_dci.tasks import BackgroundSearchTask

        with mock.patch.object(BackgroundSearchTask, "start", BackgroundSearchTask.run), \
                mock.patch("api_dci.tasks.connection"), \
                mock.patch("api_dci.tasks.requests.post") as post:
            response = self._post(async_search, ASYNC_URL)
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        post.assert_not_called()
        self.assertTrue(self.logs.lines)
        self.assertNoRequestContentLogged()


class SubscriptionDeliveryLogsNoRecordContentTestCase(LogCaptureMixin, SimpleTestCase):
    """Matching a subscription filter and delivering a notification."""

    def _subscription(self, filter_criteria=None, sender_uri=CALLBACK_URI):
        return mock.Mock(subscription_code="sub-log-001", filter_criteria=filter_criteria,
                         sender_uri=sender_uri, callback_headers=None)

    def test_filters_holding_a_national_id(self):
        from api_dci.subscriptions.subscription_filter import DCISubscriptionFilter

        subscription_filter = DCISubscriptionFilter(event_type="UPDATE")
        record = {"identifiers": [{"id_type": "NIN", "id_value": NATIONAL_ID}]}
        for filter_criteria in (
            {"national_id": NATIONAL_ID, "family_name": FAMILY_NAME},
            {"expression": f"$.identifiers[?(@.id_value=='{NATIONAL_ID}')]"},
            {"predicate": {"attribute_name": "family_name", "attribute_value": FAMILY_NAME}},
        ):
            with self.subTest(filter_criteria=next(iter(filter_criteria))):
                self.assertTrue(subscription_filter._matches_filter_criteria(
                    self._subscription(filter_criteria), record))
        self.assertTrue(self.logs.lines)
        self.assertNoRequestContentLogged()

    def test_subscriber_answers_an_error_repeating_the_record(self):
        from api_dci.subscriptions.notification_client import DCINotificationClient

        response = mock.MagicMock(status=422)
        response.json = mock.AsyncMock(
            return_value={"error": f"duplicate {NATIONAL_ID} {FAMILY_NAME}"})
        session = mock.MagicMock()
        session.post.return_value.__aenter__ = mock.AsyncMock(return_value=response)
        session.post.return_value.__aexit__ = mock.AsyncMock(return_value=False)
        callback_uri = f"{CALLBACK_URI}?token={SIGNATURE}"
        result = asyncio.run(DCINotificationClient()._send_notification_async(
            b"{}", self._subscription(sender_uri=callback_uri), session))
        self.assertFalse(result.notification_success)
        self.assertTrue(self.logs.lines)
        self.assertNoRequestContentLogged()

    def test_delivery_failure_whose_message_repeats_the_callback_uri(self):
        from api_dci.subscriptions.notification_client import DCINotificationClient

        session = mock.MagicMock()
        session.post.side_effect = OSError(f"cannot reach {CALLBACK_URI}?token={SIGNATURE}")
        result = asyncio.run(DCINotificationClient()._send_notification_async(
            b"{}", self._subscription(), session))
        self.assertFalse(result.notification_success)
        self.assertTrue(self.logs.lines)
        self.assertNoRequestContentLogged()
