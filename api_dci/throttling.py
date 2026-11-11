"""
Rate limit of the DCI registry reads, per authenticated account.

The rate comes from the Django setting API_DCI_SEARCH_THROTTLE_RATE, else from the
environment variable of the same name, else DEFAULT_RATE. It is written as DRF writes
rates ("<count>/<s|m|h|d>"); "none" or an empty value turns the throttle off. The counts
live in Django's default cache.
"""
import os

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework.throttling import UserRateThrottle

RATE_SETTING = "API_DCI_SEARCH_THROTTLE_RATE"
DEFAULT_RATE = "120/min"


def search_throttle_rate():
    """The configured rate, or None when the throttle is off."""
    rate = getattr(settings, RATE_SETTING, None)
    if rate is None:
        rate = os.environ.get(RATE_SETTING, DEFAULT_RATE)
    rate = str(rate).strip()
    return None if rate.lower() in ("", "none") else rate


class DCISearchRateThrottle(UserRateThrottle):
    """One request budget per account, shared by every view that carries this throttle."""

    scope = "dci_search"

    def get_rate(self):
        return search_throttle_rate()


def check_search_throttle_rate():
    """Raise ImproperlyConfigured unless the configured rate is a valid rate or "none"."""
    try:
        num_requests = DCISearchRateThrottle().num_requests
    except (ValueError, KeyError, IndexError) as exc:
        raise ImproperlyConfigured(
            f'{RATE_SETTING} must be "<count>/<s|m|h|d>" or "none"'
        ) from exc
    if num_requests is not None and num_requests < 0:
        raise ImproperlyConfigured(f"{RATE_SETTING} must not allow a negative count")
