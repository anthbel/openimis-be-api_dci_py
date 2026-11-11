"""
Person Create/Update Views

REST API views for creating and updating Person/Individual records from SPDCI data.
Supports FR (Farmer Registry), SR (Social Registry), and IBR (Integrated Beneficiary Registry).
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from django.contrib.auth.models import User
import logging

from ..serializers import (
    DCIPersonCreateRequestSerializer,
    DCIPersonUpdateRequestSerializer,
    DCIPersonResponseSerializer,
)
from ..converters.person_converter import PersonConverter
from ..permissions import DCIPersonCreatePermissions, DCIPersonPermissions
from ..config import get_registry_type
from ..utils.log_safety import error_fields, exception_trace

logger = logging.getLogger(__name__)


@extend_schema(
    tags=['Registry Core API'],
    summary='Create Person record',
    description='''
    Create a new Person/Individual record from SPDCI data.

    **Registry Types:**
    - **FR (Farmer Registry):** Includes farm_details, machineries_details
    - **SR (Social Registry):** Includes household_details, socio_economic_details
    - **IBR (Integrated Beneficiary Registry):** Includes beneficiary_details, program_participation

    **Response:** Returns person_id in format `openimis:individual:{uuid}`

    **Permissions:** Requires `individual.gql_mutation_create_individuals_perms`
    ''',
    request=DCIPersonCreateRequestSerializer,
    responses={
        200: DCIPersonResponseSerializer,
        400: {'description': 'Invalid request format'},
        403: {'description': 'Permission denied'},
        500: {'description': 'Internal server error'},
    }
)
@api_view(['POST'])
@permission_classes([DCIPersonCreatePermissions])
def person_create(request):
    """
    Create Person/Individual from SPDCI data.

    POST /api/api_dci/registry/person

    Request format:
    {
        "signature": "",
        "header": {
            "version": "1.0.0",
            "message_id": "msg-create-001",
            "message_ts": "2026-03-23T10:00:00Z",
            "action": "create",
            "sender_id": "external-system",
            "receiver_id": "openimis"
        },
        "message": {
            "transaction_id": "txn-create-001",
            "person": {
                "name": {"given_name": "John", "family_name": "Doe"},
                "birth_date": "1990-01-15T00:00:00Z",
                "sex": "male",
                // ... FR/SR/IBR specific fields
            }
        }
    }
    """
    logger.info(f"[DCI Person Create] Received request from {request.user}")

    # Validate request
    serializer = DCIPersonCreateRequestSerializer(data=request.data)
    if not serializer.is_valid():
        logger.error(f"[DCI Person Create] Validation error: {error_fields(serializer.errors)}")
        return Response(
            DCIPersonResponseSerializer.create_error_response(
                request_data=request.data,
                error_message=f"Validation error: {serializer.errors}",
                operation="create"
            ),
            status=status.HTTP_400_BAD_REQUEST
        )

    validated_data = serializer.validated_data

    try:
        # Check Individual module availability
        try:
            from individual.models import Individual  # noqa: F401
        except ImportError:
            logger.error("[DCI Person Create] Individual module not available")
            return Response(
                DCIPersonResponseSerializer.create_error_response(
                    request_data=request.data,
                    error_message="Individual module not available",
                    operation="create"
                ),
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        # Extract person data from message
        message_data = validated_data['message']
        person_data = message_data.get('person', {})

        # Get registry type from config
        registry_type = get_registry_type()

        # Get user for audit trail
        user = request.user
        if not isinstance(user, User):
            # Fallback to Admin user
            user = User.objects.filter(username='Admin').first()
            if not user:
                user = User.objects.filter(is_superuser=True).first()

        # Extract metadata
        header_data = validated_data['header']
        metadata = {
            'source_system': header_data.get('sender_id', ''),
            'external_id': person_data.get('identifier', [{}])[0].get('value', '') if person_data.get('identifier') else ''
        }

        # Create Individual from SPDCI data
        individual, json_ext = PersonConverter.create_individual_from_spdci(
            spdci_person=person_data,
            registry_type=registry_type,
            user=user,
            metadata=metadata
        )

        # Generate person_id
        person_id = PersonConverter.generate_person_id(individual)

        logger.info(f"[DCI Person Create] Successfully created Individual {person_id}")

        # Build success response
        response_data = DCIPersonResponseSerializer.create_success_response(
            request_data=request.data,
            person_id=person_id,
            operation="create"
        )

        return Response(response_data, status=status.HTTP_200_OK)

    except ValueError as e:
        logger.error(f"[DCI Person Create] Invalid data: {type(e).__name__}")
        return Response(
            DCIPersonResponseSerializer.create_error_response(
                request_data=request.data,
                error_message=f"Invalid data: {str(e)}",
                operation="create"
            ),
            status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        logger.error(f"[DCI Person Create] Unexpected error: {exception_trace(e)}")
        return Response(
            DCIPersonResponseSerializer.create_error_response(
                request_data=request.data,
                error_message=f"Internal server error: {str(e)}",
                operation="create"
            ),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@extend_schema(
    tags=['Registry Core API'],
    summary='Update Person record',
    description='''
    Update an existing Person/Individual record from SPDCI data.

    **person_id format:** `openimis:individual:{uuid}`

    **Partial updates:** Only provided fields will be updated

    **Registry-specific data:** Merged into existing json_ext

    **Permissions:** Requires `individual.change_individual`
    ''',
    request=DCIPersonUpdateRequestSerializer,
    responses={
        200: DCIPersonResponseSerializer,
        400: {'description': 'Invalid request format'},
        403: {'description': 'Permission denied'},
        404: {'description': 'Person not found'},
        500: {'description': 'Internal server error'},
    }
)
@api_view(['PUT'])
@permission_classes([DCIPersonPermissions])
def person_update(request, person_id):
    """
    Update Person/Individual from SPDCI data.

    PUT /api/api_dci/registry/person/{person_id}

    Request format:
    {
        "signature": "",
        "header": {
            "version": "1.0.0",
            "message_id": "msg-update-001",
            "message_ts": "2026-03-23T11:00:00Z",
            "action": "update",
            "sender_id": "external-system",
            "receiver_id": "openimis"
        },
        "message": {
            "transaction_id": "txn-update-001",
            "person": {
                // Partial update - only changed fields
                "name": {"given_name": "John", "family_name": "Doe-Smith"},
                "farm_details": [{...}]
            }
        }
    }
    """
    logger.info(f"[DCI Person Update] Received request from {request.user}")

    # Validate request
    serializer = DCIPersonUpdateRequestSerializer(data=request.data)
    if not serializer.is_valid():
        logger.error(f"[DCI Person Update] Validation error: {error_fields(serializer.errors)}")
        return Response(
            DCIPersonResponseSerializer.create_error_response(
                request_data=request.data,
                error_message=f"Validation error: {serializer.errors}",
                operation="update"
            ),
            status=status.HTTP_400_BAD_REQUEST
        )

    validated_data = serializer.validated_data

    try:
        # Check Individual module availability
        try:
            from individual.models import Individual  # noqa: F401
        except ImportError:
            logger.error("[DCI Person Update] Individual module not available")
            return Response(
                DCIPersonResponseSerializer.create_error_response(
                    request_data=request.data,
                    error_message="Individual module not available",
                    operation="update"
                ),
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        # Parse person_id to get UUID
        uuid = PersonConverter.parse_person_id(person_id)
        if not uuid:
            logger.error("[DCI Person Update] Invalid person_id format")
            return Response(
                DCIPersonResponseSerializer.create_error_response(
                    request_data=request.data,
                    error_message="Invalid person_id format. Expected: openimis:individual:{uuid}",
                    operation="update"
                ),
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get Individual by UUID
        try:
            individual = Individual.objects.get(uuid=uuid, is_deleted=False)
        except Individual.DoesNotExist:
            logger.error(f"[DCI Person Update] Individual not found: {person_id}")
            return Response(
                DCIPersonResponseSerializer.create_error_response(
                    request_data=request.data,
                    error_message=f"Person not found: {person_id}",
                    operation="update"
                ),
                status=status.HTTP_404_NOT_FOUND
            )

        # Extract person data from message
        message_data = validated_data['message']
        person_data = message_data.get('person', {})

        # Get registry type from config
        registry_type = get_registry_type()

        # Get user for audit trail
        user = request.user
        if not isinstance(user, User):
            # Fallback to Admin user
            user = User.objects.filter(username='Admin').first()
            if not user:
                user = User.objects.filter(is_superuser=True).first()

        # Extract metadata
        header_data = validated_data['header']
        metadata = {
            'source_system': header_data.get('sender_id', ''),
            'external_id': person_data.get('identifier', [{}])[0].get('value', '') if person_data.get('identifier') else ''
        }

        # Update Individual from SPDCI data
        individual, updated_json_ext = PersonConverter.update_individual_from_spdci(
            individual=individual,
            spdci_person=person_data,
            registry_type=registry_type,
            user=user,
            metadata=metadata
        )

        logger.info(f"[DCI Person Update] Successfully updated Individual {person_id}")

        # Build success response
        response_data = DCIPersonResponseSerializer.create_success_response(
            request_data=request.data,
            person_id=person_id,
            operation="update"
        )

        return Response(response_data, status=status.HTTP_200_OK)

    except ValueError as e:
        logger.error(f"[DCI Person Update] Invalid data: {type(e).__name__}")
        return Response(
            DCIPersonResponseSerializer.create_error_response(
                request_data=request.data,
                error_message=f"Invalid data: {str(e)}",
                operation="update"
            ),
            status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        logger.error(f"[DCI Person Update] Unexpected error: {exception_trace(e)}")
        return Response(
            DCIPersonResponseSerializer.create_error_response(
                request_data=request.data,
                error_message=f"Internal server error: {str(e)}",
                operation="update"
            ),
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
