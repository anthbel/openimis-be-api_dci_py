"""
DCI API Configuration

Configuration settings for DCI API module.
"""
import os


class DCIConfig:
    """Configuration for DCI API module"""

    @classmethod
    def get_subscribe_individual_signal(cls):
        """
        Check if Individual subscription notifications are enabled.

        Off unless DCI_SUBSCRIBE_INDIVIDUAL_SIGNAL is set to 'true'.

        Returns:
            bool: True if notifications should be sent for Individual events
        """
        return os.environ.get('DCI_SUBSCRIBE_INDIVIDUAL_SIGNAL', 'False').lower() == 'true'

    @classmethod
    def get_notification_timeout_seconds(cls):
        """
        Get HTTP timeout for subscription notifications.

        Returns:
            int: Timeout in seconds (default 30)
        """
        try:
            return int(os.environ.get('DCI_NOTIFICATION_TIMEOUT', '30'))
        except ValueError:
            return 30

    @classmethod
    def get_registry_id(cls):
        """
        Get the sender_id for this registry in notifications.

        Returns:
            str: Registry identifier (default 'openimis')
        """
        return os.environ.get('DCI_REGISTRY_ID', 'openimis')

    @classmethod
    def get_registry_type(cls):
        """
        Get the registry type (FR, SR, IBR).

        Returns:
            str: Registry type from env var SPDCI_REGISTRY_TYPE
        """
        return os.environ.get('SPDCI_REGISTRY_TYPE', 'FR')
