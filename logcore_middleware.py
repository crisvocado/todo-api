"""FastAPI middleware that reports unhandled exceptions to logcore."""

from starlette.middleware.base import BaseHTTPMiddleware

from logcore_logger import emit_log, error_from_exception


def _trace_id(request):
    # Cloud Run sets "TRACE_ID/SPAN_ID;o=1"; only the trace id is useful here.
    header = request.headers.get("x-cloud-trace-context", "")
    return header.split("/")[0] or None


class LogcoreMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            emit_log(
                "ERROR",
                f"{request.method} {request.url.path} raised {type(exc).__name__}",
                error=error_from_exception(exc),
                trace_id=_trace_id(request),
                # Path and method only: query strings and bodies can carry PII.
                context={"method": request.method, "path": request.url.path},
            )
            raise
