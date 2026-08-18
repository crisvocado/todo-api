"""Structured JSON logging to stdout for logcore.

Cloud Logging captures every line written here and the log sink forwards it to
logcore. Nothing in this module may raise: a log that cannot be produced is
worth less than the request it would have broken.
"""

import datetime
import hashlib
import json
import os
import sys
import traceback


SERVICE_NAME = "todo-api"

VALID_ENVS = ("prod", "staging", "dev", "test", "local")

# Frames coming from these paths belong to dependencies, not to this service.
LIB_MARKERS = ("site-packages", "dist-packages", "/usr/lib/python")

_MISSING_SERVICE_ID_REPORTED = False


def _frames_from_traceback(tb):
    # extract_tb returns the outermost frame first. logcore takes the first
    # in-app frame as the location an issue groups on, so the throw site has to
    # come first — otherwise every error in the service groups on uvicorn.
    return [
        {
            "function": frame.name,
            "file": frame.filename,
            "line": frame.lineno or 0,
            "inApp": not any(marker in frame.filename for marker in LIB_MARKERS),
        }
        for frame in reversed(traceback.extract_tb(tb))
    ]


def error_from_exception(exc):
    """Build the `error` object from a caught exception.

    `stack` is a list of parsed frames; logcore rejects the raw traceback
    string. The exception chain is followed to its root because a wrapped
    exception's own traceback stops at the `raise`, and the line that actually
    broke is not in it.
    """
    chain = []
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        # __cause__ is `raise X from Y`; __context__ is an exception raised
        # while handling another, which `raise ... from None` suppresses.
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )

    frames = []
    # Root cause first, so the issue keys on where it actually broke rather
    # than on whichever layer re-raised it.
    for link in reversed(chain):
        if frames:
            frames.append(
                {
                    "function": f"<raised {type(link).__name__}: {link}>"[:256],
                    "inApp": False,
                }
            )
        frames.extend(_frames_from_traceback(link.__traceback__))

    return {"type": type(exc).__name__, "message": str(exc), "stack": frames[:50]}


def _insert_id(timestamp, message, error):
    top_frame = ((error or {}).get("stack") or [{}])[0]
    raw = (
        f"{timestamp}{SERVICE_NAME}{message}"
        f"{top_frame.get('file', '')}{top_frame.get('line', '')}"
    )
    # Truncated to 32: the wire schema rejects a full 64-char digest.
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _labels(trace_id):
    env = os.environ.get("LOGCORE_ENV", "dev")
    labels = {"env": env if env in VALID_ENVS else "dev"}
    # Cloud Run does not set this by default; the schema validates it as a GCP
    # project id, so it is sent only when it actually looks like one.
    source_project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if source_project:
        labels["source_project"] = source_project
    if trace_id:
        labels["trace_id"] = trace_id
    return labels


def _warn_missing_service_id():
    global _MISSING_SERVICE_ID_REPORTED
    if not _MISSING_SERVICE_ID_REPORTED:
        _MISSING_SERVICE_ID_REPORTED = True
        # stderr, so the warning never lands among the JSON lines on stdout.
        print(
            "logcore: LOGCORE_SERVICE_ID is not set, logs are not being sent",
            file=sys.stderr,
        )


def emit_log(severity, message, error=None, trace_id=None, context=None):
    """Write one JSON log line to stdout. Never raises.

    This signature is the module's contract: anything that reports to logcore
    calls it this way, and `test_logcore.py` pins it. `error` is optional on
    purpose — a bug that computes the wrong value never raises, and reporting
    it is the only way that class of failure is ever seen.
    """
    try:
        # Read at call time, not at import: the id is fixed at deploy time and
        # a missing variable must not stop the app from starting.
        service_id = os.environ.get("LOGCORE_SERVICE_ID")
        if not service_id:
            # logcore discards a sink-delivered log that declares no
            # service_id, because a service name is not unique across
            # customers. Emitting anyway would only produce noise.
            _warn_missing_service_id()
            return

        timestamp = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        entry = {
            "timestamp": timestamp,
            "severity": severity,
            "message": message,
            "service": SERVICE_NAME,
            "service_id": service_id,
            # Cloud Run promotes only logging.googleapis.com/* keys out of a
            # structured line. A top-level env would stay inside jsonPayload,
            # where logcore does not read it, and every issue would record
            # env="unknown".
            "logging.googleapis.com/labels": _labels(trace_id),
            "logging.googleapis.com/insertId": _insert_id(timestamp, message, error),
        }
        if error:
            entry["error"] = error
        if context:
            entry["context"] = context

        print(json.dumps(entry), file=sys.stdout, flush=True)
    except Exception:  # noqa: BLE001 - logging must never break a request
        pass
