"""The callback URLs the async search may post its results to."""
from urllib.parse import urlsplit

from .apps import ApiDciConfig


def callback_allowed(sender_uri):
    """Whether the async search may post to ``sender_uri``: an https URL whose host name is one of the module
    configuration's ``callback_allowed_hosts``, compared case-insensitively, on any port. While no host is listed,
    no callback is allowed."""
    hosts = ApiDciConfig.callback_allowed_hosts
    if not isinstance(sender_uri, str) or not isinstance(hosts, (list, tuple)):
        return False
    try:
        parts = urlsplit(sender_uri)
        parts.port  # a malformed port raises ValueError
    except ValueError:
        return False
    allowed = {host.lower() for host in hosts if isinstance(host, str)}
    return parts.scheme.lower() == "https" and bool(parts.hostname) and parts.hostname in allowed
