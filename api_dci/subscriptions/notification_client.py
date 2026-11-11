"""
DCI Notification Client

Handles async HTTP notifications to subscribers following SPDCI notify format.
"""
import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import List, Dict, Any, Iterable

import aiohttp
import orjson

from ..models import DCISubscription
from ..utils.log_safety import exception_trace

logger = logging.getLogger(__name__)


@dataclass
class SubscriberNotificationOutput:
    """Result of a notification attempt to a subscriber"""
    subscription: DCISubscription
    notification_success: bool
    http_status_code: int = None
    reason_of_failure: Any = None


class DCINotificationClient:
    """
    Async HTTP client for sending SPDCI notify requests to subscribers.

    Sends notifications in SPDCI FR notify format:
    {
        "signature": "",
        "header": {...},
        "message": {
            "transaction_id": "...",
            "correlation_id": "...",
            "notify_request": [...]
        }
    }
    """

    def __init__(self, timeout_seconds: int = 30):
        """
        Args:
            timeout_seconds: HTTP request timeout in seconds
        """
        self.timeout_seconds = timeout_seconds

    def propagate_notifications(
        self,
        notification_payload: Dict,
        subscribers: List[DCISubscription],
    ) -> Iterable[SubscriberNotificationOutput]:
        """
        Send notifications to all subscribers synchronously (creates event loop).

        Args:
            notification_payload: SPDCI notify message payload
            subscribers: List of active subscriptions to notify

        Returns:
            List of notification results
        """
        return asyncio.run(
            self.propagate_notifications_async(notification_payload, subscribers)
        )

    async def propagate_notifications_async(
        self,
        notification_payload: Dict,
        subscribers: List[DCISubscription]
    ) -> Iterable[SubscriberNotificationOutput]:
        """
        Send notifications to all subscribers asynchronously.

        Args:
            notification_payload: SPDCI notify message payload
            subscribers: List of active subscriptions to notify

        Returns:
            List of notification results
        """
        payload_bytes = self._serialize_payload(notification_payload)

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout_seconds)
        ) as session:
            tasks = []
            for subscription in subscribers:
                task = asyncio.ensure_future(
                    self._send_notification_async(payload_bytes, subscription, session)
                )
                tasks.append(task)

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Handle any exceptions that occurred
            final_results = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(
                        f"Notification to {subscribers[i].subscription_code} failed with exception: "
                        f"{exception_trace(result)}"
                    )
                    final_results.append(
                        SubscriberNotificationOutput(
                            subscription=subscribers[i],
                            notification_success=False,
                            reason_of_failure=str(result)
                        )
                    )
                else:
                    final_results.append(result)

            return final_results

    async def _send_notification_async(
        self,
        payload_bytes: bytes,
        subscription: DCISubscription,
        session: aiohttp.ClientSession,
    ) -> SubscriberNotificationOutput:
        """
        Send notification to a single subscriber.

        Args:
            payload_bytes: Serialized SPDCI notify payload
            subscription: Subscription to notify
            session: aiohttp client session

        Returns:
            Notification result
        """
        if not subscription.sender_uri:
            logger.warning(
                f"Subscription {subscription.subscription_code} has no sender_uri, skipping notification"
            )
            return SubscriberNotificationOutput(
                subscription=subscription,
                notification_success=False,
                reason_of_failure="No sender_uri configured"
            )

        try:
            headers = self._prepare_headers(subscription)

            logger.info(
                f"Sending DCI notification for subscription {subscription.subscription_code}"
            )

            async with session.post(
                url=subscription.sender_uri,
                headers=headers,
                data=payload_bytes
            ) as response:
                status_code = response.status

                # Try to read response body
                try:
                    response_body = await response.json()
                except Exception:
                    response_body = await response.text()

                if status_code >= 400:
                    logger.error(
                        f"Notification to {subscription.subscription_code} failed "
                        f"with status {status_code}"
                    )
                    return SubscriberNotificationOutput(
                        subscription=subscription,
                        notification_success=False,
                        http_status_code=status_code,
                        reason_of_failure=f"HTTP {status_code}: {response_body}"
                    )
                else:
                    logger.info(
                        f"Successfully notified {subscription.subscription_code} "
                        f"(status {status_code})"
                    )
                    return SubscriberNotificationOutput(
                        subscription=subscription,
                        notification_success=True,
                        http_status_code=status_code
                    )

        except asyncio.TimeoutError:
            logger.error(
                f"Notification to {subscription.subscription_code} timed out after {self.timeout_seconds}s"
            )
            return SubscriberNotificationOutput(
                subscription=subscription,
                notification_success=False,
                reason_of_failure=f"Timeout after {self.timeout_seconds}s"
            )

        except Exception as e:
            logger.error(
                f"Notification to {subscription.subscription_code} failed: {exception_trace(e)}"
            )
            return SubscriberNotificationOutput(
                subscription=subscription,
                notification_success=False,
                reason_of_failure=str(e)
            )

    def _prepare_headers(self, subscription: DCISubscription) -> Dict[str, str]:
        """
        Prepare HTTP headers for notification request.

        Args:
            subscription: Subscription with optional custom headers

        Returns:
            Dict of HTTP headers
        """
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

        # Add custom headers from subscription if configured
        if subscription.callback_headers:
            try:
                import json
                custom_headers = json.loads(subscription.callback_headers)
                headers.update(custom_headers)
            except Exception as e:
                logger.warning(
                    f"Failed to parse callback_headers for {subscription.subscription_code}: {e}"
                )

        return headers

    @staticmethod
    def _serialize_payload(payload: Dict) -> bytes:
        """
        Serialize payload to JSON bytes.

        Args:
            payload: Dict to serialize

        Returns:
            JSON bytes
        """
        def uuid_convert(o):
            """Handle UUID and Decimal serialization"""
            if isinstance(o, uuid.UUID):
                return str(o)
            import decimal
            if isinstance(o, decimal.Decimal):
                return float(o)
            raise TypeError(f"Object of type {type(o)} is not JSON serializable")

        return orjson.dumps(payload, default=uuid_convert)
