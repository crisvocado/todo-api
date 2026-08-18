"""Structured JSON logging to stdout for logcore.

Cloud Logging captures stdout from the running service and the logcore sink
forwards it, so this path needs no network call and no API key.

Cloud Run promotes ONLY `logging.googleapis.com/*` keys out of a structured
line; anything else stays inside jsonPayload, where logcore does not look for
labels. So env/source_project/trace_id live under the labels key and the insert
id under insertId — a top-level `env` would silently record every issue from
this service as env="unknown".
"""

import datetime
import hashlib
import json
import os
import re
import sys
import traceback


DEFAULT_SERVICE = "todo-api"

_SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
_SOURCE_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_ENVS = {"prod", "staging", "dev", "test", "local"}

_warned_missing_service_id = False


def _service_name():
    name = os.environ.get("LOGCORE_SERVICE", DEFAULT_SERVICE)
    return name if _SERVICE_RE.match(name) else DEFAULT_SERVICE


def _env():
    env = os.environ.get("LOGCORE_ENV", "dev")
    return env if env in _ENVS else "dev"


def _source_project():
    project = (
        os.environ.get("LOGCORE_SOURCE_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or ""
    )
    # Validated as a GCP project id whenever present, so an unset or malformed
    # value is dropped rather than sent as an empty string.
    return project if _SOURCE_PROJECT_RE.match(project) else None


def _insert_id(timestamp, service, message, stack_trace):
    # Deterministic, so a redelivery of the same event dedups instead of
    # opening a second issue. Truncated to 32: the schema rejects a full digest.
    raw = f"{timestamp}{service}{message}{stack_trace}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def error_payload(exc):
    """Shape an exception for the entry's `error` field."""
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "stack_trace": "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ),
    }


def build_entry(
    severity,
    message,
    error=None,
    context=None,
    fingerprint=None,
    trace_id=None,
    service_id=None,
):
    """Build one logcore wire entry, or None when no service id is configured.

    Pure — no I/O — so the entry can be inspected without a transport.
    """
    # The platform issues this id per service; the service NAME is not unique
    # across customers, so logcore discards a sink-delivered log without it.
    # Read from the environment: a re-created service gets a new id, which
    # belongs in the deploy config rather than in committed code.
    service_id = service_id or os.environ.get("LOGCORE_SERVICE_ID", "")
    if not service_id:
        return None

    timestamp = (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    service = _service_name()
    stack_trace = (error or {}).get("stack_trace", "")

    labels = {"env": _env()}
    source_project = _source_project()
    if source_project:
        labels["source_project"] = source_project
    if trace_id:
        labels["trace_id"] = trace_id

    entry = {
        "timestamp": timestamp,
        "severity": severity,
        "message": message,
        "service": service,
        "service_id": service_id,
        "logging.googleapis.com/labels": labels,
        "logging.googleapis.com/insertId": _insert_id(
            timestamp, service, message, stack_trace
        ),
    }
    if error:
        entry["error"] = error
    if context:
        entry["context"] = context
    if fingerprint:
        entry["fingerprint"] = fingerprint
    return entry


def _warn_missing_service_id():
    global _warned_missing_service_id
    if not _warned_missing_service_id:
        _warned_missing_service_id = True
        # stderr, never stdout: a plain-text line in the stdout stream would
        # sit between JSON objects the log collector is parsing.
        print(
            "logcore: LOGCORE_SERVICE_ID is not set, logs are not being reported",
            file=sys.stderr,
        )


def emit(severity, message, stream=None, **fields):
    """Write one JSON line and return the entry written (None if nothing was).

    Never raises: a log that cannot be delivered must not break the request it
    describes.
    """
    try:
        entry = build_entry(severity, message, **fields)
        if entry is None:
            _warn_missing_service_id()
            return None
        out = sys.stdout if stream is None else stream
        # One write per entry: concurrent requests must not interleave partial
        # writes inside a single JSON object.
        out.write(json.dumps(entry, default=str) + "\n")
        out.flush()
        return entry
    except Exception:
        return None
