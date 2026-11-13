"""
Person create and update audit user.

The assembly's user model is core.User, so the audit user of a create or update is the
authenticated openIMIS user.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from api_dci.tests.mixin import LogInMixin

CREATE_URL = "/api/api_dci/registry/person"


def person_message(action):
    return {
        "signature": "",
        "header": {
            "version": "1.0.0",
            "message_id": "msg-audit-001",
            "message_ts": "2026-11-11T00:00:00Z",
            "action": action,
            "sender_id": "programme-mis",
            "receiver_id": "registry",
        },
        "message": {
            "transaction_id": "txn-audit-001",
            "person": {"name": {"given_name": "Aline", "family_name": "Audit"}},
        },
    }


class PersonAuditUserTestCase(LogInMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.test_user = self.get_or_create_user_api()
        self.login()

    def test_the_assembly_user_model_is_not_the_contrib_auth_user(self):
        self.assertEqual(get_user_model()._meta.label, "core.User")
        self.assertIsInstance(self.test_user, get_user_model())

    def test_create_records_the_authenticated_user(self):
        individual = mock.Mock(uuid="00000000-0000-0000-0000-000000000001")
        with mock.patch("api_dci.views.person_create_view.get_registry_type", return_value="SR"), \
                mock.patch("api_dci.views.person_create_view.PersonConverter") as converter:
            converter.create_individual_from_spdci.return_value = (individual, {})
            converter.generate_person_id.return_value = "openimis:individual:1"
            response = self.client.post(CREATE_URL, person_message("create"), format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(converter.create_individual_from_spdci.call_args.kwargs["user"], self.test_user)

    def test_update_records_the_authenticated_user(self):
        from individual.models import Individual

        individual = Individual(first_name="Aline", last_name="Audit", dob="1990-01-15", json_ext={})
        individual.save(user=self.test_user)
        person_id = f"openimis:individual:{individual.uuid}"
        with mock.patch("api_dci.views.person_create_view.get_registry_type", return_value="SR"), \
                mock.patch("api_dci.views.person_create_view.PersonConverter.update_individual_from_spdci",
                           return_value=(individual, {})) as update:
            response = self.client.put(f"{CREATE_URL}/{person_id}/update", person_message("update"),
                                       format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(update.call_args.kwargs["user"], self.test_user)
