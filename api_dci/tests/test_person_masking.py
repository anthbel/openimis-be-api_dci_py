"""The DCI Person responses apply individual's person mask to a caller without the masked-data right."""
import re
from unittest import mock

from django.core.cache import caches
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from core.test_helpers import create_right_only_user
from individual.apps import IndividualConfig
from individual.data_masking import IndividualMask, may_see_masked
from individual.models import Individual
from location.models import Location

from api_dci.apps import ApiDciConfig
from api_dci.tasks import BackgroundSearchTask

MASKED = "***************"
# The DCI person read and update rights.
GATE = ["gql_dci_person_search_perms", "gql_dci_person_update_perms"]
DISTRICT = "DCIM-D"
DETAIL_URL = "/api/api_dci/registry/person/openimis:individual:{}"
SYNC_URL = "/api/api_dci/registry/sync/search"
UPDATE_URL = "/api/api_dci/registry/person/openimis:individual:{}/update"


def search_request(last_name):
    return {
        "signature": "",
        "header": {
            "version": "1.0.0", "message_id": "mask-msg", "message_ts": "2026-03-24T10:00:00Z",
            "action": "search", "sender_id": "test-sender", "receiver_id": "openimis",
            "sender_uri": "https://mis.example.org/on-search",
        },
        "message": {
            "transaction_id": "txn-mask",
            "search_request": [{
                "reference_id": "ref-mask", "timestamp": "2026-03-24T10:00:00Z",
                "search_criteria": {"query_type": "sync", "query": {"@type": "Person", "lastName": last_name}},
            }],
        },
    }


def records_of(body):
    """The Person records of a search response, whatever registry format it takes."""
    response = body["message"]["search_response"][0]
    if "data" in response:
        return response["data"]["reg_records"]
    return response["registry_data"]["data"]


def update_request(given_name):
    return {
        "signature": "",
        "header": {
            "version": "1.0.0", "message_id": "mask-upd", "message_ts": "2026-03-24T10:00:00Z",
            "action": "update", "sender_id": "test-sender", "receiver_id": "openimis",
        },
        "message": {"transaction_id": "txn-mask-upd", "person": {"name": {"given_name": given_name}}},
    }


def district_location():
    """A village under the district DISTRICT, the location of the masked person."""
    region = Location.objects.create(code="DCIM-R", name="DCIM-R", type="R", validity_from="2019-06-01",
                                     audit_user_id=-1)
    district = Location.objects.create(code=DISTRICT, name=DISTRICT, type="D", parent=region,
                                       validity_from="2019-06-01", audit_user_id=-1)
    municipality = Location.objects.create(code="DCIM-W", name="DCIM-W", type="W", parent=district,
                                           validity_from="2019-06-01", audit_user_id=-1)
    village = Location.objects.create(code="DCIM-V", name="DCIM-V", type="V", parent=municipality,
                                      validity_from="2019-06-01", audit_user_id=-1)
    caches["location"].clear()
    caches["default"].clear()
    return village


class PersonMaskingTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        village = district_location()
        cls.masked_reader = create_right_only_user("dci_mask_masked", GATE, [DISTRICT])
        cls.unmasked_reader = create_right_only_user(
            "dci_mask_unmasked", GATE + ["gql_query_enable_viewing_masked_data_perms"], [DISTRICT])
        cls.role_reader = create_right_only_user("dci_mask_role", ["gql_individual_search_perms"], [DISTRICT])
        cls.person = Individual(
            first_name="Maskfirst", last_name="Clearlast", dob="1990-01-15", location=village,
            json_ext={"phone": "+25779000001", "email": "clear@example.org"},
        )
        cls.person.save(username=cls.masked_reader.username)

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(IndividualMask._sources, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        IndividualMask.register_fields("test", lambda: ["first_name", "dob", "json_ext.phone"])
        own = mock.patch.object(IndividualConfig, "individual_masking_enabled", False)
        own.start()
        self.addCleanup(own.stop)

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def assert_masked(self, person):
        self.assertEqual(person["name"]["given_name"], MASKED)
        self.assertEqual(person["name"]["family_name"], "Clearlast")
        self.assertEqual(person["birth_date"], MASKED)
        self.assertEqual(person["phone_number"], [MASKED])
        self.assertEqual(person["email"], ["clear@example.org"])

    def assert_clear(self, person):
        self.assertEqual(person["name"]["given_name"], "Maskfirst")
        self.assertEqual(person["birth_date"], "1990-01-15T00:00:00Z")
        self.assertEqual(person["phone_number"], ["+25779000001"])

    def test_the_person_search_right_of_individual_does_not_pass_the_gate(self):
        self.assertTrue(self.role_reader.has_perms(IndividualConfig.gql_individual_search_perms))
        response = self.client_for(self.role_reader).get(DETAIL_URL.format(self.person.id))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_the_masked_reader_passes_the_gate_without_the_masked_data_right(self):
        self.assertTrue(self.masked_reader.has_perms(ApiDciConfig.gql_dci_person_search_perms))
        self.assertFalse(may_see_masked(self.masked_reader))
        self.assertTrue(may_see_masked(self.unmasked_reader))

    def test_person_detail_is_masked_without_the_masked_data_right(self):
        response = self.client_for(self.masked_reader).get(DETAIL_URL.format(self.person.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assert_masked(response.json())

    def test_person_detail_is_clear_with_the_masked_data_right(self):
        response = self.client_for(self.unmasked_reader).get(DETAIL_URL.format(self.person.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assert_clear(response.json())

    def test_the_mask_does_not_change_the_stored_person(self):
        self.client_for(self.masked_reader).get(DETAIL_URL.format(self.person.id))
        stored = Individual.objects.get(id=self.person.id)
        self.assertEqual(stored.first_name, "Maskfirst")
        self.assertEqual(stored.json_ext["phone"], "+25779000001")

    def test_sync_search_is_masked_without_the_masked_data_right(self):
        response = self.client_for(self.masked_reader).post(SYNC_URL, data=search_request("Clearlast"), format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        records = records_of(response.json())
        self.assertEqual(len(records), 1)
        self.assert_masked(records[0])

    def test_sync_search_is_clear_with_the_masked_data_right(self):
        response = self.client_for(self.unmasked_reader).post(
            SYNC_URL, data=search_request("Clearlast"), format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assert_clear(records_of(response.json())[0])

    def _async_records(self, user):
        request = search_request("Clearlast")
        task = BackgroundSearchTask(
            request_data=request, search_requests=request["message"]["search_request"], user=user)
        with mock.patch.object(BackgroundSearchTask, "_post_callback") as post:
            task._search_and_post()
        post.assert_called_once()
        return records_of(post.call_args.args[1])

    def test_async_search_callback_is_masked_without_the_masked_data_right(self):
        records = self._async_records(self.masked_reader)
        self.assertEqual(len(records), 1)
        self.assert_masked(records[0])

    def test_async_search_callback_is_clear_with_the_masked_data_right(self):
        self.assert_clear(self._async_records(self.unmasked_reader)[0])

    def test_async_search_callback_holds_no_person_for_a_task_without_a_user(self):
        self.assertEqual(self._async_records(None), [])

    def test_an_update_sending_the_mask_back_is_refused_and_writes_nothing(self):
        response = self.client_for(self.masked_reader).put(
            UPDATE_URL.format(self.person.id), data=update_request(MASKED), format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertIn("first_name", response.json()["message"]["error_message"])
        self.assertEqual(Individual.objects.get(id=self.person.id).first_name, "Maskfirst")

    def test_an_update_with_a_new_value_is_written_by_the_caller(self):
        response = self.client_for(self.masked_reader).put(
            UPDATE_URL.format(self.person.id), data=update_request("Newfirst"), format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        stored = Individual.objects.get(id=self.person.id)
        self.assertEqual(stored.first_name, "Newfirst")
        self.assertEqual(stored.user_updated, self.masked_reader)

    def test_a_failed_update_gives_an_error_id_and_no_exception_text(self):
        with self.assertLogs("api_dci.views.person_create_view", level="ERROR") as logs:
            response = self.client_for(self.masked_reader).put(
                UPDATE_URL.format(self.person.id), data=update_request(None), format="json")
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR, response.content)
        body = response.content.decode()
        for leaked in ("1990-01-15", "+25779000001", "Clearlast", "Failing row", "null value"):
            self.assertNotIn(leaked, body)
        error_id = re.search(r"error id ([0-9a-f]{32})", response.json()["message"]["error_message"]).group(1)
        self.assertTrue(any(error_id in line for line in logs.output), logs.output)
        self.assertFalse(any("1990-01-15" in line or "+25779000001" in line for line in logs.output), logs.output)
