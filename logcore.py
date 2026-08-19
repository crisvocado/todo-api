"""Structured logging for logcore.

Writes one JSON object per line to stdout. Cloud Logging captures those lines
and the `errors-to-logcore` sink forwards them, so nothing here touches the
network and the module is safe to import and to exercise in tests.
"""

import datetime
import hashlib
import json
import os
import sys
import traceback

# Frames whose file lives under one of these belongs to a dependency, not to
# this service. logcore separates the two to decide what an issue groups on.
LIBRARY_PATH_MARKERS = ("site-packages", "dist-packages", "/usr/lib/python")

MAX_STACK_FRAMES = 50

_warned_about_missing_service_id = False


def _service_id():
    """The platform's id for this service.

    Not the service name: a name is not unique across customers, so logcore
    discards a sink-delivered log that declares no service_id. Read from the
    environment because a re-created service is issued a new id, and an env var
    is fixed at deploy time rather than by editing committed code.
    """
    return os.environ.get("LOGCORE_SERVICE_ID", "")


def _labels(trace_id):
    # Cloud Run promotes only `logging.googleapis.com/*` keys out of a
    # structured line. A top-level "env" stays inside jsonPayload, where logcore
    # does not look, and every issue from this service records env="unknown".
    labels = {"env": os.environ.get("LOGCORE_ENV", "dev")}
    source_project = os.environ.get("LOGCORE_SOURCE_PROJECT", "")
    if source_project:
        labels["source_project"] = source_project
    if trace_id:
        labels["trace_id"] = trace_id
    return labels


def _frames_from_traceback(tb):
    # Reversed: extract_tb returns outermost first, and logcore takes the first
    # in-app frame as the issue's top location. Left in its original order every
    # error in the service would group on the ASGI entry point they all share.
    return [
        {
            "function": frame.name,
            "file": frame.filename,
            "line": frame.lineno or 0,
            "inApp": not any(
                marker in frame.filename for marker in LIBRARY_PATH_MARKERS
            ),
        }
        for frame in reversed(traceback.extract_tb(tb))
    ]


def error_from_exception(exc):
    """Build the `error` object from a caught exception, root cause first.

    `stack` is a list of parsed frames; a raw traceback string is rejected and
    the log is lost. The exception chain is walked to its root because a layer
    that re-raises stops its own traceback at the `raise`, so the line that
    actually broke is not in it.
    """
    chain = []
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        # __cause__ is `raise X from Y`; __context__ is an exception raised while
        # handling another. `raise ... from None` sets __suppress_context__,
        # which is the author saying the context is noise.
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )

    frames = []
    for link in reversed(chain):
        if frames:
            # A marker rather than a real frame, so the boundary between one
            # exception and the next is visible. inApp=False keeps it out of the
            # fingerprint material; file and line are optional in the schema.
            frames.append(
                {
                    "function": f"<raised {type(link).__name__}: {link}>"[:256],
                    "inApp": False,
                }
            )
        frames.extend(_frames_from_traceback(link.__traceback__))

    # type and message stay those of the exception actually raised: that is what
    # the service reported and what its own logs will say.
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "stack": frames[:MAX_STACK_FRAMES],
    }


def _insert_id(timestamp, service, message, error):
    """A deterministic id, so the same error redelivered is deduped.

    Built from the throw site rather than the whole stack: it identifies the
    error while staying stable across the frames around it that vary per
    request.
    """
    top_frame = ((error or {}).get("stack") or [{}])[0]
    raw = f"{timestamp}{service}{message}{top_frame.get('file', '')}{top_frame.get('line', '')}"
    # Truncated to 32: the wire schema rejects a full 64-char digest.
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def log(severity, message, error=None, context=None, fingerprint=None, trace_id=None):
    """Emit one log entry. Never raises: a log that cannot be written is dropped."""
    global _warned_about_missing_service_id

    try:
        service_id = _service_id()
        if not service_id:
            if not _warned_about_missing_service_id:
                _warned_about_missing_service_id = True
                print(
                    "logcore: LOGCORE_SERVICE_ID is not set, logs are not being "
                    "reported",
                    file=sys.stderr,
                    flush=True,
                )
            return

        service = os.environ.get("LOGCORE_SERVICE", "todo-api")
        timestamp = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        entry = {
            "timestamp": timestamp,
            "severity": severity,
            "message": message,
            "service": service,
            # logcore reads this out of jsonPayload, so it stays top-level.
            "service_id": service_id,
            "logging.googleapis.com/labels": _labels(trace_id),
            "logging.googleapis.com/insertId": _insert_id(
                timestamp, service, message, error
            ),
        }
        if error:
            entry["error"] = error
        if context:
            entry["context"] = context
        if fingerprint:
            entry["fingerprint"] = fingerprint

        # One object per line, written in a single call so concurrent requests
        # cannot interleave partial writes.
        print(json.dumps(entry), file=sys.stdout, flush=True)
    except Exception:
        # Logging must never break a request.
        pass


def log_exception(exc, message=None, context=None, fingerprint=None, trace_id=None):
    """Report a caught exception at ERROR severity."""
    log(
        "ERROR",
        message or f"{type(exc).__name__}: {exc}",
        error=error_from_exception(exc),
        context=context,
        fingerprint=fingerprint,
        trace_id=trace_id,
    )
