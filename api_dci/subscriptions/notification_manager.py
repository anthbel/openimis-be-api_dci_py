"""
DCI Notification Manager

Coordinates notification delivery to subscribers following SPDCI FR notify format.
"""
import logging
import uuid
from datetime import datetime
from typing import List, Dict, Iterable

from core.datetimes import ad_datetime
from ..models import DCISubscription, DCINotificationLog
from ..configurations import DCIConfig
from ..utils.log_safety import exception_trace
from .notification_client import DCINotificationClient, SubscriberNotificationOutput

logger = logging.getLogger(__name__)


class DCINotificationManager:
    """
    Manages SPDCI FR notify notifications to subscribers.

    Creates notify payload in SPDCI format and sends to all matching subscribers.
    Logs all notification attempts to DCINotificationLog.
    """

    def __init__(self, client: DCINotificationClient = None):
        """
        Args:
            client: HTTP notification client (creates default if None)
        """
        if client is None:
            timeout = DCIConfig.get_notification_timeout_seconds()
            client = DCINotificationClient(timeout_seconds=timeout)
        self.client = client
        self.registry_id = DCIConfig.get_registry_id()
        self.registry_type = DCIConfig.get_registry_type()

    def notify_subscribers(
        self,
        event_type: str,
        resource_type: str,
        resource_record: dict,
        subscriptions: List[DCISubscription],
    ) -> Iterable[DCINotificationLog]:
        """
        Notify all subscribers about a resource change event.

        Args:
            event_type: REGISTER, UPDATE, or DEREGISTER
            resource_type: Person, Farmer, or Member
            resource_record: The full resource record dict
            subscriptions: List of subscriptions to notify

        Returns:
            List of notification log entries
        """
        if not subscriptions:
            logger.debug(f"No subscriptions to notify for {event_type} {resource_type}")
            return []

        # Filter out subscriptions without sender_uri
        valid_subscriptions = [
            sub for sub in subscriptions
            if sub.sender_uri
        ]

        if not valid_subscriptions:
            logger.warning(
                f"No valid subscriptions with sender_uri for {event_type} {resource_type}"
            )
            return []

        logger.info(
            f"Notifying {len(valid_subscriptions)} subscribers about "
            f"{event_type} for {resource_type}"
        )

        # Send notifications to each subscription individually
        # (Each subscriber gets their own notify message with their subscription_id)
        notification_logs = []

        for subscription in valid_subscriptions:
            try:
                # Build SPDCI notify payload for this specific subscriber
                notify_payload = self._build_notify_payload(
                    event_type=event_type,
                    resource_type=resource_type,
                    resource_record=resource_record,
                    subscription=subscription
                )

                # Send notification
                results = self.client.propagate_notifications(
                    notification_payload=notify_payload,
                    subscribers=[subscription]  # Send to one subscriber at a time
                )

                # Save results to database
                for result in results:
                    log_entry = self._save_notification_log(
                        result=result,
                        event_type=event_type,
                        resource_type=resource_type,
                        resource_id=resource_record.get('id', 'unknown')
                    )
                    notification_logs.append(log_entry)

            except Exception as e:
                logger.error(
                    f"Failed to notify subscription {subscription.subscription_code}: "
                    f"{exception_trace(e)}"
                )
                # Log the failure
                log_entry = self._save_notification_log(
                    result=SubscriberNotificationOutput(
                        subscription=subscription,
                        notification_success=False,
                        reason_of_failure=str(e)
                    ),
                    event_type=event_type,
                    resource_type=resource_type,
                    resource_id=resource_record.get('id', 'unknown')
                )
                notification_logs.append(log_entry)

        return notification_logs

    def _build_notify_payload(
        self,
        event_type: str,
        resource_type: str,
        resource_record: dict,
        subscription: DCISubscription
    ) -> Dict:
        """
        Build SPDCI FR notify request payload.

        Format:
        {
            "signature": "",
            "header": {
                "version": "1.0.0",
                "message_id": "msg-notify-...",
                "message_ts": "2026-03-17T10:00:00Z",
                "action": "notify",
                "sender_id": "openimis",
                "receiver_id": "{subscription.sender_id}",
                "is_msg_encrypted": false
            },
            "message": {
                "transaction_id": "txn-notify-...",
                "correlation_id": "{subscription.transaction_id}",
                "notify_request": [
                    {
                        "reference_id": "ref-...",
                        "timestamp": "...",
                        "subscription_id": "{subscription.subscription_code}",
                        "data": {
                            "version": "1.0.0",
                            "reg_type": "FR",
                            "reg_record_type": "Person",
                            "event_type": "REGISTER",
                            "reg_record": {...}
                        }
                    }
                ]
            }
        }

        Args:
            event_type: REGISTER, UPDATE, DEREGISTER
            resource_type: Person, Farmer, Member
            resource_record: Resource data dict
            subscription: Subscription receiving the notification

        Returns:
            SPDCI notify payload dict
        """
        message_id = f"msg-notify-{uuid.uuid4().hex[:16]}"
        transaction_id = f"txn-notify-{uuid.uuid4().hex[:16]}"
        reference_id = f"ref-{uuid.uuid4().hex[:8]}"
        timestamp = datetime.utcnow().isoformat() + 'Z'

        return {
            "signature": "",
            "header": {
                "version": "1.0.0",
                "message_id": message_id,
                "message_ts": timestamp,
                "action": "notify",
                "sender_id": self.registry_id,  # This registry's ID from config
                "receiver_id": subscription.sender_id,  # Subscriber's ID
                "is_msg_encrypted": False
            },
            "message": {
                "transaction_id": transaction_id,
                "correlation_id": subscription.transaction_id or transaction_id,
                "notify_request": [
                    {
                        "reference_id": reference_id,
                        "timestamp": timestamp,
                        "subscription_id": subscription.subscription_code,
                        "data": {
                            "version": "1.0.0",
                            "reg_type": self.registry_type,  # FR/SR/IBR from config
                            "reg_record_type": resource_type,
                            "event_type": event_type,
                            "reg_record": resource_record
                        }
                    }
                ]
            }
        }

    def _save_notification_log(
        self,
        result: SubscriberNotificationOutput,
        event_type: str,
        resource_type: str,
        resource_id: str
    ) -> DCINotificationLog:
        """
        Save notification result to database log.

        Args:
            result: Notification result from client
            event_type: Event type
            resource_type: Resource type
            resource_id: Resource ID

        Returns:
            Saved log entry
        """
        correlation_id = f"notify-{uuid.uuid4().hex[:12]}"

        log_entry = DCINotificationLog(
            subscription=result.subscription,
            event_type=event_type,
            resource_type=resource_type,
            resource_id=str(resource_id),
            notified_successfully=result.notification_success,
            notification_time=ad_datetime.AdDatetime.now(),
            http_status_code=result.http_status_code,
            error=str(result.reason_of_failure) if result.reason_of_failure else None,
            correlation_id=correlation_id
        )

        try:
            log_entry.save()
            logger.debug(
                f"Logged notification to {result.subscription.subscription_code}: "
                f"success={result.notification_success}"
            )
        except Exception as e:
            logger.error(f"Failed to save notification log: {exception_trace(e)}")

        return log_entry
