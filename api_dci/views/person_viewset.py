"""
DCI Person ViewSet

REST API views for DCI Person sync/search operations.
"""
import logging
from urllib.parse import urlsplit

from django.core.exceptions import PermissionDenied
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

from ..serializers import (
    DCISearchRequestSerializer,
    DCISearchResponseSerializer,
)
from ..converters.person_converter import PersonConverter
from ..permissions import DCISearchPermissions
from ..config import get_registry_type
from ..record_providers import provider_search_args, run_provider_search, search_target

logger = logging.getLogger(__name__)


# Helper functions for SPDCI query processing

def _extract_filters_from_expression(expression_query):
    """Extract filters from expression query format"""
    filters = {}

    # Find $and or $or arrays if they exist
    filter_list = expression_query.get('$and', [])
    if not filter_list and isinstance(expression_query, dict) and '$and' not in expression_query:
        # Flat format
        filter_list = [{k: {"$eq": v}} for k, v in expression_query.items() if k != '@type']

    # Extract filters
    for item in filter_list:
        if isinstance(item, dict):
            for k, v in item.items():
                if isinstance(v, dict) and '$eq' in v:
                    filters[k] = v['$eq']
                else:
                    filters[k] = v

    return filters


def _apply_filters(queryset, filters):
    """Apply filters to Individual queryset"""
    if 'firstName' in filters:
        queryset = queryset.filter(first_name__icontains=filters['firstName'])

    if 'lastName' in filters:
        queryset = queryset.filter(last_name__icontains=filters['lastName'])

    if 'dob' in filters:
        queryset = queryset.filter(dob=filters['dob'])

    if 'gender' in filters:
        # Map DCI gender to OpenIMIS gender code
        gender_map = {
            'Male': 'M',
            'Female': 'F',
            'Other': 'O'
        }
        gender_code = gender_map.get(filters['gender'])
        if gender_code:
            queryset = queryset.filter(gender__code=gender_code)

    if 'phone' in filters:
        queryset = queryset.filter(phone__icontains=filters['phone'])

    if 'email' in filters:
        queryset = queryset.filter(email__icontains=filters['email'])

    return queryset


def _apply_predicate_expression(queryset, expression):
    """Apply a single predicate expression to the queryset"""
    attribute_name = expression.get('attribute_name', '')
    operator = expression.get('operator', 'eq')
    attribute_value = expression.get('attribute_value', '')

    # Map SPDCI operators to Django ORM
    # SPDCI operators: eq, gt, lt, ge, le, in
    if not attribute_name or not attribute_value:
        return queryset

    # Map attribute names to Individual model fields
    field_mapping = {
        'age': None,  # Requires calculation
        'first_name': 'first_name',
        'last_name': 'last_name',
        'firstName': 'first_name',
        'lastName': 'last_name',
        'dob': 'dob',
        'gender': 'gender__code',
        'phone': 'phone',
        'email': 'email',
    }

    field = field_mapping.get(attribute_name, attribute_name)
    if not field:
        return queryset

    # Apply operator
    if operator == 'eq':
        queryset = queryset.filter(**{field: attribute_value})
    elif operator == 'gt':
        queryset = queryset.filter(**{f'{field}__gt': attribute_value})
    elif operator == 'lt':
        queryset = queryset.filter(**{f'{field}__lt': attribute_value})
    elif operator == 'ge':
        queryset = queryset.filter(**{f'{field}__gte': attribute_value})
    elif operator == 'le':
        queryset = queryset.filter(**{f'{field}__lte': attribute_value})
    elif operator == 'in':
        if isinstance(attribute_value, list):
            queryset = queryset.filter(**{f'{field}__in': attribute_value})

    return queryset


def _error_response(message, http_status):
    return Response({'header': {'status': 'error', 'message': message}}, status=http_status)


def _provider_sync_search(request, provider, validated_data):
    """Serve one page of a record provider type."""
    message_data = validated_data['message']
    search_requests = message_data.get('search_request') or []
    if not search_requests:
        return _error_response("No search_request provided", status.HTTP_400_BAD_REQUEST)
    search_request = search_requests[0]
    try:
        records, next_cursor = run_provider_search(
            provider, request.user, validated_data, search_request
        )
    except ValueError as e:
        return _error_response(str(e), status.HTTP_400_BAD_REQUEST)
    except PermissionDenied:
        return _error_response("Permission denied", status.HTTP_403_FORBIDDEN)
    except Exception:
        logger.exception("DCI record provider search failed")
        return _error_response("Record search failed", status.HTTP_500_INTERNAL_SERVER_ERROR)

    response_data = DCISearchResponseSerializer.create_record_response(
        request_data=request.data,
        reg_record_type=search_request['search_criteria']['reg_record_type'],
        records=records,
        next_cursor=next_cursor,
        transaction_id=message_data['transaction_id'],
        reference_id=search_request.get('reference_id', ''),
    )
    return Response(response_data, status=status.HTTP_200_OK)


@extend_schema(
    tags=['DCI Registry'],
    summary='Search for Person records',
    description='''
    Search for Person records following the DCI (Digital Convergence Initiative) standard.

    This endpoint implements the DCI Registry Core API specification for searching Person records.
    The request and response follow the DCI message format with signature, header, and message sections.
    ''',
    request=DCISearchRequestSerializer,
    responses={
        200: DCISearchResponseSerializer,
    }
)
@api_view(['POST'])
@permission_classes([DCISearchPermissions])
def sync_search(request):
    """
    DCI sync/search endpoint for Person records.

    POST /api/dci/reg/sync/search

    This endpoint implements the DCI Registry Core API specification
    for searching Person records.

    Request body follows DCI message format:
    {
        "signature": {...},
        "header": {...},
        "message": {
            "transaction_id": "...",
            "search_criteria": {
                "reg_type": "person",
                "query_type": "sync",
                "query": {
                    "@type": "Person",
                    "firstName": "John",
                    "lastName": "Doe"
                }
            }
        }
    }

    Response follows DCI message format with matching Person records.
    """
    # Debug logging
    import logging
    logger = logging.getLogger(__name__)
    logger.error(f"[DEBUG] Received request data: {request.data}")

    # Validate request
    serializer = DCISearchRequestSerializer(data=request.data)
    if not serializer.is_valid():
        logger.error(f"[DEBUG] Validation errors: {serializer.errors}")
        return Response(
            {
                'header': {
                    'status': 'error',
                    'message': 'Invalid request format'
                },
                'errors': serializer.errors
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    validated_data = serializer.validated_data

    provider = search_target(request.data)
    if provider is not None:
        return _provider_sync_search(request, provider, validated_data)

    try:
        message_data = validated_data['message']
        transaction_id = message_data['transaction_id']
        search_requests = message_data.get('search_request', [])

        if not search_requests:
            raise ValueError("No search_request provided")

        search_request = search_requests[0]
        reference_id = search_request.get('reference_id', '')
        search_criteria = search_request.get('search_criteria', {})
        query_type = search_criteria.get('query_type', 'sync')
        query_obj = search_criteria.get('query', {})

        # Build query for Individual model
        from individual.models import Individual

        # Start with all valid individuals
        queryset = Individual.objects.filter(is_deleted=False)

        # Handle different SPDCI query types
        if query_type == 'idtype-value':
            # SPDCI FR Standard: Search by identifier type and value
            # Format: {"type": "FARMER_ID", "value": "FARMER-TEST-001"}
            id_type = query_obj.get('type', '')
            id_value = query_obj.get('value', '')

            if id_type and id_value:
                # Map SPDCI identifier types to OpenIMIS fields
                if id_type in ['FARMER_ID', 'UIN', 'NIN']:
                    # Search by UUID or other identifiers
                    queryset = queryset.filter(uuid__icontains=id_value)
                else:
                    # Default: search in UUID
                    queryset = queryset.filter(uuid__icontains=id_value)

        elif query_type == 'predicate':
            # SPDCI FR Standard: Search by predicate conditions
            # Format: [{"seq_num": 1, "expression1": {...}, "condition": "and", "expression2": {...}}]
            if isinstance(query_obj, list):
                for predicate in query_obj:
                    expr1 = predicate.get('expression1', {})
                    expr2 = predicate.get('expression2', {})

                    # Apply first expression
                    queryset = _apply_predicate_expression(queryset, expr1)
                    # Apply second expression if present
                    if expr2:
                        queryset = _apply_predicate_expression(queryset, expr2)

        elif query_type == 'expression':
            # SPDCI FR Standard: Expression query (implementation-specific)
            # Format: {"type": "...", "value": {"expression": {...}}}
            expression_value = query_obj.get('value', {})
            expression_query = expression_value.get('expression', {})

            # Apply expression filters (flexible format)
            filters = _extract_filters_from_expression(expression_query)
            queryset = _apply_filters(queryset, filters)

        elif query_type == 'sync' or query_type == 'async':
            # Legacy OpenIMIS format (backward compatibility)
            # Navigate DCI expression structure: query -> value -> expression -> query
            expression_q = query_obj.get('value', {}).get('expression', {}).get('query', {})

            # fallback to the flat query if expression structure is missing
            if not expression_q:
                expression_q = query_obj

            # Extract and apply filters
            filters = _extract_filters_from_expression(expression_q)
            queryset = _apply_filters(queryset, filters)

        else:
            raise ValueError(f"Unsupported query_type: {query_type}")

        # Convert to DCI Person format
        persons = [
            PersonConverter.individual_to_dci_person(individual)
            for individual in queryset[:100]  # Limit to 100 results
        ]

    except ImportError:
        # Individual module not installed
        return Response(
            {
                'header': {
                    'status': 'error',
                    'message': 'Individual module not available'
                }
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE
        )
    except Exception as e:
        return Response(
            {
                'header': {
                    'status': 'error',
                    'message': str(e)
                }
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    # Build response using configured registry type
    registry_type = get_registry_type()
    response_data = DCISearchResponseSerializer.create_response(
        request_data=request.data,
        persons=persons,
        transaction_id=transaction_id,
        reference_id=reference_id,
        format_type=registry_type
    )

    return Response(response_data, status=status.HTTP_200_OK)


@extend_schema(
    tags=['DCI Registry'],
    summary='Async Search for Person records',
    description='''
    Asynchronously search for Person records following the DCI standard.

    This endpoint returns an immediate 202 ACK response while queuing the search task.
    Once completed, it POSTs the search array callback back to standard sender_uri.
    ''',
    request=DCISearchRequestSerializer,
    responses={
        202: DCISearchResponseSerializer,
    }
)
@api_view(['POST'])
@permission_classes([DCISearchPermissions])
def async_search(request):
    """
    DCI async search endpoint for Person records.
    POST /api/dci/reg/search
    """
    # Validate request
    serializer = DCISearchRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(
            {
                'header': {
                    'status': 'error',
                    'message': 'Invalid request format'
                },
                'errors': serializer.errors
            },
            status=status.HTTP_400_BAD_REQUEST
        )

    validated_data = serializer.validated_data

    try:
        message_data = validated_data['message']
        transaction_id = message_data['transaction_id']
        search_requests = message_data.get('search_request', [])

        if not search_requests:
            raise ValueError("No search_request provided")

        provider = search_target(request.data)
        if provider is not None:
            sender_uri = validated_data['header'].get('sender_uri') or ''
            parts = urlsplit(sender_uri)
            if parts.scheme.lower() != 'https' or not parts.netloc:
                return _error_response(
                    "header.sender_uri must be an https:// URL", status.HTTP_400_BAD_REQUEST
                )
            try:
                provider_search_args(validated_data, search_requests[0])
            except ValueError as e:
                return _error_response(str(e), status.HTTP_400_BAD_REQUEST)

        # Queue background processing
        from ..tasks import BackgroundSearchTask
        task = BackgroundSearchTask(
            request_data=request.data,
            search_requests=search_requests,
            provider=provider,
            user=request.user,
        )
        task.start()

    except Exception as e:
        return Response(
            {
                'header': {
                    'status': 'error',
                    'message': str(e)
                }
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    # Return immediate ACK
    response_data = DCISearchResponseSerializer.create_ack_response(
        request_data=request.data,
        transaction_id=transaction_id
    )

    return Response(response_data, status=status.HTTP_202_ACCEPTED)
