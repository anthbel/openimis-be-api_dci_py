"""
DCI Subscription Filter

Filters active subscriptions based on event type and filter criteria.
"""
import logging
from typing import List

from ..models import DCISubscription
from ..utils.log_safety import exception_trace

logger = logging.getLogger(__name__)


class DCISubscriptionFilter:
    """
    Filters subscriptions based on event type and filter criteria.

    SPDCI FR subscriptions can specify:
    - event_type: REGISTER, UPDATE, DEREGISTER, or ALL
    - filter_criteria: Optional SPDCI query filter (idtype-value, expression, predicate)
    """

    def __init__(self, event_type: str, resource_type: str = 'Person'):
        """
        Args:
            event_type: Event type (REGISTER, UPDATE, DEREGISTER)
            resource_type: Resource type being modified (Person, Farmer, Member)
        """
        self.event_type = event_type
        self.resource_type = resource_type

    def get_filtered_subscriptions(self, resource_data: dict = None) -> List[DCISubscription]:
        """
        Get all active subscriptions matching this event.

        Args:
            resource_data: Optional dict of the resource data for filter matching

        Returns:
            List of matching active subscriptions
        """
        # Get all active, non-expired subscriptions
        base_queryset = DCISubscription.objects.filter(
            is_deleted=False,
            status=DCISubscription.SubscriptionStatus.ACTIVE
        )

        # Filter by event type
        # Subscriptions with event_type=ALL should receive all events
        matching_subscriptions = base_queryset.filter(
            event_type__in=[self.event_type, DCISubscription.EventType.ALL]
        )

        # Filter out expired subscriptions
        from core.datetimes import ad_datetime
        now = ad_datetime.AdDatetime.now()
        valid_subscriptions = [
            sub for sub in matching_subscriptions
            if not sub.expiring or sub.expiring > now
        ]

        # Apply filter criteria if resource_data provided
        if resource_data:
            filtered = []
            for subscription in valid_subscriptions:
                if self._matches_filter_criteria(subscription, resource_data):
                    filtered.append(subscription)
            return filtered

        logger.info(
            f"Found {len(valid_subscriptions)} active subscriptions "
            f"for event_type={self.event_type}"
        )

        return valid_subscriptions

    def _matches_filter_criteria(
        self,
        subscription: DCISubscription,
        resource_data: dict
    ) -> bool:
        """
        Check if resource matches subscription filter criteria.

        SPDCI FR supports filter formats:
        - idtype-value: Match by ID type and value
        - expression: JSONPath-like expression
        - predicate: Complex filter predicate

        Args:
            subscription: Subscription with optional filter_criteria
            resource_data: Resource data to match against

        Returns:
            True if resource matches filter (or no filter specified)
        """
        if not subscription.filter_criteria:
            # No filter means match all
            return True

        try:
            filter_criteria = subscription.filter_criteria

            # Handle idtype-value filter
            if 'idtype-value' in filter_criteria:
                return self._match_idtype_value(
                    filter_criteria['idtype-value'],
                    resource_data
                )

            # Handle expression filter
            if 'expression' in filter_criteria:
                return self._match_expression(
                    filter_criteria['expression'],
                    resource_data
                )

            # Handle predicate filter
            if 'predicate' in filter_criteria:
                return self._match_predicate(
                    filter_criteria['predicate'],
                    resource_data
                )

            # Unknown filter format - be conservative and include it
            logger.warning(
                f"Unknown filter format for subscription {subscription.subscription_code}"
            )
            return True

        except Exception as e:
            logger.error(
                f"Error matching filter for subscription {subscription.subscription_code}: "
                f"{exception_trace(e)}"
            )
            # On error, include the subscription to be safe
            return True

    def _match_idtype_value(self, filter_spec: dict, resource_data: dict) -> bool:
        """
        Match by ID type and value.

        Example filter: {"id_type": "NATIONAL_ID", "id_value": "123456"}

        Args:
            filter_spec: idtype-value filter specification
            resource_data: Resource data

        Returns:
            True if matches
        """
        id_type = filter_spec.get('id_type')
        id_value = filter_spec.get('id_value')

        if not id_type or not id_value:
            return True

        # Check if resource has matching identifier
        identifiers = resource_data.get('identifiers', [])
        for identifier in identifiers:
            if (identifier.get('id_type') == id_type
                    and identifier.get('id_value') == id_value):
                return True

        return False

    def _match_expression(self, expression: str, resource_data: dict) -> bool:
        """
        Match using JSONPath-like expression.

        For now, this is a placeholder. Full implementation would use a JSONPath library.

        Args:
            expression: JSONPath expression
            resource_data: Resource data

        Returns:
            True if matches (currently always True)
        """
        # TODO: Implement JSONPath evaluation
        # For now, match all to avoid excluding subscribers
        logger.debug("Expression filter not fully implemented")
        return True

    def _match_predicate(self, predicate: dict, resource_data: dict) -> bool:
        """
        Match using predicate filter.

        For now, this is a placeholder. Full implementation would evaluate predicates.

        Args:
            predicate: Predicate specification
            resource_data: Resource data

        Returns:
            True if matches (currently always True)
        """
        # TODO: Implement predicate evaluation
        # For now, match all to avoid excluding subscribers
        logger.debug("Predicate filter not fully implemented")
        return True
