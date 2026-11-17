"""DCI person access: the DCI person rights, the caller's locations, masked filters and the create audit user.

The callers are role users holding the DCI rights through a role, with or without the masked-data right
(900101), and each limited to one district.
"""
from unittest import mock

from django.core.cache import caches
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from core.test_helpers import _sync_role_rights, create_test_interactive_user, create_test_role
from individual.apps import IndividualConfig
from individual.data_masking import IndividualMask
from individual.models import Group, GroupIndividual, Individual
from location.models import Location
from location.test_helpers import assign_user_districts

from api_dci.apps import ApiDciConfig
from api_dci.tasks import BackgroundSearchTask

PERSON_QUERY = "195701"
PERSON_CREATE = "195702"
PERSON_UPDATE = "195703"
MASKED_DATA = "900101"
INDIVIDUAL_QUERY = "159001"
MASKED = "***************"

DETAIL_URL = "/api/api_dci/registry/person/openimis:individual:{}"
CREATE_URL = "/api/api_dci/registry/person"
SYNC_URL = "/api/api_dci/registry/sync/search"
ASYNC_URL = "/api/api_dci/registry/async/search"


def clear_location_cache():
    caches["location"].clear()
    caches["default"].clear()


def make_location(code, loc_type, parent=None):
    return Location.objects.create(
        code=code, name=code, type=loc_type, parent=parent, validity_from="2019-06-01", audit_user_id=-1)


def role_user(username, right_ids, district_codes=()):
    role = create_test_role(name=f"DCI access {username}")
    _sync_role_rights(role, [int(right) for right in right_ids])
    user = create_test_interactive_user(username=username, roles=[role.id])
    if district_codes:
        assign_user_districts(user, district_codes)
    clear_location_cache()
    return type(user).objects.get(pk=user.pk)


def header(action, **extra):
    return {
        "version": "1.0.0", "message_id": f"access-{action}", "message_ts": "2026-11-17T10:00:00Z",
        "action": action, "sender_id": "programme-mis", "receiver_id": "registry", **extra,
    }


def search_request(criteria):
    return {
        "signature": "",
        "header": header("search", sender_uri="https://mis.example.org/on-search"),
        "message": {
            "transaction_id": "txn-access",
            "search_request": [{
                "reference_id": "ref-access", "timestamp": "2026-11-17T10:00:00Z", "search_criteria": criteria,
            }],
        },
    }


def by_fields(**fields):
    return search_request({"query_type": "sync", "query": {"@type": "Person", **fields}})


def by_predicate(attribute_name, value, operator="eq"):
    return search_request({"query_type": "predicate", "query": [{
        "seq_num": 1,
        "expression1": {"attribute_name": attribute_name, "operator": operator, "attribute_value": value},
    }]})


def records_of(body):
    response = body["message"]["search_response"][0]
    if "data" in response:
        return response["data"]["reg_records"]
    return response["registry_data"]["data"]


def family_names(body):
    return sorted(record["name"]["family_name"] for record in records_of(body))


@override_settings(ROW_SECURITY=True)
class DCIPersonAccessTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        clear_location_cache()
        region = make_location("DCIA-R", "R")
        cls.district = make_location("DCIA-D", "D", region)
        cls.other_district = make_location("DCIA-D2", "D", region)
        cls.village = make_location("DCIA-V", "V", make_location("DCIA-W", "W", cls.district))
        cls.other_village = make_location("DCIA-V2", "V", make_location("DCIA-W2", "W", cls.other_district))
        clear_location_cache()
        cls.admin = create_test_interactive_user(username="dci_access_admin")
        cls.reader = role_user("dci_access_reader", [PERSON_QUERY], ["DCIA-D"])
        cls.unmasked_reader = role_user("dci_access_unmasked", [PERSON_QUERY, MASKED_DATA], ["DCIA-D"])
        cls.individual_reader = role_user("dci_access_individual", [INDIVIDUAL_QUERY], ["DCIA-D"])
        cls.creator = role_user("dci_access_creator", [PERSON_CREATE], ["DCIA-D"])
        cls.near = cls.person("Nearfirst", "Accessnear", cls.village)
        cls.far = cls.person("Farfirst", "Accessfar", cls.other_village)

    @classmethod
    def person(cls, first_name, last_name, location):
        person = Individual(first_name=first_name, last_name=last_name, dob="1990-01-15", location=location,
                            json_ext={"phone": "+25779000001"})
        person.save(user=cls.admin)
        return person

    def setUp(self):
        super().setUp()
        clear_location_cache()
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

    def search(self, user, payload):
        return self.client_for(user).post(SYNC_URL, data=payload, format="json")

    # -- the DCI person rights ------------------------------------------------------------------------------

    def test_the_rights_are_declared_by_the_module(self):
        self.assertEqual(ApiDciConfig.gql_dci_person_search_perms, [PERSON_QUERY])
        self.assertEqual(ApiDciConfig.gql_dci_person_create_perms, [PERSON_CREATE])
        self.assertEqual(ApiDciConfig.gql_dci_person_update_perms, [PERSON_UPDATE])

    def test_a_role_holding_the_person_query_right_reads_a_person(self):
        response = self.client_for(self.reader).get(DETAIL_URL.format(self.near.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.json()["name"]["family_name"], "Accessnear")

    def test_a_role_holding_the_person_query_right_searches(self):
        response = self.search(self.reader, by_fields(lastName="Access"))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)

    def test_the_individual_search_right_alone_does_not_open_the_dci_api(self):
        response = self.client_for(self.individual_reader).get(DETAIL_URL.format(self.near.id))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_superuser_reads_every_person(self):
        superuser = create_test_interactive_user(username="dci_access_super", custom_props={"is_superuser": True})
        response = self.client_for(superuser).get(DETAIL_URL.format(self.far.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        response = self.search(superuser, by_fields(lastName="Access"))
        self.assertEqual(family_names(response.json()), ["Accessfar", "Accessnear"])

    # -- the caller's locations ------------------------------------------------------------------------------

    def test_detail_of_a_person_outside_the_callers_locations_is_not_found(self):
        response = self.client_for(self.reader).get(DETAIL_URL.format(self.far.id))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.content)

    def test_sync_search_returns_only_persons_in_the_callers_locations(self):
        response = self.search(self.reader, by_fields(lastName="Access"))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(family_names(response.json()), ["Accessnear"])

    def test_async_search_returns_only_persons_in_the_callers_locations(self):
        request = by_fields(lastName="Access")
        task = BackgroundSearchTask(
            request_data=request, search_requests=request["message"]["search_request"], user=self.reader)
        with mock.patch.object(BackgroundSearchTask, "_post_callback") as post:
            task._search_and_post()
        post.assert_called_once()
        self.assertEqual(sorted(r["name"]["family_name"] for r in records_of(post.call_args.args[1])),
                         ["Accessnear"])

    def test_a_person_in_two_households_is_read_once(self):
        member = self.person("Twofirst", "Accesstwice", self.village)
        for code in ("DCIA-G1", "DCIA-G2"):
            group = Group(code=code, location=self.village, json_ext={})
            group.save(user=self.admin)
            GroupIndividual(group=group, individual=member, role=GroupIndividual.Role.HEAD).save(user=self.admin)
        response = self.client_for(self.reader).get(DETAIL_URL.format(member.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        response = self.search(self.reader, by_fields(lastName="Accesstwice"))
        self.assertEqual(family_names(response.json()), ["Accesstwice"])

    # -- filters on masked fields ----------------------------------------------------------------------------

    def test_a_filter_on_a_masked_field_is_refused_without_the_masked_data_right(self):
        for payload in (by_fields(firstName="Nearfirst"), by_fields(dob="1990-01-15"),
                        by_predicate("first_name", "Nearfirst"), by_predicate("dob", "1990-01-01", "gt"),
                        by_predicate("json_ext__phone", "+25779000001"),
                        by_predicate("groupindividuals__individual__first_name", "Nearfirst")):
            with self.subTest(payload=payload["message"]["search_request"][0]["search_criteria"]):
                response = self.search(self.reader, payload)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.content)
                self.assertNotIn("Accessnear", response.content.decode())

    def test_an_async_filter_on_a_masked_field_is_refused_before_the_search_is_queued(self):
        with mock.patch("api_dci.tasks.BackgroundSearchTask") as task:
            response = self.client_for(self.reader).post(
                ASYNC_URL, data=by_fields(firstName="Nearfirst"), format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.content)
        task.assert_not_called()

    def test_a_filter_on_a_masked_field_is_served_with_the_masked_data_right(self):
        response = self.search(self.unmasked_reader, by_fields(firstName="Nearfirst"))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(family_names(response.json()), ["Accessnear"])

    def test_a_filter_on_an_unmasked_field_is_served_without_the_masked_data_right(self):
        response = self.search(self.reader, by_fields(lastName="Accessnear"))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        records = records_of(response.json())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["name"]["given_name"], MASKED)

    # -- create ----------------------------------------------------------------------------------------------

    def test_create_saves_the_person_with_the_caller_as_user(self):
        payload = {
            "signature": "",
            "header": header("create"),
            "message": {"transaction_id": "txn-access-create", "person": {
                "name": {"given_name": "Created", "family_name": "Throughdci"},
                "birth_date": "1991-02-03T00:00:00Z",
            }},
        }
        response = self.client_for(self.creator).post(CREATE_URL, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        person = Individual.objects.get(last_name="Throughdci")
        self.assertEqual(person.user_created, self.creator)
        self.assertEqual(person.user_updated, self.creator)
        self.assertEqual(str(person.dob), "1991-02-03")
