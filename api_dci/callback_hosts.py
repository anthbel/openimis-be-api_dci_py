"""The callback URLs the async search may post its results to."""
from urllib3.exceptions import LocationParseError
from urllib3.util import parse_url

from .apps import ApiDciConfig


def _unsafe_character(char):
    """A backslash, whitespace or control character: parsers disagree on where such a URL's host ends."""
    return char == "\\" or char.isspace() or ord(char) < 0x20 or ord(char) == 0x7f


def callback_allowed(sender_uri):
    """Whether the async search may post to ``sender_uri``: an https URL without user information whose host,
    as urllib3 (the parser requests connects with) reads it, is one of the module configuration's
    ``callback_allowed_hosts``, compared case-insensitively, on any port. A URL holding a backslash, whitespace
    or a control character is refused. While no host is listed, no callback is allowed."""
    hosts = ApiDciConfig.callback_allowed_hosts
    if not isinstance(sender_uri, str) or not isinstance(hosts, (list, tuple)):
        return False
    if any(_unsafe_character(char) for char in sender_uri):
        return False
    try:
        url = parse_url(sender_uri)
    except LocationParseError:
        return False
    allowed = {host.lower() for host in hosts if isinstance(host, str)}
    return (url.scheme or "").lower() == "https" and url.auth is None and bool(url.host) \
        and url.host.lower() in allowed
