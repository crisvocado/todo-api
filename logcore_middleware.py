"""ASGI middleware that reports unhandled exceptions to logcore."""

from logcore import log_exception


class LogcoreMiddleware:
    """Logs any exception escaping the app, then re-raises it untouched.

    Pure ASGI rather than BaseHTTPMiddleware: it adds no buffering to responses
    and sees exceptions exactly as they propagate. HTTPException never reaches
    it, because Starlette's exception middleware turns those into responses
    further in — which is what we want, a 404 is not an error to report.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        try:
            await self.app(scope, receive, send)
        except Exception as exc:
            method = scope.get("method", "")
            path = scope.get("path", "")
            # Method and path only. The body, headers and query string carry
            # user data and credentials, and never belong in a log.
            log_exception(
                exc,
                message=f"Unhandled exception on {method} {path}",
                context={"method": method, "path": path},
            )
            raise
