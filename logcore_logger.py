"""Structured JSON logging to stdout for logcore.

Cloud Logging captures each line printed here and the logcore sink forwards it.
Nothing is emitted at import time — the first line is written only when
``log`` or ``log_exception`` is called.
"""

import datetime
import hashlib
import json
import os
import sys
import traceback


# Frames whose file lives under one of these belong to a dependency, not to
# this service. logcore fingerprints on the first in-app frame, so mislabelling
# them would group every issue on a library.
LIB_MARKERS = ("site-packages", "dist-packages", "/usr/lib/python")

MAX_FRAMES = 50


def _now():
    # The schema wants ISO 8601 with a Z suffix; isoformat() renders "+00:00".
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _frames_of(tb):
    # Reversed: extract_tb returns the outermost frame first, and logcore reads
    # in_app_locations[0] as the top location. Left as-is every issue would
    # group on Starlette's entry point, identical for every error we report.
    return [
        {
            "function": frame.name,
            "file": frame.filename,
            "line": frame.lineno or 0,
            "inApp": not any(m in frame.filename for m in LIB_MARKERS),
        }
        for frame in reversed(traceback.extract_tb(tb))
    ]


def error_from_exception(exc):
    """Build the ``error`` object for a caught exception.

    ``stack`` is a list of parsed frames — a raw traceback string is rejected
    and the log is lost silently on the sink path.

    The exception chain is followed to its root because a handler wrapping a
    sqlite3 error is ordinary here, and the wrapper's own traceback stops at the
    ``raise``: the line that actually broke is not in it.
    """
    chain, seen, current = [], set(), exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        # __cause__ is `raise X from Y`; __context__ is an exception raised
        # while handling another. `from None` sets __suppress_context__, which
        # is the author saying the context is noise.
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )

    frames = []
    # Root cause first, so the issue keys on where it actually broke rather than
    # on whichever layer re-raised. Truncating at MAX_FRAMES then keeps the root.
    for link in reversed(chain):
        if frames:
            # A marker rather than a real frame: file and line are optional, and
            # inApp=False keeps it out of the fingerprint material while still
            # showing where one exception ends and the next begins.
            frames.append(
                {
                    "function": f"<raised {type(link).__name__}: {link}>"[:256],
                    "inApp": False,
                }
            )
        frames.extend(_frames_of(link.__traceback__))

    # type/message stay those of the exception actually raised: that is what the
    # service reported and what its own logs will say.
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "stack": frames[:MAX_FRAMES],
    }


def build_entry(severity, message, error=None, trace_id=None, context=None,
                fingerprint=None):
    """Assemble one log entry in the shape the Cloud Logging sink expects.

    Kept separate from :func:`log` so the entry can be inspected in tests
    without capturing stdout.
    """
    timestamp = _now()
    service = os.environ.get("LOGCORE_SERVICE_NAME", "todo-api")

    # The throw site alone, not the whole stack: it identifies the error while
    # staying stable across the frames around it that vary per request, so a
    # retry of the same failure dedups instead of opening a second issue.
    top = ((error or {}).get("stack") or [{}])[0]
    raw = f"{timestamp}{service}{message}{top.get('file', '')}{top.get('line', '')}"
    # Truncated to 32: the wire schema rejects a full 64-char digest.
    insert_id = hashlib.sha256(raw.encode()).hexdigest()[:32]

    labels = {"env": os.environ.get("LOGCORE_ENV", "dev")}
    source_project = os.environ.get("LOGCORE_SOURCE_PROJECT") or os.environ.get(
        "GOOGLE_CLOUD_PROJECT", ""
    )
    if source_project:
        labels["source_project"] = source_project
    if trace_id:
        labels["trace_id"] = trace_id

    entry = {
        "timestamp": timestamp,
        "severity": severity,
        "message": message,
        "service": service,
        # A service NAME is not unique across customers, so logcore discards a
        # sink-delivered log that declares no service_id. Read from the
        # environment: a re-created service is issued a new id, and that is
        # fixed at deploy time rather than by editing committed code.
        "service_id": os.environ.get("LOGCORE_SERVICE_ID", ""),
        # Cloud Run promotes ONLY logging.googleapis.com/* keys out of a
        # structured line. Anything else stays inside jsonPayload, where logcore
        # does not read labels: a top-level "env" makes every issue from this
        # service record env="unknown", and a top-level insert id loses the
        # deterministic dedup above.
        "logging.googleapis.com/labels": labels,
        "logging.googleapis.com/insertId": insert_id,
    }
    if error:
        entry["error"] = error
    if context:
        entry["context"] = context
    if fingerprint:
        entry["fingerprint"] = fingerprint
    return entry


def log(severity, message, error=None, trace_id=None, context=None,
        fingerprint=None):
    """Write one JSON object per line to stdout. Never raises."""
    try:
        entry = build_entry(severity, message, error, trace_id, context,
                            fingerprint)
        # One write, flushed: a partial line interleaved with another worker's
        # output is not valid JSON and the sink drops both.
        print(json.dumps(entry), file=sys.stdout, flush=True)
    except Exception:
        # A log that cannot be written must never break the request that
        # produced it.
        pass


def log_exception(exc, message=None, trace_id=None, context=None,
                  fingerprint=None):
    """Report a caught exception at ERROR severity. Never raises."""
    try:
        error = error_from_exception(exc)
    except Exception:
        return
    log(
        "ERROR",
        message or str(exc) or type(exc).__name__,
        error=error,
        trace_id=trace_id,
        context=context,
        fingerprint=fingerprint,
    )
