"""
DCI Subscription ViewSet

REST API views for SPDCI subscribe/unsubscribe operations.
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from django.db.models import F
import uuid
import logging

from core.datetimes import ad_datetime
from core.models import User
from ..serializers import (
    DCISubscribeRequestSerializer,
    DCISubscribeResponseSerializer,
    DCIUnsubscribeRequestSerializer,
    DCIUnsubscribeResponseSerializer,
)
from ..permissions import DCIPersonPermissions
from ..models import DCISubscription
from ..utils.log_safety import error_fields, exception_trace

logger = logging.getLogger(__name__)


@extend_schema(
    tags=['DCI Registry'],
    summary='Subscribe to registry events',
    description='''
    Subscribe to registry change events following the SPDCI standard.

    **SPDCI FR/SR/IBR Compliant**

    Supported event types:
    - REGISTER: New person/farmer/member registered
    - UPDATE: Existing record updated
    - DEREGISTER: Record deactivated/deleted
    - ALL: All event types (default)

    The subscriber will receive notifications via HTTP POST to the sender_uri
    provided in the request header.
    ''',
    request=DCISubscribeRequestSerializer,
    responses={
        200: DCISubscribeResponseSerializer,
    }
)
@api_view(['POST'])
@permission_classes([DCIPersonPermissions])
def subscribe(request):
    """
    DCI subscribe endpoint for event subscriptions.

    POST /api/api_dci/registry/subscribe

    Creates one or more event subscriptions and returns subscription codes
    that can be used to unsubscribe later.
    """
    logger.info(f"[DCI Subscribe] Received request from {request.user}")

    # Validate request
    serializer = DCISubscribeRequestSerializer(data=request.data)
    if not serializer.is_valid():
        logger.error(f"[DCI Subscribe] Validation errors: {error_fields(serializer.errors)}")
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
        header_data = validated_data['header']
        message_data = validated_data['message']
        transaction_id = message_data['transaction_id']
        subscribe_requests = message_data.get('subscribe_request', [])

        if not subscribe_requests:
            raise ValueError("No subscribe_request provided")

        # Extract sender information from header
        sender_id = header_data.get('sender_id')
        sender_uri = header_data.get('sender_uri', '')  # Optional in SPDCI spec

        if not sender_id:
            raise ValueError("sender_id is required in header")

        # Process each subscription request
        subscription_codes = []

        for sub_request in subscribe_requests:
            reference_id = sub_request.get('reference_id', '')
            filter_criteria = sub_request.get('filter')
            event_type = sub_request.get('event_type', 'ALL')
            expiry = sub_request.get('expiry')

            # Generate unique subscription code
            subscription_code = f"sub-{uuid.uuid4().hex[:16]}"

            # Create subscription
            subscription = DCISubscription()
            subscription.subscription_code = subscription_code
            subscription.sender_id = sender_id
            subscription.sender_uri = sender_uri
            subscription.event_type = event_type
            subscription.filter_criteria = filter_criteria
            subscription.status = DCISubscription.SubscriptionStatus.ACTIVE
            subscription.expiring = expiry
            subscription.transaction_id = transaction_id
            subscription.reference_id = reference_id
            subscription.save(username=request.user.username if hasattr(request.user, 'username') else 'Admin')

            logger.info(
                f"[DCI Subscribe] Created subscription {subscription_code} "
                f"for transaction {transaction_id} (event_type={event_type})"
            )

            subscription_codes.append((reference_id, subscription_code))

    except ValueError as e:
        logger.error(f"[DCI Subscribe] Value error: {type(e).__name__}")
        return Response(
            {
                'header': {
                    'status': 'error',
                    'message': str(e)
                }
            },
            status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        logger.error(f"[DCI Subscribe] Unexpected error: {exception_trace(e)}")
        return Response(
            {
                'header': {
                    'status': 'error',
                    'message': f'Internal server error: {str(e)}'
                }
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    # Build ACK response (SPDCI FR format)
    response_data = DCISubscribeResponseSerializer.create_ack_response(
        request_data=request.data,
        transaction_id=transaction_id
    )

    logger.info(
        f"[DCI Subscribe] Successfully created {len(subscription_codes)} subscriptions "
        f"for transaction {transaction_id}, returning ACK"
    )

    return Response(response_data, status=status.HTTP_200_OK)


@extend_schema(
    tags=['DCI Registry'],
    summary='Unsubscribe from registry events',
    description='''
    Unsubscribe from registry change events following the SPDCI standard.

    **SPDCI FR/SR/IBR Compliant**

    Cancels one or more active subscriptions using the subscription codes
    returned from the subscribe endpoint.
    ''',
    request=DCIUnsubscribeRequestSerializer,
    responses={
        200: DCIUnsubscribeResponseSerializer,
    }
)
@api_view(['POST'])
@permission_classes([DCIPersonPermissions])
def unsubscribe(request):
    """
    DCI unsubscribe endpoint for canceling event subscriptions.

    POST /api/api_dci/registry/unsubscribe

    Deactivates one or more subscriptions by subscription code.
    """
    logger.info(f"[DCI Unsubscribe] Received request from {request.user}")

    # Validate request
    serializer = DCIUnsubscribeRequestSerializer(data=request.data)
    if not serializer.is_valid():
        logger.error(f"[DCI Unsubscribe] Validation errors: {error_fields(serializer.errors)}")
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
        unsubscribe_requests = message_data.get('unsubscribe_request', [])

        # Process each unsubscribe request (if any provided)
        unsubscribe_results = []

        for unsub_request in unsubscribe_requests:
            reference_id = unsub_request.get('reference_id', '')
            subscription_id = unsub_request.get('subscription_id')

            try:
                # Check if subscription exists and get current status
                subscription = DCISubscription.objects.filter(
                    subscription_code=subscription_id,
                    is_deleted=False
                ).first()

                if not subscription:
                    logger.warning(
                        f"[DCI Unsubscribe] Subscription not found (transaction {transaction_id})"
                    )
                    unsubscribe_results.append((
                        reference_id,
                        False,
                        f"Subscription {subscription_id} not found"
                    ))
                    continue

                # Check if already inactive
                if subscription.status == DCISubscription.SubscriptionStatus.INACTIVE:
                    unsubscribe_results.append((
                        reference_id,
                        True,
                        f"Subscription {subscription_id} already inactive"
                    ))
                    logger.warning(
                        f"[DCI Unsubscribe] Subscription {subscription.subscription_code} already inactive"
                    )
                    continue

                # Deactivate subscription using QuerySet.update() for atomic operation
                # Note: Using .update() instead of .save() to avoid HistoryModel cache issues
                user = User.objects.filter(
                    username=request.user.username if hasattr(request.user, 'username') else 'Admin'
                ).first()
                if not user:
                    user = User.objects.filter(i_user_id=1).first()

                updated_count = DCISubscription.objects.filter(
                    subscription_code=subscription_id,
                    is_deleted=False,
                    status=DCISubscription.SubscriptionStatus.ACTIVE  # Only update if still active
                ).update(
                    status=DCISubscription.SubscriptionStatus.INACTIVE,
                    version=F('version') + 1,
                    date_updated=ad_datetime.AdDatetime.now(),
                    user_updated=user
                )

                if updated_count > 0:
                    logger.info(
                        f"[DCI Unsubscribe] Deactivated subscription {subscription.subscription_code}"
                    )
                else:
                    logger.warning(
                        f"[DCI Unsubscribe] Subscription {subscription.subscription_code} was not updated "
                        f"(possibly already inactive)"
                    )

                unsubscribe_results.append((
                    reference_id,
                    True,
                    f"Subscription {subscription_id} successfully unsubscribed"
                ))

            except Exception as e:
                logger.error(
                    f"[DCI Unsubscribe] Error processing a subscription of transaction "
                    f"{transaction_id}: {exception_trace(e)}"
                )
                unsubscribe_results.append((
                    reference_id,
                    False,
                    f"Error unsubscribing {subscription_id}: {str(e)}"
                ))

    except ValueError as e:
        logger.error(f"[DCI Unsubscribe] Value error: {type(e).__name__}")
        return Response(
            {
                'header': {
                    'status': 'error',
                    'message': str(e)
                }
            },
            status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        logger.error(f"[DCI Unsubscribe] Unexpected error: {exception_trace(e)}")
        return Response(
            {
                'header': {
                    'status': 'error',
                    'message': f'Internal server error: {str(e)}'
                }
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    # Build ACK response (SPDCI FR format)
    response_data = DCIUnsubscribeResponseSerializer.create_ack_response(
        request_data=request.data,
        transaction_id=transaction_id
    )

    logger.info(
        f"[DCI Unsubscribe] Processed {len(unsubscribe_results)} unsubscribe requests "
        f"for transaction {transaction_id}, returning ACK"
    )

    return Response(response_data, status=status.HTTP_200_OK)
