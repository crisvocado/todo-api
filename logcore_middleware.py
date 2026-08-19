"""Middleware de FastAPI que reporta a logcore las excepciones no manejadas."""

from starlette.middleware.base import BaseHTTPMiddleware

from logcore_logger import log_exception


class LogcoreMiddleware(BaseHTTPMiddleware):
    """Registra la excepción y la relanza para que FastAPI responda 500.

    Las HTTPException no llegan hasta aquí: Starlette ya las ha convertido en
    respuesta antes de salir del router, que es justo lo que queremos —un 404
    esperado no es un error que reportar.
    """

    async def dispatch(self, request, call_next):
        try:
            return await call_next(request)
        except Exception as exception:
            log_exception(
                exception,
                context={
                    "method": request.method,
                    # Solo la ruta: la query puede llevar datos personales.
                    "path": request.url.path,
                },
                # El header lo pone Cloud Run con el formato
                # "TRACE_ID/SPAN_ID;o=1"; a logcore le interesa el trace.
                trace_id=(
                    request.headers.get("x-cloud-trace-context", "").split("/")[0]
                    or None
                ),
            )
            raise
