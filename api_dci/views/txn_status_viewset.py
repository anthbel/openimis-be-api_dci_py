"""
DCI Transaction Status ViewSet

REST API view for SPDCI async transaction status check operation.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
import logging

from ..serializers import DCITxnStatusRequestSerializer, DCITxnStatusResponseSerializer
from ..permissions import DCIPersonPermissions
from ..utils.log_safety import error_fields

logger = logging.getLogger(__name__)


@extend_schema(
    tags=['DCI Registry'],
    summary='Check transaction status',
    description='''
    Check the status of a previously submitted async transaction following the SPDCI standard.

    **SPDCI FR Compliant**

    Allows callers to query the processing status of an async search or other
    async operation by transaction ID.
    ''',
    request=DCITxnStatusRequestSerializer,
    responses={200: DCITxnStatusResponseSerializer},
)
@api_view(['POST'])
@permission_classes([DCIPersonPermissions])
def txn_status(request):
    """
    DCI transaction status endpoint.

    POST /api/api_dci/registry/txn/status
    """
    logger.info(f"[DCI TxnStatus] Received request from {request.user}")

    serializer = DCITxnStatusRequestSerializer(data=request.data)
    if not serializer.is_valid():
        logger.error(f"[DCI TxnStatus] Validation errors: {error_fields(serializer.errors)}")
        return Response(
            {
                'header': {'status': 'error', 'message': 'Invalid request format'},
                'errors': serializer.errors,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    transaction_id = serializer.validated_data['message']['transaction_id']

    response_data = DCITxnStatusResponseSerializer.create_ack_response(
        request_data=request.data,
        transaction_id=transaction_id,
    )

    logger.info(f"[DCI TxnStatus] Returning ACK for transaction {transaction_id}")
    return Response(response_data, status=status.HTTP_200_OK)
