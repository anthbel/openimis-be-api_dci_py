"""
Tests for record providers: registry, startup resolution, per-provider permission
and dispatch of sync and async search on reg_record_type.
"""
from types import SimpleNamespace
from unittest import mock

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITestCase, force_authenticate

from api_dci import record_providers
from api_dci.permissions import DCISearchPermissions, RecordProviderPermission
from api_dci.record_providers import (
    MAX_PAGE_SIZE,
    PERSON_RECORD_TYPES,
    get_record_provider,
    load_record_providers,
    register_record_provider,
)
from api_dci.tests.record_provider_fixtures import (
    HOUSEHOLDS,
    PROVIDER_RIGHT,
    FakeHouseholdProvider,
)
from api_dci.views.person_viewset import async_search, sync_search

FIXTURES = "api_dci.tests.record_provider_fixtures"
PROVIDER_PATH = f"{FIXTURES}.FakeHouseholdProvider"
PERSON_QUERY_RIGHT = "individual.gql_query_individuals_perms"
SYNC_URL = "/api/api_dci/registry/sync/search"
ASYNC_URL = "/api/api_dci/registry/async/search"
_ABSENT = object()


def search_payload(reg_record_type=_ABSENT, cursor=None, page_size=None, query=None,
                   extra_items=()):
    criteria = {
        "query_type": "expression",
        "query": query if query is not None else {"type": "openimis:handoff", "value": {}},
    }
    if reg_record_type is not _ABSENT:
        criteria["reg_record_type"] = reg_record_type
    if page_size is not None:
        criteria["pagination"] = {"page_size": page_size}
    header = {
        "version": "1.0.0",
        "message_id": "msg-rp-001",
        "message_ts": "2026-11-10T00:00:00Z",
        "action": "search",
        "sender_id": "programme-mis",
        "sender_uri": "https://mis.example.org/registry/on-search",
        "receiver_id": "registry",
        "total_count": 1,
    }
    if cursor is not None:
        header["meta"] = {"cursor": cursor}
    items = [{
        "reference_id": "ref-rp-001",
        "timestamp": "2026-11-10T00:00:00Z",
        "search_criteria": criteria,
    }]
    items.extend(extra_items)
    return {
        "signature": "",
        "header": header,
        "message": {"transaction_id": "txn-rp-001", "search_request": items},
    }


def user_holding(*rights):
    return SimpleNamespace(is_authenticated=True, has_perm=lambda perm: perm in rights)


class ProviderRegistryIsolation:
    """Gives each test an empty provider registry and a fresh call log."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(record_providers._providers, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        FakeHouseholdProvider.calls = []


class RegisterRecordProviderTestCase(ProviderRegistryIsolation, SimpleTestCase):

    def test_registered_path_resolves_to_a_provider_instance(self):
        register_record_provider("Household", PROVIDER_PATH)
        provider = get_record_provider("Household")
        self.assertIsInstance(provider, FakeHouseholdProvider)
        self.assertEqual(provider.required_right(), PROVIDER_RIGHT)

    def test_unregistered_type_has_no_provider(self):
        self.assertIsNone(get_record_provider("Household"))

    def test_unresolvable_path_is_refused(self):
        with self.assertRaises(ImproperlyConfigured):
            register_record_provider("Household", f"{FIXTURES}.NoSuchProvider")
        with self.assertRaises(ImproperlyConfigured):
            register_record_provider("Household", "no_such_module.Provider")
        self.assertIsNone(get_record_provider("Household"))

    def test_object_without_the_protocol_is_refused(self):
        for name in ("ProviderWithoutSearch", "NOT_A_PROVIDER"):
            with self.subTest(name=name), self.assertRaises(ImproperlyConfigured):
                register_record_provider("Household", f"{FIXTURES}.{name}")

    def test_person_record_types_cannot_be_taken(self):
        for record_type in sorted(PERSON_RECORD_TYPES):
            with self.subTest(record_type=record_type), self.assertRaises(ImproperlyConfigured):
                register_record_provider(record_type, PROVIDER_PATH)

    def test_type_cannot_be_rebound_to_another_path(self):
        register_record_provider("Household", PROVIDER_PATH)
        register_record_provider("Household", PROVIDER_PATH)
        with self.assertRaises(ImproperlyConfigured):
            register_record_provider("Household", f"{FIXTURES}.ProviderWithoutSearch")

    def test_load_registers_every_configured_type(self):
        load_record_providers({"Household": PROVIDER_PATH, "EnrolmentOutcome": PROVIDER_PATH})
        self.assertIsNotNone(get_record_provider("Household"))
        self.assertIsNotNone(get_record_provider("EnrolmentOutcome"))


class RecordProviderStartupTestCase(ProviderRegistryIsolation, SimpleTestCase):
    """AppConfig.ready resolves the configured record_providers and fails on a bad path."""

    def _ready_with(self, record_providers_cfg):
        from api_dci.apps import ApiDciConfig
        app_config = apps.get_app_config("api_dci")
        cfg = {"record_providers": record_providers_cfg}
        with mock.patch("core.models.ModuleConfiguration.get_or_default", return_value=cfg), \
                mock.patch("api_dci.signals.bind_service_signals"), \
                mock.patch.object(ApiDciConfig, "record_providers", {}):
            app_config.ready()

    def test_ready_registers_configured_providers(self):
        self._ready_with({"Household": PROVIDER_PATH})
        self.assertIsInstance(get_record_provider("Household"), FakeHouseholdProvider)

    def test_ready_fails_on_an_unresolvable_path(self):
        with self.assertRaises(ImproperlyConfigured):
            self._ready_with({"Household": f"{FIXTURES}.NoSuchProvider"})

    def test_installed_configuration_resolves(self):
        from api_dci.apps import ApiDciConfig, DEFAULT_CFG
        self.assertIn("record_providers", DEFAULT_CFG)
        load_record_providers(ApiDciConfig.record_providers)


class RecordProviderPermissionTestCase(SimpleTestCase):
    """The provider gate asks for the provider's right only and fails closed."""

    def _allows(self, provider, user, method="POST"):
        request = SimpleNamespace(method=method, user=user)
        return RecordProviderPermission(provider).has_permission(request, None)

    def test_user_with_the_provider_right_is_allowed(self):
        checked = []
        user = SimpleNamespace(
            is_authenticated=True, has_perm=lambda perm: checked.append(perm) or True
        )
        self.assertTrue(self._allows(FakeHouseholdProvider(), user))
        self.assertEqual(checked, [PROVIDER_RIGHT])

    def test_user_without_the_provider_right_is_denied(self):
        self.assertFalse(self._allows(FakeHouseholdProvider(), user_holding(PERSON_QUERY_RIGHT)))

    def test_unusable_required_right_denies(self):
        def raising():
            raise RuntimeError("rights unavailable")

        for right in (lambda: "", lambda: "   ", lambda: None, lambda: ["990001"], raising):
            provider = SimpleNamespace(required_right=right)
            with self.subTest(right=right):
                user = SimpleNamespace(is_authenticated=True, has_perm=lambda perm: True)
                self.assertFalse(self._allows(provider, user))

    def test_user_that_cannot_answer_is_denied(self):
        provider = FakeHouseholdProvider()
        self.assertFalse(self._allows(provider, SimpleNamespace(is_authenticated=True)))
        self.assertFalse(self._allows(provider, SimpleNamespace(has_perm=lambda perm: True)))
        self.assertFalse(self._allows(
            provider, SimpleNamespace(is_authenticated=False, has_perm=lambda perm: True)
        ))

    def test_only_post_is_allowed(self):
        user = SimpleNamespace(is_authenticated=True, has_perm=lambda perm: True)
        self.assertFalse(self._allows(FakeHouseholdProvider(), user, method="GET"))


class DCISearchPermissionsTestCase(ProviderRegistryIsolation, SimpleTestCase):
    """The search gate picks the provider or the Person check from reg_record_type."""

    def setUp(self):
        super().setUp()
        register_record_provider("Household", PROVIDER_PATH)

    def _checked_rights(self, payload):
        checked = []
        user = SimpleNamespace(
            is_authenticated=True, has_perm=lambda perm: checked.append(perm) or True
        )
        request = SimpleNamespace(method="POST", user=user, data=payload)
        allowed = DCISearchPermissions().has_permission(request, None)
        return allowed, checked

    def test_absent_type_is_a_person_search(self):
        self.assertEqual(self._checked_rights(search_payload()), (True, [PERSON_QUERY_RIGHT]))

    def test_person_type_is_a_person_search(self):
        self.assertEqual(
            self._checked_rights(search_payload("Person")), (True, [PERSON_QUERY_RIGHT])
        )

    def test_provider_type_checks_the_provider_right_only(self):
        self.assertEqual(
            self._checked_rights(search_payload("Household")), (True, [PROVIDER_RIGHT])
        )

    def test_unregistered_type_is_a_person_search(self):
        for record_type in ("SRPerson", "Beneficiary", "Individual", "", None, 7,
                            ["Household"]):
            with self.subTest(record_type=record_type):
                self.assertEqual(
                    self._checked_rights(search_payload(record_type)),
                    (True, [PERSON_QUERY_RIGHT]),
                )

    def test_mixed_types_in_one_message_are_denied(self):
        person_item = search_payload("Person")["message"]["search_request"][0]
        payload = search_payload("Household", extra_items=[person_item])
        self.assertEqual(self._checked_rights(payload), (False, []))

    def test_provider_type_mixed_with_an_unregistered_type_is_denied(self):
        other_item = search_payload("SRPerson")["message"]["search_request"][0]
        payload = search_payload("Household", extra_items=[other_item])
        self.assertEqual(self._checked_rights(payload), (False, []))

    def test_unreadable_body_is_denied(self):
        class Unreadable:
            method = "POST"
            user = SimpleNamespace(is_authenticated=True, has_perm=lambda perm: True)

            @property
            def data(self):
                raise ValueError("malformed")

        self.assertFalse(DCISearchPermissions().has_permission(Unreadable(), None))


class ProviderSyncSearchTestCase(ProviderRegistryIsolation, SimpleTestCase):
    """sync_search serves provider types from the provider, never from Individual."""

    def setUp(self):
        super().setUp()
        register_record_provider("Household", PROVIDER_PATH)
        self.factory = APIRequestFactory()
        self.consumer = user_holding(PROVIDER_RIGHT)

    def _post(self, payload, user=None, view=sync_search, url=SYNC_URL):
        request = self.factory.post(url, payload, format="json")
        force_authenticate(request, user=user or self.consumer)
        return view(request)

    def test_consumer_is_refused_a_person_search(self):
        response = self._post(search_payload())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_consumer_gets_a_page_of_its_provider_type(self):
        response = self._post(search_payload("Household", page_size=2))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["header"]["meta"], {"next_cursor": "2"})
        item = response.data["message"]["search_response"][0]
        self.assertEqual(item["status"], "succ")
        self.assertEqual(item["reference_id"], "ref-rp-001")
        self.assertEqual(item["data"]["reg_record_type"], "Household")
        self.assertEqual(item["data"]["reg_records"], HOUSEHOLDS[:2])
        self.assertNotIn("pagination", item)

    def test_provider_receives_user_query_cursor_and_limit(self):
        query = {"type": "openimis:handoff", "value": {"consumer": "mis-1"}}
        self._post(search_payload("Household", cursor="2", page_size=2, query=query))
        self.assertEqual(FakeHouseholdProvider.calls, [
            {"user": self.consumer, "query": query, "cursor": "2", "limit": 2},
        ])

    def test_last_page_has_no_next_cursor(self):
        response = self._post(search_payload("Household", cursor="4", page_size=2))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["header"]["meta"], {"next_cursor": None})
        records = response.data["message"]["search_response"][0]["data"]["reg_records"]
        self.assertEqual(records, HOUSEHOLDS[4:])

    def test_missing_page_size_uses_the_maximum(self):
        self._post(search_payload("Household"))
        self.assertEqual(FakeHouseholdProvider.calls[0]["limit"], MAX_PAGE_SIZE)

    def test_invalid_page_size_is_rejected(self):
        for page_size in (0, -1, MAX_PAGE_SIZE + 1, "10", 2.5, True):
            with self.subTest(page_size=page_size):
                response = self._post(search_payload("Household", page_size=page_size))
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(FakeHouseholdProvider.calls, [])

    def test_non_string_cursor_is_rejected(self):
        response = self._post(search_payload("Household", cursor=5))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(FakeHouseholdProvider.calls, [])

    def test_unregistered_type_is_searched_as_person(self):
        person_user = user_holding(PROVIDER_RIGHT, PERSON_QUERY_RIGHT)
        statuses = {}
        for record_type in (_ABSENT, "SRPerson"):
            with self.subTest(record_type=record_type), \
                    mock.patch("individual.models.Individual.objects") as individuals:
                response = self._post(search_payload(record_type), user=person_user)
                statuses[record_type] = response.status_code
                individuals.filter.assert_called()
        self.assertEqual(statuses["SRPerson"], statuses[_ABSENT])
        self.assertEqual(FakeHouseholdProvider.calls, [])


class ProviderAsyncSearchTestCase(ProviderRegistryIsolation, SimpleTestCase):
    """async_search hands provider types to the background task with the provider."""

    def setUp(self):
        super().setUp()
        register_record_provider("Household", PROVIDER_PATH)
        self.factory = APIRequestFactory()
        self.consumer = user_holding(PROVIDER_RIGHT)

    def _post(self, payload, user=None):
        request = self.factory.post(ASYNC_URL, payload, format="json")
        force_authenticate(request, user=user or self.consumer)
        return async_search(request)

    def test_consumer_is_refused_a_person_async_search(self):
        with mock.patch("api_dci.tasks.BackgroundSearchTask") as task:
            response = self._post(search_payload())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        task.assert_not_called()

    def test_unknown_type_is_refused(self):
        with mock.patch("api_dci.tasks.BackgroundSearchTask") as task:
            response = self._post(search_payload("Unknown"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        task.assert_not_called()

    def test_unregistered_type_is_queued_as_a_person_search(self):
        person_user = user_holding(PERSON_QUERY_RIGHT)
        with mock.patch("api_dci.tasks.BackgroundSearchTask") as task:
            response = self._post(search_payload("SRPerson"), user=person_user)
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        task.assert_called_once()
        self.assertIsNone(task.call_args.kwargs["provider"])

    def test_provider_type_is_queued_with_its_provider_and_user(self):
        with mock.patch("api_dci.tasks.BackgroundSearchTask") as task:
            response = self._post(search_payload("Household", page_size=2))
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        kwargs = task.call_args.kwargs
        self.assertIsInstance(kwargs["provider"], FakeHouseholdProvider)
        self.assertIs(kwargs["user"], self.consumer)
        task.return_value.start.assert_called_once_with()

    def test_provider_callback_must_be_https(self):
        for sender_uri in (None, "", "http://mis.example.org/on-search", "mis.example.org"):
            payload = search_payload("Household")
            if sender_uri is None:
                payload["header"].pop("sender_uri")
            else:
                payload["header"]["sender_uri"] = sender_uri
            with self.subTest(sender_uri=sender_uri), \
                    mock.patch("api_dci.tasks.BackgroundSearchTask") as task:
                response = self._post(payload)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                task.assert_not_called()


class PersonSearchThroughSearchGateTestCase(ProviderRegistryIsolation, SimpleTestCase):
    """A holder of the Person query right passes the gate for Person, not for providers."""

    def setUp(self):
        super().setUp()
        register_record_provider("Household", PROVIDER_PATH)
        self.factory = APIRequestFactory()
        self.person_reader = user_holding(PERSON_QUERY_RIGHT)

    def _post(self, view, url, payload):
        request = self.factory.post(url, payload, format="json")
        force_authenticate(request, user=self.person_reader)
        return view(request)

    def test_person_reader_passes_the_gate_on_sync_and_async_search(self):
        # An empty body fails validation, so 400 means the permission check passed.
        for view, url in ((sync_search, SYNC_URL), (async_search, ASYNC_URL)):
            with self.subTest(url=url):
                response = self._post(view, url, {})
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_person_reader_is_refused_a_provider_type(self):
        for view, url in ((sync_search, SYNC_URL), (async_search, ASYNC_URL)):
            with self.subTest(url=url):
                response = self._post(view, url, search_payload("Household"))
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(FakeHouseholdProvider.calls, [])


class ProviderBackgroundSearchTestCase(ProviderRegistryIsolation, SimpleTestCase):
    """The background task posts the provider page and never queries Individual."""

    def test_provider_task_posts_the_provider_page(self):
        from api_dci.tasks import BackgroundSearchTask

        payload = search_payload("Household", cursor="1", page_size=2)
        consumer = user_holding(PROVIDER_RIGHT)
        task = BackgroundSearchTask(
            request_data=payload,
            search_requests=payload["message"]["search_request"],
            provider=FakeHouseholdProvider(),
            user=consumer,
        )
        with mock.patch("api_dci.tasks.requests.post") as post, \
                mock.patch("individual.models.Individual.objects") as individuals:
            task.run()
        individuals.filter.assert_not_called()
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "https://mis.example.org/registry/on-search")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["header"]["meta"], {"next_cursor": "3"})
        item = body["message"]["search_response"][0]
        self.assertEqual(item["data"]["reg_records"], HOUSEHOLDS[1:3])
        self.assertEqual(FakeHouseholdProvider.calls[0]["user"], consumer)


class ConsumerServiceAccountTestCase(ProviderRegistryIsolation, APITestCase):
    """A real consumer account holding only the provider right."""

    def setUp(self):
        super().setUp()
        register_record_provider("Household", PROVIDER_PATH)
        from core.test_helpers import (
            _sync_role_rights,
            create_test_interactive_user,
            create_test_role,
        )
        role = create_test_role(name="DCI handoff consumer")
        _sync_role_rights(role, [int(PROVIDER_RIGHT)])
        self.consumer = create_test_interactive_user(
            username="dci_handoff_consumer", roles=[role.id]
        )
        self.client.force_authenticate(user=self.consumer)

    def test_consumer_gets_403_on_person_and_200_on_its_provider_type(self):
        self.assertFalse(self.consumer.has_perm(PERSON_QUERY_RIGHT))
        person = self.client.post(SYNC_URL, search_payload(), format="json")
        self.assertEqual(person.status_code, status.HTTP_403_FORBIDDEN)
        household = self.client.post(SYNC_URL, search_payload("Household", page_size=3),
                                     format="json")
        self.assertEqual(household.status_code, status.HTTP_200_OK)
        records = household.data["message"]["search_response"][0]["data"]["reg_records"]
        self.assertEqual(records, HOUSEHOLDS[:3])

    def test_consumer_gets_403_on_an_unknown_type(self):
        response = self.client.post(SYNC_URL, search_payload("Unknown"), format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class ProviderErrorIsNotEchoedTestCase(ProviderRegistryIsolation, SimpleTestCase):
    """A ValueError raised inside a provider is a server error, not echoed to the caller."""

    def setUp(self):
        super().setUp()
        register_record_provider("Household", f"{FIXTURES}.ProviderRaisingValueError")
        self.factory = APIRequestFactory()

    def test_provider_value_error_gives_a_generic_500(self):
        request = self.factory.post(SYNC_URL, search_payload("Household"), format="json")
        force_authenticate(request, user=user_holding(PROVIDER_RIGHT))
        response = sync_search(request)
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertNotIn("hh_secret", str(response.data))

    def test_invalid_page_size_is_still_a_400(self):
        request = self.factory.post(SYNC_URL, search_payload("Household", page_size=0), format="json")
        force_authenticate(request, user=user_holding(PROVIDER_RIGHT))
        response = sync_search(request)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class CallbackRedirectTestCase(SimpleTestCase):
    """The async callback is posted to the sender's URL only, never to a redirect target."""

    def test_callback_does_not_follow_redirects(self):
        from api_dci.tasks import BackgroundSearchTask

        with mock.patch("api_dci.tasks.requests.post") as post:
            BackgroundSearchTask._post_callback("https://mis.example.org/registry/on-search", {})
        self.assertIs(post.call_args.kwargs.get("allow_redirects"), False)
