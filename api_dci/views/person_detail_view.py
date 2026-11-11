"""
DCI Person Detail ViewSet

Retrieves a single Person by ID.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes

from ..converters.person_converter import PersonConverter
from ..permissions import DCIPersonPermissions
from ..throttling import DCISearchRateThrottle


@extend_schema(
    tags=['DCI Registry'],
    summary='Get Person by ID',
    description='''
    Retrieve a single Person record by its unique identifier.

    The ID format is: `openimis:individual:{uuid}`

    Example: `openimis:individual:12345678-1234-1234-1234-123456789abc`
    ''',
    parameters=[
        OpenApiParameter(
            name='id',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.PATH,
            description='Person ID in format: openimis:individual:{uuid}',
            required=True,
        )
    ],
    responses={
        200: {
            'description': 'Person found',
            'content': {
                'application/json': {
                    'example': {
                        '@type': 'Person',
                        'id': 'openimis:individual:12345678-1234-1234-1234-123456789abc',
                        'firstName': 'John',
                        'lastName': 'Doe',
                        'dob': '1990-01-15'
                    }
                }
            }
        },
        400: {
            'description': 'Invalid ID format',
            'content': {
                'application/json': {
                    'example': {
                        'error': 'Invalid ID format. Expected: openimis:individual:{uuid}'
                    }
                }
            }
        },
        404: {
            'description': 'Person not found',
            'content': {
                'application/json': {
                    'example': {
                        'error': 'Person not found'
                    }
                }
            }
        },
    }
)
@api_view(['GET'])
@permission_classes([DCIPersonPermissions])
@throttle_classes([DCISearchRateThrottle])
def person_detail(request, person_id):
    """
    Retrieve a Person by ID.

    GET /api/api_dci/reg/person/{id}

    The person_id should be in the format: openimis:individual:{uuid}
    """
    try:
        # Import here to avoid circular imports
        from individual.models import Individual

        # Parse the ID to extract UUID
        # Expected format: openimis:individual:{uuid}
        if not person_id.startswith('openimis:individual:'):
            return Response(
                {
                    'error': 'Invalid ID format. Expected: openimis:individual:{uuid}'
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Extract UUID from the ID
        uuid_str = person_id.replace('openimis:individual:', '')

        # Query the Individual
        try:
            individual = Individual.objects.get(uuid=uuid_str, is_deleted=False)
        except Individual.DoesNotExist:
            return Response(
                {'error': 'Person not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Convert to DCI Person format
        person_data = PersonConverter.individual_to_dci_person(individual)

        return Response(person_data, status=status.HTTP_200_OK)

    except ImportError:
        return Response(
            {
                'error': 'Individual module not available'
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE
        )
    except Exception as e:
        return Response(
            {
                'error': f'Internal server error: {str(e)}'
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
