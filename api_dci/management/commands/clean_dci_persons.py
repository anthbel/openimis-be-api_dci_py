"""Places the persons the DCI API created without a location, retires on request the ones it cannot place, and
removes the request copies it kept under json_ext.spdci_data, from the persons and from their history."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api_dci.converters.person_converter import PersonConverter
from api_dci.person_search import PersonLocationRefused, creation_location
from api_dci.utils.json_ext_manager import JsonExtManager


def _addresses(*values):
    return [address for value in values if isinstance(value, list) for address in value]


def _cut_request_copy(json_ext):
    """``json_ext`` with its ``spdci_data`` cut to the registry-specific data of its registry type; None when it
    holds no request copy."""
    request_copy = json_ext.get("spdci_data") if isinstance(json_ext, dict) else None
    if not isinstance(request_copy, dict):
        return None
    kept = JsonExtManager.extract_registry_data(json_ext.get("registry_type"), request_copy)
    if kept is None or kept == request_copy:
        return None
    return {**json_ext, "spdci_data": kept}


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
        "data, moving sex, the first phone_number and email, and the address to their json_ext keys. Cuts the "
        "spdci_data of the person's history rows the same way. With --retire-unplaced, soft-deletes the persons "
        "it cannot place. Reports the persons it changes or cannot place by uuid; writes only with --apply."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Save the changes; without it, only report them.")
        parser.add_argument("--username", help="The user the changes are saved as; required with --apply. It needs "
                                               "the individual update right, and the delete right to retire.")
        parser.add_argument("--retire-unplaced", action="store_true",
                            help="Soft-delete, through the individual service, the persons that cannot be placed.")

    def handle(self, *args, **options):
        from individual.models import Individual

        retire = options["retire_unplaced"]
        user = None
        if options["apply"]:
            user = get_user_model().objects.filter(username=options["username"] or "").first()
            if user is None:
                raise CommandError("--apply needs --username naming an existing user")
            self._check_rights(user, retire)
        persons = Individual.objects.filter(is_deleted=False, json_ext__has_key="registry_metadata") \
            .select_related("user_created").order_by("id")
        counts = {"reported": 0, "unplaced": 0, "retired": 0, "history": 0}
        for person in persons.iterator():
            line = self._clean(person, user, retire, counts)
            if line:
                counts["reported"] += 1
                self.stdout.write(f"{person.uuid}: {line}")
        done = "" if user else " would be"
        self.stdout.write(f"{counts['reported']} persons reported, {counts['unplaced']} not placed, "
                          f"{counts['retired']}{done} retired, {counts['history']} history rows{done} purged")
        if counts["unplaced"] > counts["retired"]:
            self.stdout.write("Persons not placed have no location and are visible to every user: place them, or "
                              "retire them with --retire-unplaced.")

    @staticmethod
    def _check_rights(user, retire):
        """CommandError unless ``user`` holds the individual rights the changes need."""
        from individual.apps import IndividualConfig

        if not user.has_perms(IndividualConfig.gql_individual_update_perms):
            raise CommandError("--username needs the individual update right")
        if retire and not user.has_perms(IndividualConfig.gql_individual_delete_perms):
            raise CommandError("--retire-unplaced needs a --username holding the individual delete right")

    def _clean(self, person, user, retire, counts):
        """The report line of ``person``, saved as ``user`` unless None, in one transaction; empty when nothing
        changes."""
        with transaction.atomic():
            parts = self._place_and_cut(person, user)
            if person.location_id is None:
                counts["unplaced"] += 1
                if retire:
                    parts.append(self._retire(person, user, counts))
            rows = self._purge_history(person, user)
            if rows:
                counts["history"] += rows
                parts.append(f"{rows} history rows {'purged' if user else 'would be purged'}")
        return "; ".join(parts)

    def _place_and_cut(self, person, user):
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
            person.save(user=user)
        return parts

    @staticmethod
    def _retire(person, user, counts):
        """Soft-deletes ``person`` through the individual service as ``user``; its report part."""
        from individual.services import IndividualService

        if user is None:
            counts["retired"] += 1
            return "would be retired"
        result = IndividualService(user).delete({"id": person.id})
        if not result.get("success"):
            return "not retired (the individual service refused the deletion)"
        counts["retired"] += 1
        return "retired"

    @staticmethod
    def _purge_history(person, user):
        """The number of history rows of ``person`` holding a request copy, cut when ``user`` is given."""
        from individual.models import Individual

        history = Individual.history.model.objects.filter(id=person.id)
        rows = 0
        for row in history.only("pk", "json_ext").iterator():
            cut = _cut_request_copy(row.json_ext)
            if cut is None:
                continue
            rows += 1
            if user:
                history.filter(pk=row.pk).update(json_ext=cut)
        return rows
