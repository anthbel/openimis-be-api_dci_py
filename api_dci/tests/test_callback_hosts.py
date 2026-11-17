"""The async search posts its results only over https and only to a host of ``callback_allowed_hosts``: another
sender_uri is refused before the search is queued, and checked again before the callback is posted."""
from unittest import mock

import urllib3.util.connection

from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from api_dci.apps import ApiDciConfig
from api_dci.record_providers import register_record_provider
from api_dci.tasks import BackgroundSearchTask
from api_dci.tests.record_provider_fixtures import PROVIDER_RIGHT
from api_dci.tests.test_record_providers import (
    ASYNC_URL, PERSON_QUERY_RIGHT, PROVIDER_PATH, ProviderRegistryIsolation, search_payload, user_holding,
)
from api_dci.views.person_viewset import async_search

ALLOWED = ["mis.example.org"]
REFUSED_URIS = (
    None, "", "mis.example.org/on-search", "http://mis.example.org/on-search", "ftp://mis.example.org/on-search",
    "https://other.example.org/on-search", "https://mis.example.org.evil.example/on-search",
    "https://evil.example/mis.example.org", "https://mis.example.org@evil.example/on-search",
    "https://10.0.0.1/on-search", "https:///on-search",
    # requests reads the host up to a backslash: these would connect to evil.example and 10.0.0.5.
    "https://evil.example\\@mis.example.org/cb", "https://10.0.0.5:8443\\@mis.example.org/cb",
    "https://mis.example.org\\cb", "https://user@mis.example.org/cb", "https://:secret@mis.example.org/cb",
    "https://mis.example.org\t/cb", "https://mis.example.org/c b", "https://mis.example.org/cb\n",
    "https://mis.example.org\x00.evil.example/cb", "https://mis.example.org:99999/cb",
)
ACCEPTED_URIS = ("https://mis.example.org/on-search", "https://MIS.Example.ORG:8443/registry/on-search")


def with_sender_uri(payload, sender_uri):
    if sender_uri is None:
        payload["header"].pop("sender_uri")
    else:
        payload["header"]["sender_uri"] = sender_uri
    return payload


class AsyncSearchCallbackHostTestCase(ProviderRegistryIsolation, SimpleTestCase):
    def setUp(self):
        super().setUp()
        register_record_provider("Household", PROVIDER_PATH)
        self.factory = APIRequestFactory()
        self.person_reader = user_holding(PERSON_QUERY_RIGHT)
        self.consumer = user_holding(PROVIDER_RIGHT)

    def _post(self, payload, user):
        request = self.factory.post(ASYNC_URL, payload, format="json")
        force_authenticate(request, user=user)
        return async_search(request)

    def searches(self):
        """(record type, user) of a person search and of a record provider search."""
        return (("SRPerson", self.person_reader), ("Household", self.consumer))

    def test_a_callback_not_https_or_to_an_unlisted_host_is_refused_before_queuing(self):
        for record_type, user in self.searches():
            for sender_uri in REFUSED_URIS:
                payload = with_sender_uri(search_payload(record_type), sender_uri)
                with self.subTest(record_type=record_type, sender_uri=sender_uri), \
                        mock.patch.object(ApiDciConfig, "callback_allowed_hosts", ALLOWED), \
                        mock.patch("api_dci.tasks.BackgroundSearchTask") as task:
                    response = self._post(payload, user)
                    self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                    self.assertEqual(response.data["header"]["status"], "error")
                    self.assertNotIn("message", response.data)
                    task.assert_not_called()

    def test_every_callback_is_refused_while_no_host_is_listed(self):
        for record_type, user in self.searches():
            payload = with_sender_uri(search_payload(record_type), ACCEPTED_URIS[0])
            with self.subTest(record_type=record_type), \
                    mock.patch("api_dci.tasks.BackgroundSearchTask") as task:
                response = self._post(payload, user)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                task.assert_not_called()

    def test_an_https_callback_to_a_listed_host_is_queued(self):
        for record_type, user in self.searches():
            for sender_uri in ACCEPTED_URIS:
                payload = with_sender_uri(search_payload(record_type), sender_uri)
                with self.subTest(record_type=record_type, sender_uri=sender_uri), \
                        mock.patch.object(ApiDciConfig, "callback_allowed_hosts", ["MIS.example.org"]), \
                        mock.patch("api_dci.tasks.BackgroundSearchTask") as task:
                    response = self._post(payload, user)
                    self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
                    task.return_value.start.assert_called_once_with()


class CallbackPostTestCase(SimpleTestCase):
    """The task checks the callback again before posting, since the allowed hosts may change once it is queued."""

    def test_a_callback_whose_host_is_no_longer_listed_is_not_posted(self):
        with mock.patch.object(ApiDciConfig, "callback_allowed_hosts", []), \
                mock.patch("api_dci.tasks.requests.post") as post:
            BackgroundSearchTask._post_callback(ACCEPTED_URIS[0], {"message": {}})
        post.assert_not_called()

    def test_a_refused_callback_is_never_searched_nor_posted(self):
        payload = with_sender_uri(search_payload("SRPerson"), "http://mis.example.org/on-search")
        task = BackgroundSearchTask(request_data=payload, search_requests=payload["message"]["search_request"],
                                    user=user_holding(PERSON_QUERY_RIGHT))
        with mock.patch.object(ApiDciConfig, "callback_allowed_hosts", ALLOWED), \
                mock.patch("api_dci.tasks.async_person_queryset") as search, \
                mock.patch("api_dci.tasks.requests.post") as post:
            task._search_and_post()
        search.assert_not_called()
        post.assert_not_called()

    def test_a_listed_callback_is_posted_without_following_redirects(self):
        with mock.patch.object(ApiDciConfig, "callback_allowed_hosts", ALLOWED), \
                mock.patch("api_dci.tasks.requests.post") as post:
            BackgroundSearchTask._post_callback(ACCEPTED_URIS[1], {"message": {}})
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], ACCEPTED_URIS[1])
        self.assertIs(post.call_args.kwargs["allow_redirects"], False)
        self.assertEqual(post.call_args.kwargs["timeout"], 10)


class CallbackConnectionTestCase(ProviderRegistryIsolation, SimpleTestCase):
    """The hosts the async search connects to, recorded where urllib3 opens its socket, for a search run through
    the endpoint with its background task run in place."""

    def setUp(self):
        super().setUp()
        register_record_provider("Household", PROVIDER_PATH)
        self.factory = APIRequestFactory()
        self.consumer = user_holding(PROVIDER_RIGHT)
        self.connections = []

        def create_connection(address, *args, **kwargs):
            self.connections.append(address)
            raise ConnectionRefusedError("no network in tests")

        for patcher in (
                mock.patch.object(urllib3.util.connection, "create_connection", create_connection),
                mock.patch.object(BackgroundSearchTask, "start", BackgroundSearchTask.run),
                mock.patch.object(ApiDciConfig, "callback_allowed_hosts", ALLOWED),
                mock.patch("api_dci.tasks.connection")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def search(self, sender_uri):
        request = self.factory.post(
            ASYNC_URL, with_sender_uri(search_payload("Household"), sender_uri), format="json")
        force_authenticate(request, user=self.consumer)
        return async_search(request)

    def test_a_refused_callback_opens_no_connection(self):
        for sender_uri in REFUSED_URIS:
            with self.subTest(sender_uri=sender_uri):
                self.connections.clear()
                response = self.search(sender_uri)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(self.connections, [])

    def test_the_task_posts_no_refused_callback_however_it_was_queued(self):
        for sender_uri in REFUSED_URIS:
            with self.subTest(sender_uri=sender_uri):
                self.connections.clear()
                BackgroundSearchTask._post_callback(sender_uri, {"message": {}})
                self.assertEqual(self.connections, [])

    def test_an_allowed_callback_connects_to_the_listed_host_only(self):
        for sender_uri, port in zip(ACCEPTED_URIS, (443, 8443)):
            with self.subTest(sender_uri=sender_uri):
                self.connections.clear()
                response = self.search(sender_uri)
                self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
                self.assertEqual(self.connections, [("mis.example.org", port)])
