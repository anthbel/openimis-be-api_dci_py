"""
Rate limit of the DCI search views, per authenticated account.

An empty body fails validation with 400 after the permission and throttle checks, so a
400 means the request was let through and a 429 means it was throttled.
"""
import os
import uuid
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from api_dci.views.person_viewset import async_search, sync_search

RATE_SETTING = "API_DCI_SEARCH_THROTTLE_RATE"
SYNC_URL = "/api/api_dci/registry/sync/search"
ASYNC_URL = "/api/api_dci/registry/async/search"
SEARCH_VIEWS = ((sync_search, SYNC_URL), (async_search, ASYNC_URL))


def search_account():
    return SimpleNamespace(pk=uuid.uuid4(), is_authenticated=True, has_perm=lambda perm: True)


class SearchThrottleTestCase(SimpleTestCase):

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.factory = APIRequestFactory()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(RATE_SETTING, None)

    def _post(self, view, url, user):
        request = self.factory.post(url, {}, format="json")
        force_authenticate(request, user=user)
        return view(request)

    def _statuses(self, view, url, user, count):
        return [self._post(view, url, user).status_code for _ in range(count)]

    @override_settings(**{RATE_SETTING: "2/min"})
    def test_request_over_the_rate_gets_429_with_retry_after(self):
        for view, url in SEARCH_VIEWS:
            with self.subTest(url=url):
                cache.clear()
                user = search_account()
                self.assertEqual(self._statuses(view, url, user, 2), [400, 400])
                response = self._post(view, url, user)
                self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
                self.assertTrue(1 <= int(response["Retry-After"]) <= 60)

    @override_settings(**{RATE_SETTING: "2/min"})
    def test_throttled_answer_has_the_body_of_a_refused_search(self):
        refused = SimpleNamespace(pk=uuid.uuid4(), is_authenticated=True,
                                  has_perm=lambda perm: False)
        denied = self._post(sync_search, SYNC_URL, refused)
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

        user = search_account()
        self._statuses(sync_search, SYNC_URL, user, 2)
        throttled = self._post(sync_search, SYNC_URL, user)
        self.assertEqual(throttled.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(set(throttled.data), set(denied.data))
        self.assertEqual(set(throttled.data), {"detail"})

    @override_settings(**{RATE_SETTING: "2/min"})
    def test_another_account_is_not_affected(self):
        for view, url in SEARCH_VIEWS:
            with self.subTest(url=url):
                cache.clear()
                first, second = search_account(), search_account()
                self._statuses(view, url, first, 2)
                self.assertEqual(self._post(view, url, first).status_code, 429)
                self.assertEqual(self._statuses(view, url, second, 2), [400, 400])

    @override_settings(**{RATE_SETTING: "2/min"})
    def test_sync_and_async_search_share_one_budget(self):
        user = search_account()
        self.assertEqual(self._post(sync_search, SYNC_URL, user).status_code, 400)
        self.assertEqual(self._post(async_search, ASYNC_URL, user).status_code, 400)
        self.assertEqual(self._post(sync_search, SYNC_URL, user).status_code, 429)
        self.assertEqual(self._post(async_search, ASYNC_URL, user).status_code, 429)

    def test_default_rate_is_120_per_minute(self):
        with override_settings(**{RATE_SETTING: "unused"}):
            del settings.API_DCI_SEARCH_THROTTLE_RATE
            for view, url in SEARCH_VIEWS:
                with self.subTest(url=url):
                    cache.clear()
                    user = search_account()
                    self.assertEqual(set(self._statuses(view, url, user, 120)), {400})
                    self.assertEqual(self._post(view, url, user).status_code, 429)

    def test_setting_sets_the_rate(self):
        for rate, allowed in (("3/min", 3), ("1/hour", 1)):
            with self.subTest(rate=rate), override_settings(**{RATE_SETTING: rate}):
                cache.clear()
                user = search_account()
                self.assertEqual(set(self._statuses(sync_search, SYNC_URL, user, allowed)), {400})
                self.assertEqual(self._post(sync_search, SYNC_URL, user).status_code, 429)

    def test_environment_variable_sets_the_rate_when_the_setting_is_absent(self):
        os.environ[RATE_SETTING] = "3/min"
        with override_settings(**{RATE_SETTING: "unused"}):
            del settings.API_DCI_SEARCH_THROTTLE_RATE
            user = search_account()
            self.assertEqual(self._statuses(sync_search, SYNC_URL, user, 3), [400] * 3)
            self.assertEqual(self._post(sync_search, SYNC_URL, user).status_code, 429)

    def test_setting_wins_over_the_environment_variable(self):
        os.environ[RATE_SETTING] = "1/min"
        with override_settings(**{RATE_SETTING: "3/min"}):
            user = search_account()
            self.assertEqual(self._statuses(sync_search, SYNC_URL, user, 3), [400] * 3)

    def test_none_or_empty_rate_disables_the_throttle(self):
        for rate in ("none", "None", "", "  "):
            for view, url in SEARCH_VIEWS:
                with self.subTest(rate=rate, url=url), override_settings(**{RATE_SETTING: rate}):
                    user = search_account()
                    self.assertEqual(set(self._statuses(view, url, user, 130)), {400})

    def test_none_in_the_environment_disables_the_throttle(self):
        os.environ[RATE_SETTING] = "none"
        with override_settings(**{RATE_SETTING: "unused"}):
            del settings.API_DCI_SEARCH_THROTTLE_RATE
            user = search_account()
            self.assertEqual(set(self._statuses(sync_search, SYNC_URL, user, 130)), {400})


class SearchThrottleRateCheckTestCase(SimpleTestCase):
    """The rate is checked when the app starts, so a bad value never reaches a search."""

    def test_malformed_rate_is_refused(self):
        from api_dci.throttling import check_search_throttle_rate

        for rate in ("120", "ten/min", "120/fortnight", "-1/min", "/min"):
            with self.subTest(rate=rate), override_settings(**{RATE_SETTING: rate}):
                with self.assertRaises(ImproperlyConfigured):
                    check_search_throttle_rate()

    def test_well_formed_or_disabled_rate_is_accepted(self):
        from api_dci.throttling import check_search_throttle_rate

        for rate in ("120/min", "5/s", "1000/day", "0/min", "none", ""):
            with self.subTest(rate=rate), override_settings(**{RATE_SETTING: rate}):
                check_search_throttle_rate()
