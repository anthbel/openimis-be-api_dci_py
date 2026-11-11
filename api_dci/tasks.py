import threading
import requests
import logging

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
        try:
            from individual.models import Individual
            from .converters.person_converter import PersonConverter
            from .serializers.person_serializer import DCISearchResponseSerializer

            # Extract common message details
            transaction_id = self.request_data.get('message', {}).get('transaction_id')
            sender_uri = self.request_data.get('header', {}).get('sender_uri')

            if not sender_uri:
                logger.error("DCI Async Search failed: No sender_uri provided for callback.")
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

            query_obj = search_criteria.get('query', {})

            # Navigate DCI expression structure: query -> value -> expression -> query
            expression_q = query_obj.get('value', {}).get('expression', {}).get('query', {})

            if not expression_q:
                expression_q = query_obj

            queryset = Individual.objects.filter(is_deleted=False)

            filter_list = expression_q.get('$and', [])
            if not filter_list and isinstance(expression_q, dict) and '$and' not in expression_q:
                filter_list = [{k: {"$eq": v}} for k, v in expression_q.items() if k != '@type']

            filters = {}
            for item in filter_list:
                if isinstance(item, dict):
                    for k, v in item.items():
                        if isinstance(v, dict) and '$eq' in v:
                            filters[k] = v['$eq']
                        else:
                            filters[k] = v

            if 'firstName' in filters:
                queryset = queryset.filter(first_name__icontains=filters['firstName'])
            if 'lastName' in filters:
                queryset = queryset.filter(last_name__icontains=filters['lastName'])
            if 'dob' in filters:
                queryset = queryset.filter(dob=filters['dob'])
            if 'gender' in filters:
                gender_map = {'Male': 'M', 'Female': 'F', 'Other': 'O'}
                gender_code = gender_map.get(filters['gender'])
                if gender_code:
                    queryset = queryset.filter(gender__code=gender_code)
            if 'phone' in filters:
                queryset = queryset.filter(phone__icontains=filters['phone'])
            if 'email' in filters:
                queryset = queryset.filter(email__icontains=filters['email'])

            # Convert to DCI Person format
            persons = [
                PersonConverter.individual_to_dci_person(individual)
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
        requests.post(
            sender_uri,
            json=response_data,
            headers={"Content-Type": "application/json"},
            timeout=10,
            allow_redirects=False,
        )
