"""
LogIn Mixin for DCI tests

Provides authentication utilities for API tests.
"""
from rest_framework.test import APIClient


class LogInMixin:
    """
    Mixin for handling authentication in tests.
    """

    test_user = None
    _test_username = "test_dci_user"
    _test_password = "test_password_123"
    _auth_token = None

    def setUp(self):
        """
        Set up authentication for tests.
        """
        super().setUp()
        self.client = APIClient()

    def get_or_create_user_api(self):
        """
        Get or create a test user for API authentication.

        The user holds the DCI person rights through a role, in the district of ``dci_village``.

        Returns:
            User: Test user instance
        """
        from django.contrib.auth import get_user_model

        User = get_user_model()

        user = User.objects.filter(username=self._test_username).first()
        if user is None:
            from django.core.cache import caches
            from core.test_helpers import create_test_interactive_user
            from location.test_helpers import assign_user_districts

            user = create_test_interactive_user(
                username=self._test_username, password=self._test_password,
                roles=[self._dci_role().id],
            )
            assign_user_districts(user, [self.dci_village().parent.parent.code])
            caches["location"].clear()
            caches["default"].clear()
        return user

    @staticmethod
    def dci_village():
        """A village in the district of the test user, for the persons it reads, writes and creates."""
        from location.test_helpers import create_test_village

        return create_test_village({"name": "DCI test village", "code": "DCITV"})

    @staticmethod
    def _dci_role():
        """The role holding the DCI person rights."""
        from core.test_helpers import _sync_role_rights, create_test_role
        from api_dci.apps import ApiDciConfig

        role = create_test_role(name="DCI API test client")
        _sync_role_rights(role, [
            int(right)
            for rights in (ApiDciConfig.gql_dci_person_search_perms, ApiDciConfig.gql_dci_person_create_perms,
                           ApiDciConfig.gql_dci_person_update_perms)
            for right in rights
        ])
        return role

    def login(self):
        """
        Perform login and set authentication token.

        Returns:
            bool: True if login successful
        """
        if not self.test_user:
            self.test_user = self.get_or_create_user_api()

        # Re-fetch the user from DB so Django loads a fresh permission set
        # (avoids stale permission cache from earlier has_perm calls)
        from django.contrib.auth import get_user_model
        User = get_user_model()
        try:
            self.test_user = User.objects.get(pk=self.test_user.pk)
        except Exception:
            pass

        # Force authentication for the test client
        self.client.force_authenticate(user=self.test_user)
        return True

    def logout(self):
        """
        Logout and clear authentication.
        """
        self.client.force_authenticate(user=None)
        self._auth_token = None

    def load_user_data_from_json(self, json_path):
        """
        Load user credentials from JSON file.

        Args:
            json_path: Path to JSON file with credentials
        """
        # To be implemented if needed for loading test credentials
        # from JSON files like in FHIR R4 tests
        pass
