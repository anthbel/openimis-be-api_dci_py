"""Places the persons the DCI API created without a location, and removes the request copies it kept under
json_ext.spdci_data."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api_dci.converters.person_converter import PersonConverter
from api_dci.person_search import PersonLocationRefused, creation_location
from api_dci.utils.json_ext_manager import JsonExtManager


def _addresses(*values):
    return [address for value in values if isinstance(value, list) for address in value]


def cleaned_json_ext(json_ext):
    """``json_ext`` with its ``spdci_data`` cut to the registry-specific data of its registry type, the person's
    own keys (sex, phone, email, address) the copy holds and ``json_ext`` lacks added; None when it holds no
    such copy."""
    request_copy = json_ext.get("spdci_data")
    if not isinstance(request_copy, dict):
        return None
    kept = JsonExtManager.extract_registry_data(json_ext.get("registry_type"), request_copy)
    if kept is None or kept == request_copy:
        return None
    own = PersonConverter.dci_person_to_individual_data(request_copy).get("json_ext", {})
    return {**own, **json_ext, "spdci_data": kept}


class Command(BaseCommand):
    help = (
        "Places each person the DCI API created without a location at the one address location_code its json_ext "
        "names, when its creator may write that location, and cuts json_ext.spdci_data to the registry-specific "
        "data, moving sex, the first phone_number and email, and the address to their json_ext keys. Reports "
        "the persons it changes or cannot place by uuid; writes only with --apply."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Save the changes; without it, only report them.")
        parser.add_argument("--username", help="The user the changes are saved as; required with --apply.")

    def handle(self, *args, **options):
        from individual.models import Individual

        user = None
        if options["apply"]:
            user = get_user_model().objects.filter(username=options["username"] or "").first()
            if user is None:
                raise CommandError("--apply needs --username naming an existing user")
        persons = Individual.objects.filter(is_deleted=False, json_ext__has_key="registry_metadata") \
            .select_related("user_created").order_by("id")
        reported = unplaced = 0
        for person in persons.iterator():
            line = self._clean(person, user)
            if line:
                reported += 1
                unplaced += "not placed" in line
                self.stdout.write(f"{person.uuid}: {line}")
        self.stdout.write(f"{reported} persons reported, {unplaced} not placed")

    def _clean(self, person, user):
        """The report line of ``person``, saved as ``user`` unless None; empty when nothing changes."""
        json_ext = person.json_ext if isinstance(person.json_ext, dict) else {}
        cleaned = cleaned_json_ext(json_ext)
        placed = False
        parts = []
        if person.location_id is None:
            request_copy = json_ext.get("spdci_data") if isinstance(json_ext.get("spdci_data"), dict) else {}
            addresses = _addresses(json_ext.get("address"), request_copy.get("address"))
            try:
                person.location = creation_location(person.user_created, {"address": addresses})
                placed = True
                parts.append("placed" if user else "would be placed")
            except PersonLocationRefused as exc:
                parts.append(f"not placed ({exc})")
        if cleaned is not None:
            person.json_ext = cleaned
            parts.append("request copy removed" if user else "request copy would be removed")
        if user and (placed or cleaned is not None):
            with transaction.atomic():
                person.save(user=user)
        return "; ".join(parts)
