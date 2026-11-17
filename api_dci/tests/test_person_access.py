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
from api_dci.tests.utils import allow_callback_host

PERSON_QUERY = "195701"
PERSON_CREATE = "195702"
PERSON_UPDATE = "195703"
MASKED_DATA = "900101"
INDIVIDUAL_QUERY = "159001"
MASKED = "***************"

DETAIL_URL = "/api/api_dci/registry/person/openimis:individual:{}"
CREATE_URL = "/api/api_dci/registry/person"
UPDATE_URL = "/api/api_dci/registry/person/openimis:individual:{}/update"
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
        cls.updater = role_user("dci_access_updater", [PERSON_UPDATE], ["DCIA-D"])
        cls.near = cls.person("Nearfirst", "Accessnear", cls.village)
        cls.far = cls.person("Farfirst", "Accessfar", cls.other_village)

    @classmethod
    def person(cls, first_name, last_name, location):
        person = Individual(first_name=first_name, last_name=last_name, dob="1990-01-15", location=location,
                            json_ext={"phone": "+25779000001", "email": f"{last_name.lower()}@example.org",
                                      "sex": "female"})
        person.save(user=cls.admin)
        return person

    def setUp(self):
        super().setUp()
        allow_callback_host(self)
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
                        by_predicate("phone", "+25779000001"), by_fields(phone="+2577900")):
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

    def create(self, user, **person):
        payload = {
            "signature": "",
            "header": header("create"),
            "message": {"transaction_id": "txn-access-create", "person": {
                "name": {"given_name": "Created", "family_name": "Throughdci"},
                "birth_date": "1991-02-03T00:00:00Z", **person,
            }},
        }
        return self.client_for(user).post(CREATE_URL, data=payload, format="json")

    def test_create_saves_the_person_with_the_caller_as_user(self):
        response = self.create(self.creator, address=[{"type": "residential", "location_code": "DCIA-V"}])
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        person = Individual.objects.get(last_name="Throughdci")
        self.assertEqual(person.user_created, self.creator)
        self.assertEqual(person.user_updated, self.creator)
        self.assertEqual(str(person.dob), "1991-02-03")

    def test_create_places_the_person_at_its_address_location_with_its_contacts_in_json_ext(self):
        response = self.create(
            self.creator, address=[{"type": "residential", "location_code": "DCIA-V"}], sex="female",
            phone_number=["+25779000009"], email=["created@example.org"])
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        person = Individual.objects.get(last_name="Throughdci")
        self.assertEqual(person.location_id, self.village.id)
        self.assertEqual((person.json_ext["sex"], person.json_ext["phone"], person.json_ext["email"]),
                         ("female", "+25779000009", "created@example.org"))
        self.assertNotIn("Created", str(person.json_ext))
        self.assertNotIn("1991-02-03", str(person.json_ext))

    def test_create_and_update_keep_only_the_first_phone_and_email(self):
        response = self.create(
            self.creator, address=[{"type": "residential", "location_code": "DCIA-V"}],
            phone_number=["+25779000009", "+25779000008"], email=["first@example.org", "second@example.org"])
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        person = Individual.objects.get(last_name="Throughdci")
        self.assertEqual((person.json_ext["phone"], person.json_ext["email"]), ("+25779000009", "first@example.org"))
        self.assertEqual(str(person.json_ext).count("+25779000009"), 1)
        self.assertNotIn("+25779000008", str(person.json_ext))
        self.assertNotIn("second@example.org", str(person.json_ext))
        payload = {
            "signature": "",
            "header": header("update"),
            "message": {"transaction_id": "txn-access-update", "person": {
                "name": {"family_name": "Accessnear"}, "phone_number": ["+25779000007", "+25779000006"]}},
        }
        response = self.client_for(self.updater).put(UPDATE_URL.format(self.near.id), data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        stored = Individual.objects.get(id=self.near.id).json_ext
        self.assertEqual(stored["phone"], "+25779000007")
        self.assertEqual(str(stored).count("+25779000007"), 1)
        self.assertNotIn("+25779000006", str(stored))

    def test_create_outside_the_callers_locations_or_without_one_is_refused(self):
        for address in (None, [], [{"type": "residential"}], [{"location_code": "DCIA-V2"}],
                        [{"location_code": "NO-SUCH-CODE"}],
                        [{"location_code": "DCIA-V"}, {"location_code": "DCIA-W"}]):
            with self.subTest(address=address):
                response = self.create(self.creator, **({} if address is None else {"address": address}))
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
                self.assertNotIn("NO-SUCH-CODE", response.content.decode())
        self.assertFalse(Individual.objects.filter(last_name="Throughdci").exists())

    # -- persons and households without a location -----------------------------------------------------------

    def household(self, code, location, *members):
        """A household of ``members`` at ``location``; the locations a membership copies are set back after."""
        group = Group(code=code, location=location, json_ext={})
        group.save(user=self.admin)
        placed = {member.id: member.location_id for member in members}
        for member in members:
            GroupIndividual(group=group, individual=member, role=GroupIndividual.Role.HEAD).save(user=self.admin)
        Group.objects.filter(id=group.id).update(location=location)
        for member_id, location_id in placed.items():
            Individual.objects.filter(id=member_id).update(location_id=location_id)
        return group

    def assert_hidden(self, person):
        self.assertEqual(self.client_for(self.reader).get(DETAIL_URL.format(person.id)).status_code,
                         status.HTTP_404_NOT_FOUND)
        response = self.search(self.reader, by_fields(lastName=person.last_name))
        self.assertEqual(family_names(response.json()), [])
        self.assertEqual(self.update(person).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(Individual.objects.get(id=person.id).last_name, person.last_name)

    def test_a_person_without_a_location_is_no_ones(self):
        self.assert_hidden(self.person("Nolocfirst", "Accessnoloc", None))

    def test_a_member_elsewhere_of_a_household_without_a_location_is_hidden(self):
        member = self.person("Farmemberfirst", "Accessfarmember", self.other_village)
        group = self.household("DCIA-GN", None, member)
        self.assertIsNone(Group.objects.get(id=group.id).location_id)
        self.assert_hidden(member)

    def test_a_person_without_a_location_in_a_household_of_the_callers_locations_is_hidden(self):
        member = self.person("Nolocmemberfirst", "Accessnolocmember", None)
        self.household("DCIA-GL", self.village, member)
        self.assertIsNone(Individual.objects.get(id=member.id).location_id)
        self.assert_hidden(member)

    def test_a_member_elsewhere_of_a_household_of_the_callers_locations_is_read(self):
        member = self.person("Movedfirst", "Accessmoved", self.other_village)
        self.household("DCIA-GM", self.village, member)
        self.assertEqual(Individual.objects.get(id=member.id).location_id, self.other_village.id)
        self.assertEqual(self.client_for(self.reader).get(DETAIL_URL.format(member.id)).status_code,
                         status.HTTP_200_OK)
        self.assertEqual(family_names(self.search(self.reader, by_fields(lastName="Accessmoved")).json()),
                         ["Accessmoved"])

    def test_a_superuser_reads_a_person_without_a_location(self):
        superuser = create_test_interactive_user(username="dci_access_super2", custom_props={"is_superuser": True})
        person = self.person("Nolocfirst", "Accessnolocsuper", None)
        self.assertEqual(self.client_for(superuser).get(DETAIL_URL.format(person.id)).status_code,
                         status.HTTP_200_OK)

    def test_a_user_without_interactive_locations_reads_no_person(self):
        from types import SimpleNamespace
        from api_dci.person_search import visible_persons

        self.assertEqual(list(visible_persons(SimpleNamespace(is_superuser=False, _u=object()))), [])

    # -- the DCI search attributes ---------------------------------------------------------------------------

    def test_a_predicate_outside_the_dci_search_attributes_is_refused_to_every_reader(self):
        for attribute, value in (("user_updated__username", "dci_access_admin"),
                                 ("groupindividuals__individual__first_name", "Nearfirst"),
                                 ("json_ext__phone", "+25779000001"), ("location__code", "DCIA-V"),
                                 ("first_name__regex", "^N"), ("age", "30")):
            for user in (self.reader, self.unmasked_reader):
                with self.subTest(attribute=attribute, user=user.username):
                    response = self.search(user, by_predicate(attribute, value))
                    self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
                    self.assertIn(attribute, response.json()["header"]["message"])
                    self.assertNotIn("Accessnear", response.content.decode())

    def test_phone_email_and_gender_filters_read_the_person_json_ext_keys(self):
        for user, payload, expected in (
                (self.unmasked_reader, by_fields(phone="+2577900"), ["Accessnear"]),
                (self.unmasked_reader, by_predicate("phone", "+25779000001"), ["Accessnear"]),
                (self.reader, by_fields(email="accessnear@"), ["Accessnear"]),
                (self.reader, by_predicate("email", "accessnear@example.org"), ["Accessnear"]),
                (self.reader, by_fields(gender="Female"), ["Accessnear"]),
                (self.reader, by_fields(gender="Male"), []),
                (self.reader, by_predicate("gender", "female"), ["Accessnear"])):
            with self.subTest(user=user.username, criteria=payload["message"]["search_request"][0]["search_criteria"]):
                response = self.search(user, payload)
                self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
                self.assertEqual(family_names(response.json()), expected)

    # -- update ----------------------------------------------------------------------------------------------

    def update(self, person):
        payload = {
            "signature": "",
            "header": header("update"),
            "message": {"transaction_id": "txn-access-update", "person": {"name": {"family_name": "Renamed"}}},
        }
        return self.client_for(self.updater).put(UPDATE_URL.format(person.id), data=payload, format="json")

    def test_update_of_a_person_outside_the_callers_locations_is_not_found(self):
        response = self.update(self.far)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.content)
        self.assertEqual(Individual.objects.get(id=self.far.id).last_name, "Accessfar")

    def test_an_update_sending_the_phone_mask_back_is_refused(self):
        payload = {
            "signature": "",
            "header": header("update"),
            "message": {"transaction_id": "txn-access-update", "person": {
                "name": {"family_name": "Accessnear"}, "phone_number": [MASKED]}},
        }
        response = self.client_for(self.updater).put(UPDATE_URL.format(self.near.id), data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertIn("phone", response.json()["message"]["error_message"])
        self.assertEqual(Individual.objects.get(id=self.near.id).json_ext["phone"], "+25779000001")

    def test_update_of_a_person_in_the_callers_locations_is_written(self):
        response = self.update(self.near)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(Individual.objects.get(id=self.near.id).last_name, "Renamed")
