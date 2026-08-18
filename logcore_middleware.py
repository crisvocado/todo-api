"""FastAPI adapter: report unhandled exceptions and 5xx responses to logcore."""

from starlette.middleware.base import BaseHTTPMiddleware

from logcore_logger import log, log_exception


def _trace_id(request):
    """Cloud Run sends `X-Cloud-Trace-Context: TRACE_ID/SPAN_ID;o=1`."""
    header = request.headers.get("x-cloud-trace-context", "")
    return header.split("/")[0].split(";")[0] or None


def _context(request, status_code=None):
    # Method and path only. The query string and body can carry tokens or user
    # data, and the auth headers certainly do.
    route = request.scope.get("route")
    context = {
        "method": request.method,
        "path": getattr(route, "path", None) or request.url.path,
    }
    if status_code is not None:
        context["status_code"] = status_code
    return context


class LogcoreMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        try:
            response = await call_next(request)
        except Exception as exc:
            # Re-raised untouched: Starlette's ServerErrorMiddleware sits
            # outside this one and still owns the 500 the client receives.
            log_exception(exc, trace_id=_trace_id(request),
                          context=_context(request))
            raise

        if response.status_code >= 500:
            log(
                "ERROR",
                f"{request.method} {request.url.path} -> {response.status_code}",
                trace_id=_trace_id(request),
                context=_context(request, response.status_code),
            )
        return response


def install_logcore(app):
    """Register the middleware on a FastAPI app.

    Added last so it is the outermost user middleware and therefore sees
    exceptions that propagate out of every inner layer, CORS included.
    HTTPException never reaches here — Starlette's ExceptionMiddleware handles
    it further in, which is what keeps an ordinary 404 out of logcore.
    """
    app.add_middleware(LogcoreMiddleware)
