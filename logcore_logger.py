"""Emisor de logs estructurados para logcore.

Escribe una línea JSON por evento en stdout; Cloud Logging la captura y el sink
la reenvía a logcore. No hay red ni credenciales aquí: el transporte es stdout.
"""

import datetime
import hashlib
import json
import os
import sys
import traceback

# Marcadores de dependencias: los frames que vengan de aquí se marcan
# inApp=False para separar el código de la aplicación del de sus librerías.
LIBRARY_MARKERS = ("site-packages", "dist-packages", "/usr/lib/python")

MAX_STACK_FRAMES = 50


def _service_id():
    # Se lee en cada emisión, no al importar: un servicio recreado recibe un id
    # nuevo y eso se corrige con la variable de entorno del despliegue, no
    # editando código ya commiteado.
    return os.environ.get("LOGCORE_SERVICE_ID")


def _service_name():
    return os.environ.get("LOGCORE_SERVICE_NAME", "todo-api")


def _env():
    return os.environ.get("LOGCORE_ENV", "dev")


def _source_project():
    return os.environ.get("LOGCORE_SOURCE_PROJECT")


def _frames_of(exception_traceback):
    # Invertido: extract_tb devuelve el frame más externo primero y logcore usa
    # in_app_locations[0] como ubicación principal del issue. Sin invertir,
    # todos los errores del servicio agruparían por el punto de entrada del
    # framework, que es el mismo para todos.
    return [
        {
            "function": frame.name,
            "file": frame.filename,
            "line": frame.lineno or 0,
            "inApp": not any(marker in frame.filename for marker in LIBRARY_MARKERS),
        }
        for frame in reversed(traceback.extract_tb(exception_traceback))
    ]


def error_from_exception(exception):
    """Construye el objeto `error` a partir de una excepción capturada.

    `stack` es una LISTA DE FRAMES YA PARSEADOS. Enviar el traceback como
    cadena hace que logcore rechace la entrada y el log se pierde en silencio.

    Recorre la cadena de excepciones hasta la causa raíz: una capa que envuelve
    el error de un driver es lo normal, y el traceback del envoltorio termina en
    el `raise`, sin la línea que realmente falló.
    """
    chain = []
    seen = set()
    current = exception
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        # __cause__ es `raise X from Y`; __context__ es una excepción lanzada
        # mientras se manejaba otra. __suppress_context__ lo activa
        # `from None`, que es el autor diciendo que el contexto es ruido.
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )

    frames = []
    # La causa raíz PRIMERO, para que el issue agrupe por donde se rompió de
    # verdad y no por la capa que lo relanzó. Al truncar a 50 se conserva.
    for link in reversed(chain):
        if frames:
            # Marca, no un frame real: file y line son opcionales en el
            # esquema. inApp=False lo mantiene fuera del fingerprint sin dejar
            # de mostrar dónde termina una excepción y empieza la siguiente.
            frames.append(
                {
                    "function": f"<raised {type(link).__name__}: {link}>"[:256],
                    "inApp": False,
                }
            )
        frames.extend(_frames_of(link.__traceback__))

    # type y message son los de la excepción realmente lanzada: es lo que
    # reportó el servicio y lo que dirán sus propios logs.
    return {
        "type": type(exception).__name__,
        "message": str(exception),
        "stack": frames[:MAX_STACK_FRAMES],
    }


def _insert_id(timestamp, service, message, error):
    # El frame que lanzó (el 0), no la pila entera: identifica el error y se
    # mantiene estable frente a los frames que cambian en cada petición.
    top_frame = ((error or {}).get("stack") or [{}])[0]
    raw = (
        f"{timestamp}{service}{message}"
        f"{top_frame.get('file', '')}{top_frame.get('line', '')}"
    )
    # Truncado a 32: el esquema rechaza un digest completo de 64 caracteres.
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def build_entry(severity, message, error=None, context=None, fingerprint=None,
                trace_id=None):
    """Devuelve la línea que se escribirá, sin escribirla.

    Separado de `emit` para poder verificar la forma del payload sin capturar
    stdout.
    """
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    service = _service_name()

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
        # logcore lo lee dentro de jsonPayload, así que va al nivel superior.
        "service_id": _service_id(),
        # Cloud Run solo promociona claves logging.googleapis.com/* fuera de una
        # línea estructurada. Lo demás se queda en jsonPayload, donde logcore no
        # busca labels: un "env" al nivel superior hace que todos los issues del
        # servicio registren env="unknown", y un "insert_id" arriba pierde la
        # deduplicación determinista de los reintentos.
        "logging.googleapis.com/labels": labels,
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
    return entry


def emit(severity, message, error=None, context=None, fingerprint=None,
         trace_id=None):
    """Escribe una entrada en stdout. Nunca propaga un fallo al que llama."""
    if not _service_id():
        # Sin service_id logcore descarta la entrega del sink, porque un NOMBRE
        # de servicio no es único entre clientes. Se calla en vez de romper.
        return
    try:
        entry = build_entry(severity, message, error, context, fingerprint, trace_id)
        print(json.dumps(entry), file=sys.stdout, flush=True)
    except Exception:  # noqa: BLE001 - un log que falla no puede tumbar la app
        pass


def log_exception(exception, message=None, context=None, fingerprint=None,
                  trace_id=None):
    """Atajo para reportar una excepción con severidad ERROR."""
    error = error_from_exception(exception)
    emit(
        "ERROR",
        message or f"{error['type']}: {error['message']}",
        error=error,
        context=context,
        fingerprint=fingerprint,
        trace_id=trace_id,
    )
