import threading
import requests
import logging

from django.db import connection

from .callback_hosts import callback_allowed
from .masking import mask_person
from .person_search import async_person_queryset
from .record_providers import run_provider_search
from .utils.log_safety import exception_trace

logger = logging.getLogger(__name__)


class BackgroundSearchTask(threading.Thread):
    """
    Background thread to process DCI async search requests.
    Once completed, sends a POST request with the results to the sender_uri callback.
    """

    def __init__(self, request_data, search_requests, *args, provider=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request_data = request_data
        self.search_requests = search_requests
        # A record provider serves the search instead of Individual when set.
        self.provider = provider
        self.user = user

    def run(self):
        # The thread opens its own database connection; nothing else closes it.
        try:
            self._search_and_post()
        finally:
            connection.close()

    def _search_and_post(self):
        try:
            from .converters.person_converter import PersonConverter
            from .serializers.person_serializer import DCISearchResponseSerializer

            # Extract common message details
            transaction_id = self.request_data.get('message', {}).get('transaction_id')
            sender_uri = self.request_data.get('header', {}).get('sender_uri')

            if not callback_allowed(sender_uri):
                logger.error("DCI Async Search for transaction %s refused: its sender_uri is not an https URL "
                             "to an allowed callback host.", transaction_id)
                return

            search_request = self.search_requests[0]
            reference_id = search_request.get('reference_id', '')
            search_criteria = search_request.get('search_criteria', {})

            if self.provider is not None:
                records, next_cursor = run_provider_search(
                    self.provider, self.user, self.request_data, search_request
                )
                response_data = DCISearchResponseSerializer.create_record_response(
                    request_data=self.request_data,
                    reg_record_type=search_criteria.get('reg_record_type'),
                    records=records,
                    next_cursor=next_cursor,
                    transaction_id=transaction_id,
                    reference_id=reference_id,
                )
                self._post_callback(sender_uri, response_data)
                return

            queryset = async_person_queryset(self.user, search_criteria)

            # Convert to DCI Person format
            persons = [
                PersonConverter.individual_to_dci_person(mask_person(self.user, individual))
                for individual in queryset[:100]
            ]

            # Build and send response callback
            response_data = DCISearchResponseSerializer.create_response(
                request_data=self.request_data,
                persons=persons,
                transaction_id=transaction_id,
                reference_id=reference_id
            )

            self._post_callback(sender_uri, response_data)

        except Exception as e:
            logger.error(
                "DCI Async Search background task failed for transaction %s: %s",
                self.request_data.get('message', {}).get('transaction_id'), exception_trace(e),
            )

    @staticmethod
    def _post_callback(sender_uri, response_data):
        """Posts ``response_data`` to ``sender_uri`` once checked again, since the allowed callback hosts may have
        changed since the search was queued."""
        if not callback_allowed(sender_uri):
            logger.error("DCI Async Search callback not posted: its sender_uri is not an https URL to an allowed "
                         "callback host.")
            return
        requests.post(
            sender_uri,
            json=response_data,
            headers={"Content-Type": "application/json"},
            timeout=10,
            allow_redirects=False,
        )
