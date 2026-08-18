"""FastAPI middleware that reports failed requests to logcore."""

from starlette.middleware.base import BaseHTTPMiddleware

from logcore_logger import emit, error_payload


def _route(request):
    # The route template rather than the raw path, so /todos/1 and /todos/2
    # group into one issue instead of one per id.
    route = request.scope.get("route")
    return getattr(route, "path", None) or request.url.path


class LogcoreMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        try:
            response = await call_next(request)
        except Exception as exc:
            route = _route(request)
            emit(
                "ERROR",
                f"Unhandled exception on {request.method} {route}",
                error=error_payload(exc),
                # Method and route only — never the query string, headers or
                # body, which carry tokens and user data.
                context={"method": request.method, "route": route},
                fingerprint=f"{request.method} {route}:{type(exc).__name__}",
            )
            raise

        # 5xx only. A 4xx is ordinary traffic here (the API answers 404 for a
        # todo that does not exist) and would bury real failures in noise.
        if response.status_code >= 500:
            route = _route(request)
            emit(
                "ERROR",
                f"{response.status_code} on {request.method} {route}",
                context={
                    "method": request.method,
                    "route": route,
                    "status_code": response.status_code,
                },
                fingerprint=f"{request.method} {route}:{response.status_code}",
            )
        return response
