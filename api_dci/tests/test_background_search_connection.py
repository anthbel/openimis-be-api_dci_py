"""The background search thread closes the database connection it opened."""
from unittest import mock

from django.test import SimpleTestCase

from api_dci.tasks import BackgroundSearchTask


class BackgroundSearchConnectionTestCase(SimpleTestCase):
    def task(self, request_data):
        return BackgroundSearchTask(request_data=request_data, search_requests=[{}])

    def test_connection_closed_after_a_callback_is_posted(self):
        task = self.task({"header": {"sender_uri": "https://mis.example.org/on-search"}, "message": {}})
        with mock.patch("api_dci.tasks.connection") as connection, \
                mock.patch("api_dci.tasks.run_provider_search", return_value=([], None)), \
                mock.patch("api_dci.tasks.requests.post"):
            task.provider = object()
            task.run()
        connection.close.assert_called_once_with()

    def test_connection_closed_when_no_callback_uri_is_given(self):
        with mock.patch("api_dci.tasks.connection") as connection:
            self.task({"header": {}, "message": {}}).run()
        connection.close.assert_called_once_with()

    def test_connection_closed_when_the_search_fails(self):
        task = self.task({"header": {"sender_uri": "https://mis.example.org/on-search"}, "message": {}})
        task.provider = object()
        with mock.patch("api_dci.tasks.connection") as connection, \
                mock.patch("api_dci.tasks.run_provider_search", side_effect=RuntimeError("boom")):
            task.run()
        connection.close.assert_called_once_with()
