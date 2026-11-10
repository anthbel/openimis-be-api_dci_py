"""
Tests for DCIPersonPermissions.

The permission class gates every DCI view; these tests call txn_status
directly with a forced user object so only the permission check decides
the outcome.
"""
from types import SimpleNamespace

from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from api_dci.permissions import DCIPersonPermissions
from api_dci.views.txn_status_viewset import txn_status


def _txn_status_payload():
    return {
        "signature": "",
        "header": {
            "version": "1.0.0",
            "message_id": "msg-perm-001",
            "message_ts": "2026-01-01T00:00:00Z",
            "action": "txn-status",
            "sender_id": "test-sender",
            "receiver_id": "openimis",
            "is_msg_encrypted": False,
        },
        "message": {
            "transaction_id": "txn-perm-001",
            "txnstatus_request": {
                "reference_id": "ref-perm-001",
                "txn_type": "search",
                "attribute_type": "transaction_id",
                "attribute_value": "txn-perm-001",
            },
        },
    }


def _raise_runtime_error(*args, **kwargs):
    raise RuntimeError("permission backend unavailable")


class DCIPersonPermissionsViewTestCase(SimpleTestCase):
    """A user object that cannot answer the permission check is denied."""

    def setUp(self):
        self.factory = APIRequestFactory()

    def _post_txn_status_as(self, user):
        request = self.factory.post(
            "/api/api_dci/registry/txn/status", _txn_status_payload(), format="json"
        )
        force_authenticate(request, user=user)
        return txn_status(request)

    def test_user_without_has_perm_gets_403(self):
        user = SimpleNamespace(is_authenticated=True)
        response = self._post_txn_status_as(user)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_user_whose_has_perm_raises_gets_403(self):
        user = SimpleNamespace(is_authenticated=True, has_perm=_raise_runtime_error)
        response = self._post_txn_status_as(user)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_user_without_is_authenticated_gets_403(self):
        user = SimpleNamespace()
        response = self._post_txn_status_as(user)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_user_with_permission_gets_200(self):
        user = SimpleNamespace(is_authenticated=True, has_perm=lambda perm: True)
        response = self._post_txn_status_as(user)
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class DCIPersonPermissionsUnitTestCase(SimpleTestCase):
    """has_permission returns the user's has_perm answer for the method's right."""

    def setUp(self):
        self.permission = DCIPersonPermissions()

    def _request(self, method, user):
        return SimpleNamespace(method=method, user=user)

    def test_granted_right_allows(self):
        checked = []
        user = SimpleNamespace(
            is_authenticated=True,
            has_perm=lambda perm: checked.append(perm) or True,
        )
        self.assertTrue(self.permission.has_permission(self._request("POST", user), None))
        self.assertEqual(checked, ["individual.gql_query_individuals_perms"])

    def test_missing_right_denies(self):
        user = SimpleNamespace(is_authenticated=True, has_perm=lambda perm: False)
        self.assertFalse(self.permission.has_permission(self._request("POST", user), None))

    def test_unauthenticated_user_denies(self):
        user = SimpleNamespace(is_authenticated=False, has_perm=lambda perm: True)
        self.assertFalse(self.permission.has_permission(self._request("POST", user), None))

    def test_unmapped_method_denies(self):
        user = SimpleNamespace(is_authenticated=True, has_perm=lambda perm: True)
        self.assertFalse(self.permission.has_permission(self._request("OPTIONS", user), None))
