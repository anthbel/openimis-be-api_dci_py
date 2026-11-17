"""clean_dci_persons: the persons the DCI API created without a location are placed at their address location or,
on request, retired, and the raw request copies DCI kept under json_ext.spdci_data are removed from the persons and
from their history."""
import copy
from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from core.test_helpers import create_test_interactive_user
from individual.models import Individual

from api_dci.tests.test_person_access import INDIVIDUAL_QUERY, PERSON_CREATE, clear_location_cache, make_location, \
    role_user

INDIVIDUAL_UPDATE = "159003"
INDIVIDUAL_DELETE = "159004"
RAW_ONLY = ("+25779000004", "1980-04-05")

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
        cls.updater = role_user("dci_clean_updater", [INDIVIDUAL_QUERY, INDIVIDUAL_UPDATE])
        cls.reader = role_user("dci_clean_reader", [INDIVIDUAL_QUERY])

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

    def history(self, person):
        return list(Individual.history.model.objects.filter(id=person.id).order_by("history_date")
                    .values_list("json_ext", "first_name", "last_name", "version"))

    def unplaced_person(self, last_name):
        no_code = copy.deepcopy(OLD_JSON_EXT)
        del no_code["spdci_data"]["address"]
        return self.dci_person(last_name, no_code)

    def test_the_history_rows_lose_their_request_copy_and_keep_everything_else(self):
        person = self.dci_person("Cleanhist")
        person.first_name = "Renamed"
        person.save(user=self.creator)
        before = self.history(person)
        self.assertEqual([row[0]["spdci_data"] for row in before], [OLD_JSON_EXT["spdci_data"]] * 2)
        out = self.apply()
        after = self.history(person)
        self.assertEqual(len(after), len(before) + 1)
        for (json_ext, *fields), (old_json_ext, *old_fields) in zip(after, before):
            self.assertEqual(fields, old_fields)
            self.assertEqual(json_ext, {**old_json_ext, "spdci_data": {"farm_details": [{"crop": "maize"}]}})
        self.assertEqual(after[-1][0]["spdci_data"], {"farm_details": [{"crop": "maize"}]})
        for value in RAW_ONLY:
            self.assertNotIn(value, str([row[0] for row in after]))
        self.assertIn(f"{person.uuid}: placed; request copy removed; 2 history rows purged", out)

    def test_a_person_cleaned_before_has_only_its_history_purged(self):
        person = self.dci_person("Cleanedbefore", location=self.village)
        person.json_ext = {**OLD_JSON_EXT, "spdci_data": {"farm_details": [{"crop": "maize"}]}}
        person.save(user=self.creator)
        person = Individual.objects.get(id=person.id)
        version = person.version
        out = self.apply()
        self.assertEqual(Individual.objects.get(id=person.id).version, version)
        self.assertEqual([row[0]["spdci_data"] for row in self.history(person)],
                         [{"farm_details": [{"crop": "maize"}]}] * 2)
        self.assertIn(f"{person.uuid}: 1 history rows purged\n", out)

    def test_a_soft_deleted_person_loses_its_request_copies_and_is_neither_placed_nor_retired(self):
        person = self.dci_person("Cleandeleted")
        person.delete(user=self.admin)
        rows = len(self.history(person))
        out = self.clean("--apply", "--username", self.admin.username, "--retire-unplaced")
        person = Individual.objects.get(id=person.id)
        self.assertTrue(person.is_deleted)
        self.assertIsNone(person.location_id)
        self.assertEqual(person.json_ext["spdci_data"], {"farm_details": [{"crop": "maize"}]})
        self.assertEqual(person.json_ext["phone"], "+25779000005")
        history = self.history(person)
        self.assertEqual([row[0]["spdci_data"] for row in history],
                         [{"farm_details": [{"crop": "maize"}]}] * len(history))
        self.assertIn(f"{person.uuid}: deleted; request copy removed; {rows} history rows purged\n", out)
        self.assertIn("0 not placed, 0 retired", out)

    def test_history_rows_are_purged_when_the_current_row_no_longer_names_the_dci_api(self):
        person = self.dci_person("Cleanrenamed", location=self.village)
        person.json_ext = {"note": "edited"}
        person.save(user=self.creator)
        out = self.apply()
        history = self.history(person)
        self.assertEqual(history[0][0]["spdci_data"], {"farm_details": [{"crop": "maize"}]})
        self.assertEqual(history[0][0]["registry_metadata"], OLD_JSON_EXT["registry_metadata"])
        self.assertEqual(history[1][0], {"note": "edited"})
        self.assertEqual(Individual.objects.get(id=person.id).json_ext, {"note": "edited"})
        self.assertIn(f"{person.uuid}: 1 history rows purged\n", out)

    def test_a_dry_run_leaves_the_history_as_it_is(self):
        person = self.dci_person("Cleanhistdry")
        before = self.history(person)
        out = self.clean()
        self.assertEqual(self.history(person), before)
        self.assertIn(f"{person.uuid}: would be placed; request copy would be removed; 1 history rows would be "
                      f"purged", out)

    def test_unplaced_persons_are_reported_and_kept_without_retire_unplaced(self):
        person = self.unplaced_person("Cleankept")
        out = self.apply()
        self.assertFalse(Individual.objects.get(id=person.id).is_deleted)
        self.assertIn("1 not placed, 0 retired", out)
        self.assertIn("retire them with --retire-unplaced", out)

    def test_retire_unplaced_soft_deletes_the_unplaced_persons_only(self):
        unplaced = self.unplaced_person("Cleanretired")
        placed = self.dci_person("Cleanplaced")
        not_dci = Individual(first_name="Otherfirst", last_name="Cleannotdci", dob="1980-04-05", json_ext={})
        not_dci.save(user=self.admin)
        out = self.clean("--apply", "--username", self.admin.username, "--retire-unplaced")
        unplaced = Individual.objects.get(id=unplaced.id)
        self.assertTrue(unplaced.is_deleted)
        self.assertEqual(unplaced.user_updated, self.admin)
        self.assertIsNone(unplaced.location_id)
        self.assertFalse(Individual.objects.get(id=placed.id).is_deleted)
        self.assertFalse(Individual.objects.get(id=not_dci.id).is_deleted)
        self.assertNotIn(str(not_dci.uuid), out)
        self.assertEqual({str(row[0]["spdci_data"]) for row in self.history(unplaced)},
                         {str({"farm_details": [{"crop": "maize"}]})})
        self.assertIn(f"{unplaced.uuid}: not placed (the person needs one address location_code); request copy "
                      f"removed; retired; ", out)
        self.assertIn("1 not placed, 1 retired", out)
        self.assertNotIn("retire them with --retire-unplaced", out)
        self.assertNotIn(unplaced, Individual.get_queryset(Individual.objects.filter(is_deleted=False),
                                                           self.far_reader))

    def test_a_retirement_the_individual_service_refuses_keeps_the_person_and_its_cleaning(self):
        person = self.unplaced_person("Cleanrefused")
        with mock.patch("individual.services.IndividualService.delete", return_value={"success": False}):
            out = self.clean("--apply", "--username", self.admin.username, "--retire-unplaced")
        person = Individual.objects.get(id=person.id)
        self.assertFalse(person.is_deleted)
        self.assertEqual(person.json_ext["spdci_data"], {"farm_details": [{"crop": "maize"}]})
        self.assertIn(f"{person.uuid}: not placed (the person needs one address location_code); request copy "
                      f"removed; not retired (the individual service refused the deletion); 1 history rows purged",
                      out)
        self.assertIn("1 not placed, 0 retired", out)
        self.assertIn("retire them with --retire-unplaced", out)

    def test_retire_unplaced_in_a_dry_run_deletes_nothing(self):
        person = self.unplaced_person("Cleanretiredry")
        out = self.clean("--retire-unplaced")
        self.assertFalse(Individual.objects.get(id=person.id).is_deleted)
        self.assertIn(f"{person.uuid}: not placed (the person needs one address location_code); request copy would "
                      f"be removed; would be retired", out)
        self.assertIn("1 not placed, 1 would be retired", out)

    def test_apply_is_refused_to_a_user_without_the_rights_it_needs(self):
        person = self.unplaced_person("Cleanrights")
        before = (Individual.objects.get(id=person.id).json_ext, self.history(person))
        for username, extra, message in (
                (self.reader.username, (), "individual update right"),
                (self.updater.username, ("--retire-unplaced",), "individual delete right")):
            with self.subTest(username=username):
                with self.assertRaisesMessage(CommandError, message):
                    self.clean("--apply", "--username", username, *extra)
        self.assertEqual((Individual.objects.get(id=person.id).json_ext, self.history(person)), before)
        self.assertFalse(Individual.objects.get(id=person.id).is_deleted)
        self.clean("--apply", "--username", self.updater.username)
        self.assertEqual(Individual.objects.get(id=person.id).json_ext["spdci_data"],
                         {"farm_details": [{"crop": "maize"}]})
