"""
What the api_dci logs may say about a request or a record: its shape, never its content.

Serializer messages and exception messages can quote the values they reject, so neither is
written to a log; the field paths, error codes, exception class and stack frames are.
"""
import traceback
import uuid


def error_fields(errors):
    """Each field a DRF `errors` structure rejects, as "path: codes", without the messages."""
    return ", ".join(_error_paths(errors, ""))


def _error_paths(errors, path):
    if isinstance(errors, dict):
        return [
            line
            for key, value in errors.items()
            for line in _error_paths(value, f"{path}.{key}" if path else str(key))
        ]
    if isinstance(errors, list) and any(isinstance(item, (dict, list)) for item in errors):
        return [
            line
            for index, item in enumerate(errors)
            for line in _error_paths(item, f"{path}[{index}]")
        ]
    items = errors if isinstance(errors, list) else [errors]
    codes = ",".join(sorted({str(getattr(item, "code", None) or "invalid") for item in items}))
    return [f"{path or 'non_field_errors'}: {codes}"]


def exception_trace(exc):
    """The class of `exc` and the stack frames it went through, without its message."""
    frames = "".join(traceback.format_tb(exc.__traceback__))
    return f"{type(exc).__name__}\nTraceback (most recent call last):\n{frames}".rstrip()


def log_error(log, context, exc):
    """Logs `exc` under a new error id, by its class and stack frames, and returns the id. A response
    gives the caller this id, never the exception's message, which can quote stored values."""
    error_id = uuid.uuid4().hex
    log.error("%s [error id %s]: %s", context, error_id, exception_trace(exc))
    return error_id


def error_text(kind, error_id):
    """The message a response gives for a failure logged under `error_id`."""
    return f"{kind}; error id {error_id}"
