"""
DCI Person ViewSet

REST API views for DCI Person sync/search operations.
"""
import logging
from urllib.parse import urlsplit

from django.core.exceptions import PermissionDenied
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema

from ..serializers import (
    DCISearchRequestSerializer,
    DCISearchResponseSerializer,
)
from ..converters.person_converter import PersonConverter
from ..masking import mask_person
from ..person_search import check_async_person_search, sync_person_queryset
from ..permissions import DCISearchPermissions
from ..throttling import DCISearchRateThrottle
from ..config import get_registry_type
from ..record_providers import (
    InvalidSearchRequest, provider_search_args, run_provider_search, search_target,
)
from ..utils.log_safety import error_fields, error_text, exception_trace, log_error

logger = logging.getLogger(__name__)


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
    except InvalidSearchRequest as e:
        return _error_response(str(e), status.HTTP_400_BAD_REQUEST)
    except PermissionDenied:
        return _error_response("Permission denied", status.HTTP_403_FORBIDDEN)
    except Exception as e:
        logger.error("DCI record provider search failed: %s", exception_trace(e))
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
@throttle_classes([DCISearchRateThrottle])
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
    # Validate request
    serializer = DCISearchRequestSerializer(data=request.data)
    if not serializer.is_valid():
        logger.error(f"[DCI Sync Search] Validation errors: {error_fields(serializer.errors)}")
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
        queryset = sync_person_queryset(request.user, search_request.get('search_criteria', {}))

        # Convert to DCI Person format
        persons = [
            PersonConverter.individual_to_dci_person(mask_person(request.user, individual))
            for individual in queryset[:100]  # Limit to 100 results
        ]

    except InvalidSearchRequest as e:
        return _error_response(str(e), status.HTTP_400_BAD_REQUEST)
    except PermissionDenied as e:
        return _error_response(str(e), status.HTTP_403_FORBIDDEN)
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
        error_id = log_error(logger, "[DCI Sync Search] Unexpected error", e)
        return Response(
            {
                'header': {
                    'status': 'error',
                    'message': error_text('Internal server error', error_id)
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
@throttle_classes([DCISearchRateThrottle])
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
            except InvalidSearchRequest as e:
                return _error_response(str(e), status.HTTP_400_BAD_REQUEST)
        else:
            # The filters are checked here, so a refused one is answered before the search is queued.
            try:
                check_async_person_search(request.user, search_requests[0].get('search_criteria', {}))
            except PermissionDenied as e:
                return _error_response(str(e), status.HTTP_403_FORBIDDEN)

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
        error_id = log_error(logger, "[DCI Async Search] Unexpected error", e)
        return Response(
            {
                'header': {
                    'status': 'error',
                    'message': error_text('Internal server error', error_id)
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
