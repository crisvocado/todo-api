---
description: Reinyecta el escenario de demo (bug + su reporte a logcore) sobre un onboarding recién hecho
---

# Reinyectar el escenario de demo

Esta API se usa para demostrar el ciclo completo de la plataforma: un error
llega a logcore, el clasificador lo tipifica, el debugger clona el repo, lo
arregla y abre un PR.

**Antes de nada, mira si ya existe la rama `demo/slug-ascii-error`.** Contiene
este escenario en un solo commit, hecho para aplicarse sobre un `main` recién
onboardeado. Si está, esto se reduce a mergear su PR y no hace falta escribir
nada:

```bash
git log --oneline main..origin/demo/slug-ascii-error
```

El resto de este documento es para reconstruirla si se perdió.

## El contrato del emisor

El onboarding genera el módulo de logging desde el contrato del MCP, y ese
contrato fija **el formato del cable**, no cómo se llaman las funciones. Para
que el escenario de demo siga aplicando entre onboardings, este repo fija
además la firma, y `test_logcore.py` la afirma:

```python
emit_log(severity, message, error=None, trace_id=None, context=None) -> None
error_from_exception(exc) -> dict
```

`error` es opcional a propósito: un bug que calcula mal nunca lanza, y
reportarlo es la única vía por la que esa clase de fallo se ve. Si tras un
onboarding `test_emit_log_signature_is_the_contract` falla, arregla el módulo
generado antes de seguir — no adaptes la demo a una firma nueva.

## El bug

En `main.py`, un slug para el todo recién creado:

```python
def _slug(title: str) -> str:
    """Search key for the todo: lowercase, ascii, spaces as hyphens."""
    return title.strip().lower().replace(" ", "-").encode("ascii").decode("ascii")
```

y su uso en `create_todo`, sobre el `dict(todo)` que ya se devolvía:

```python
result = dict(todo)
result["slug"] = _slug(result["title"])
return result
```

Se eligió este bug por dos razones:

- **Lanza.** `"Comprar café".encode("ascii")` es un `UnicodeEncodeError`, así
  que el middleware del onboarding lo reporta solo. La rama de demo no importa
  ni llama a `emit_log`: si hiciera falta tocar el módulo de logging para que
  el fallo se vea, el escenario estaría probando el pegamento en vez de la
  plataforma.
- **Parece razonable.** Un `.encode("ascii")` en un slug es un descuido
  creíble, no un `raise` de mentira. El POST solo revienta con títulos
  acentuados, que en una app en español es el caso normal.

`pytest` sigue en verde con el bug puesto: ningún test manda títulos con
tilde. Es deliberado — el debugger tiene que reproducirlo desde el error que
llega a logcore, no desde una suite roja.

## Verificar

```bash
docker run --rm -v "$PWD":/app -w /app python:3.12-slim \
  sh -c "pip install -q -r requirements.txt pytest httpx && pytest -q"
```

Y contra el servicio desplegado, con `LOGCORE_SERVICE_ID` puesto en el deploy:

```bash
curl -X POST "$API_URL/todos" -H 'Content-Type: application/json' \
  -d '{"title":"Comprar café"}'
```

Devuelve 500, y en Cloud Logging tiene que aparecer una línea JSON con
`severity: ERROR` y `error.stack[0].function == "_slug"`.
