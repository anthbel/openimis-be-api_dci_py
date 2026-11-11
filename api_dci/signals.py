"""
DCI Signal Handlers

Binds Individual model events to SPDCI subscription notifications.
"""
import logging

from core.service_signals import ServiceSignalBindType
from core.signals import bind_service_signal
from openIMIS.openimisapps import openimis_apps

from .subscriptions import DCINotificationManager, DCISubscriptionFilter
from .configurations import DCIConfig
from .utils.log_safety import exception_trace

logger = logging.getLogger(__name__)
imis_modules = openimis_apps()


def bind_service_signals():
    """
    Bind service signals for Individual model events.

    Listens to individual_service create/update signals and notifies subscribers.
    """
    if "individual" not in imis_modules:
        logger.info("Individual module not available, skipping DCI signal binding")
        return

    if not DCIConfig.get_subscribe_individual_signal():
        logger.info("DCI Individual subscription signals disabled via config")
        return

    logger.info("Binding DCI subscription signals for Individual module")

    def on_individual_create(**kwargs):
        """Handle Individual creation event (REGISTER)"""
        try:
            result = kwargs.get("result", None)
            if not result:
                return

            # Extract Individual from result
            # For create_or_update, result is the Individual instance
            individual = result

            if not hasattr(individual, 'id'):
                logger.warning("Individual result has no ID, skipping notification")
                return

            logger.info(f"Processing DCI REGISTER event for Individual {individual.id}")

            # Get matching subscriptions
            subscription_filter = DCISubscriptionFilter(
                event_type='REGISTER',
                resource_type='Person'
            )
            subscriptions = subscription_filter.get_filtered_subscriptions(
                resource_data=_individual_to_dict(individual)
            )

            if not subscriptions:
                logger.debug(f"No subscriptions for REGISTER Individual {individual.id}")
                return

            # Notify subscribers
            notification_manager = DCINotificationManager()
            notification_manager.notify_subscribers(
                event_type='REGISTER',
                resource_type='Person',
                resource_record=_individual_to_dict(individual),
                subscriptions=subscriptions
            )

            logger.info(
                f"Notified {len(subscriptions)} subscribers about "
                f"REGISTER Individual {individual.id}"
            )

        except Exception as e:
            logger.error(
                f"Error processing Individual REGISTER notification: {exception_trace(e)}"
            )

    def on_individual_update(**kwargs):
        """Handle Individual update event (UPDATE)"""
        try:
            result = kwargs.get("result", None)
            if not result:
                return

            individual = result

            if not hasattr(individual, 'id'):
                logger.warning("Individual result has no ID, skipping notification")
                return

            logger.info(f"Processing DCI UPDATE event for Individual {individual.id}")

            # Get matching subscriptions
            subscription_filter = DCISubscriptionFilter(
                event_type='UPDATE',
                resource_type='Person'
            )
            subscriptions = subscription_filter.get_filtered_subscriptions(
                resource_data=_individual_to_dict(individual)
            )

            if not subscriptions:
                logger.debug(f"No subscriptions for UPDATE Individual {individual.id}")
                return

            # Notify subscribers
            notification_manager = DCINotificationManager()
            notification_manager.notify_subscribers(
                event_type='UPDATE',
                resource_type='Person',
                resource_record=_individual_to_dict(individual),
                subscriptions=subscriptions
            )

            logger.info(
                f"Notified {len(subscriptions)} subscribers about "
                f"UPDATE Individual {individual.id}"
            )

        except Exception as e:
            logger.error(
                f"Error processing Individual UPDATE notification: {exception_trace(e)}"
            )

    def on_individual_delete(**kwargs):
        """Handle Individual deletion event (DEREGISTER)"""
        try:
            result = kwargs.get("result", None)
            if not result:
                return

            individual = result

            if not hasattr(individual, 'id'):
                logger.warning("Individual result has no ID, skipping notification")
                return

            logger.info(f"Processing DCI DEREGISTER event for Individual {individual.id}")

            # Get matching subscriptions
            subscription_filter = DCISubscriptionFilter(
                event_type='DEREGISTER',
                resource_type='Person'
            )
            subscriptions = subscription_filter.get_filtered_subscriptions(
                resource_data=_individual_to_dict(individual)
            )

            if not subscriptions:
                logger.debug(f"No subscriptions for DEREGISTER Individual {individual.id}")
                return

            # Notify subscribers
            notification_manager = DCINotificationManager()
            notification_manager.notify_subscribers(
                event_type='DEREGISTER',
                resource_type='Person',
                resource_record=_individual_to_dict(individual),
                subscriptions=subscriptions
            )

            logger.info(
                f"Notified {len(subscriptions)} subscribers about "
                f"DEREGISTER Individual {individual.id}"
            )

        except Exception as e:
            logger.error(
                f"Error processing Individual DEREGISTER notification: {exception_trace(e)}"
            )

    # Bind signals
    # Note: Individual service uses 'create_or_update' for both create and update
    # We'll treat first save as REGISTER and subsequent as UPDATE
    bind_service_signal(
        'individual_service.create_or_update',
        on_individual_update,  # Handles both create and update
        bind_type=ServiceSignalBindType.AFTER
    )

    bind_service_signal(
        'individual_service.delete',
        on_individual_delete,
        bind_type=ServiceSignalBindType.AFTER
    )

    logger.info("DCI subscription signals bound successfully")


def _individual_to_dict(individual) -> dict:
    """
    Convert Individual model to SPDCI Person/Farmer record dict.

    Args:
        individual: Individual model instance

    Returns:
        Dict representation for SPDCI notify
    """
    try:
        # Basic fields
        record = {
            'id': str(individual.id),
            'first_name': individual.first_name,
            'last_name': individual.last_name,
            'dob': individual.dob.isoformat() if individual.dob else None,
        }

        # Add JSON extension data if available
        if hasattr(individual, 'json_ext') and individual.json_ext:
            record['json_ext'] = individual.json_ext

        # Add identifiers if available
        # Note: This depends on Individual model structure
        # Adjust based on actual model fields

        return record

    except Exception as e:
        logger.error(f"Error converting Individual to dict: {exception_trace(e)}")
        return {
            'id': str(getattr(individual, 'id', 'unknown')),
            'error': 'Failed to serialize Individual'
        }
