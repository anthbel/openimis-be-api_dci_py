"""clean_dci_persons: the persons the DCI API created without a location are placed at their address location, and
the raw request copies DCI kept under json_ext.spdci_data are removed."""
import copy
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from core.test_helpers import create_test_interactive_user
from individual.models import Individual

from api_dci.tests.test_person_access import INDIVIDUAL_QUERY, PERSON_CREATE, clear_location_cache, make_location, \
    role_user

# json_ext of a person the DCI API created or updated before it placed persons: the registry structure, with the
# whole request person under spdci_data.
OLD_JSON_EXT = {
    "registry_type": "fr",
    "registry_metadata": {"source_system": "programme-mis", "external_id": "", "last_sync": "2026-01-01T00:00:00Z"},
    "spdci_data": {
        "name": {"given_name": "Oldfirst", "family_name": "Cleanold"},
        "birth_date": "1980-04-05T00:00:00Z",
        "sex": "female",
        "phone_number": ["+25779000005", "+25779000004"],
        "email": ["old@example.org"],
        "address": [{"type": "residential", "location_code": "DCIC-V"}],
        "farm_details": [{"crop": "maize"}],
    },
}


@override_settings(ROW_SECURITY=True)
class CleanDciPersonsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        clear_location_cache()
        region = make_location("DCIC-R", "R")
        cls.district = make_location("DCIC-D", "D", region)
        cls.other_district = make_location("DCIC-D2", "D", region)
        cls.village = make_location("DCIC-V", "V", make_location("DCIC-W", "W", cls.district))
        cls.other_village = make_location("DCIC-V2", "V", make_location("DCIC-W2", "W", cls.other_district))
        clear_location_cache()
        cls.admin = create_test_interactive_user(username="dci_clean_admin")
        cls.creator = role_user("dci_clean_creator", [PERSON_CREATE], ["DCIC-D"])
        cls.far_reader = role_user("dci_clean_far_reader", [INDIVIDUAL_QUERY], ["DCIC-D2"])

    def setUp(self):
        super().setUp()
        clear_location_cache()

    def dci_person(self, last_name, json_ext=None, location=None):
        person = Individual(first_name="Oldfirst", last_name=last_name, dob="1980-04-05", location=location,
                            json_ext=copy.deepcopy(json_ext if json_ext is not None else OLD_JSON_EXT))
        person.save(user=self.creator)
        return Individual.objects.get(id=person.id)

    def clean(self, *args):
        out = StringIO()
        call_command("clean_dci_persons", *args, stdout=out)
        return out.getvalue()

    def apply(self):
        return self.clean("--apply", "--username", self.admin.username)

    def test_an_unlocated_dci_person_is_placed_and_its_request_copy_removed(self):
        person = self.dci_person("Cleanold")
        self.assertIn(person, Individual.get_queryset(None, self.far_reader))
        out = self.apply()
        person.refresh_from_db()
        self.assertEqual(person.location_id, self.village.id)
        self.assertEqual(person.json_ext["spdci_data"], {"farm_details": [{"crop": "maize"}]})
        self.assertEqual((person.json_ext["sex"], person.json_ext["phone"], person.json_ext["email"]),
                         ("female", "+25779000005", "old@example.org"))
        self.assertEqual(person.json_ext["address"], [{"type": "residential", "location_code": "DCIC-V"}])
        self.assertEqual(person.json_ext["registry_metadata"]["source_system"], "programme-mis")
        self.assertNotIn("+25779000004", str(person.json_ext))
        self.assertNotIn("1980-04-05", str(person.json_ext))
        self.assertEqual(person.user_updated, self.admin)
        self.assertNotIn(person, Individual.get_queryset(None, self.far_reader))
        self.assertIn(f"{person.uuid}: placed; request copy removed", out)
        self.assertNotIn("Cleanold", out)

    def test_a_dry_run_reports_and_changes_nothing(self):
        person = self.dci_person("Cleandry")
        out = self.clean()
        self.assertEqual(Individual.objects.get(id=person.id).json_ext, OLD_JSON_EXT)
        self.assertIsNone(Individual.objects.get(id=person.id).location_id)
        self.assertIn(f"{person.uuid}: would be placed; request copy would be removed", out)

    def test_a_person_without_one_address_location_of_its_creator_is_reported_and_not_placed(self):
        outside = copy.deepcopy(OLD_JSON_EXT)
        outside["spdci_data"]["address"] = [{"location_code": "DCIC-V2"}]
        no_code = copy.deepcopy(OLD_JSON_EXT)
        del no_code["spdci_data"]["address"]
        for last_name, json_ext, reason in (
                ("Cleanoutside", outside, "the address location is not one of your locations"),
                ("Cleannocode", no_code, "the person needs one address location_code")):
            with self.subTest(last_name=last_name):
                person = self.dci_person(last_name, json_ext)
                out = self.apply()
                person.refresh_from_db()
                self.assertIsNone(person.location_id)
                self.assertEqual(person.json_ext["spdci_data"], {"farm_details": [{"crop": "maize"}]})
                self.assertIn(f"{person.uuid}: not placed ({reason}); request copy removed", out)

    def test_a_located_person_keeps_its_location_and_loses_its_request_copy(self):
        person = self.dci_person("Cleanlocated", location=self.other_village)
        out = self.apply()
        person.refresh_from_db()
        self.assertEqual(person.location_id, self.other_village.id)
        self.assertEqual(person.json_ext["spdci_data"], {"farm_details": [{"crop": "maize"}]})
        self.assertIn(f"{person.uuid}: request copy removed", out)

    def test_the_own_keys_a_later_update_wrote_are_kept(self):
        json_ext = {**copy.deepcopy(OLD_JSON_EXT), "phone": "+25779000099"}
        person = self.dci_person("Cleannewer", json_ext)
        self.apply()
        person.refresh_from_db()
        self.assertEqual(person.json_ext["phone"], "+25779000099")

    def test_persons_the_dci_api_did_not_write_and_clean_persons_are_left_as_they_are(self):
        other = Individual(first_name="Otherfirst", last_name="Cleanother", dob="1980-04-05",
                           json_ext={"spdci_data": {"name": "kept"}})
        other.save(user=self.admin)
        clean = self.dci_person("Cleanclean", {**OLD_JSON_EXT, "spdci_data": {"farm_details": []}},
                                location=self.village)
        version = Individual.objects.get(id=clean.id).version
        out = self.apply()
        self.assertEqual(Individual.objects.get(id=other.id).json_ext, {"spdci_data": {"name": "kept"}})
        self.assertEqual(Individual.objects.get(id=clean.id).version, version)
        self.assertNotIn(str(other.uuid), out)
        self.assertNotIn(str(clean.uuid), out)

    def test_apply_needs_an_existing_user(self):
        with self.assertRaises(CommandError):
            self.clean("--apply")
        with self.assertRaises(CommandError):
            self.clean("--apply", "--username", "no-such-user")
