"""Cliente LLM con salida estructurada, búsqueda web opcional y contabilidad de costos.

- `AnthropicLLM`: Claude vía SDK oficial (`anthropic`). Salida JSON validada con esquema estricto.
- `ScriptedLLM`: respuestas pregrabadas para tests y demo offline (nunca se presenta como análisis real).
Sustituible: cualquier clase con el método `complete(...)` sirve (otro proveedor, modelo local, etc.).
"""
from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from pydantic import BaseModel

from qa.config import settings

try:                                       # transporte del SDK (errores de lectura del stream SSE no se envuelven)
    import httpx2 as _httpx
except ImportError:                        # pragma: no cover
    try:
        import httpx as _httpx
    except ImportError:
        _httpx = None

log = logging.getLogger(__name__)

WEB_SEARCH_USD_PER_REQUEST = 0.01          # USD 10 / 1.000 búsquedas
SEARCH_RESULT_TOKENS = 6000                # tokens de input por resultado de búsqueda (estimación)
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-1"}
UNSUPPORTED_SCHEMA_KEYS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength",
                           "pattern", "default", "minItems", "maxItems", "format", "multipleOf", "uniqueItems"}


class LLMError(RuntimeError):
    """Error de llamada. Si el proveedor ya facturó tokens, el costo viaja en la excepción (no se pierde)."""

    def __init__(self, msg: str, *, cost_usd: float = 0.0, input_tokens: int = 0, output_tokens: int = 0,
                 web_searches: int = 0):
        super().__init__(msg)
        self.cost_usd, self.input_tokens, self.output_tokens, self.web_searches = (
            cost_usd, input_tokens, output_tokens, web_searches)


class LLMRefusal(LLMError):
    pass


@dataclass
class LLMResponse:
    data: dict
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    web_searches: int = 0
    cost_usd: float = 0.0
    web_sources: list[str] = field(default_factory=list)
    stop_reason: str | None = None


class LLMClient(Protocol):
    def complete(self, *, agent: str, model: str, system: str, user: str, schema: dict, effort: str,
                 max_tokens: int, web_search_uses: int = 0, budget_usd: float | None = None) -> LLMResponse: ...


def price_tokens(model: str, input_tokens: int, output_tokens: int, web_searches: int = 0,
                 cache_read: int = 0, cache_write: int = 0) -> float:
    """USD de una llamada. Precios por modelo en settings (incluye lectura/escritura de caché)."""
    p = settings()["llm"]["pricing_usd_per_mtok"].get(model)
    if p is None:
        raise LLMError(f"sin precio configurado para {model} (config/settings.yaml)")
    read = p.get("cache_read", p["input"] * 0.1)
    write = p.get("cache_write", p["input"] * 1.25)
    return (input_tokens * p["input"] + output_tokens * p["output"] + cache_read * read + cache_write * write) / 1e6 \
        + web_searches * WEB_SEARCH_USD_PER_REQUEST


def usage_cost(usage: Any, model: str) -> tuple[float, int, int, int]:
    """Costo de un `usage` de la API. Si hay `iterations` (fallback server-side), cada intento se cobra
    al precio de SU modelo y se suman todos (también el intento rechazado)."""
    stu = getattr(usage, "server_tool_use", None)
    searches = (getattr(stu, "web_search_requests", 0) or 0) if stu else 0
    its = getattr(usage, "iterations", None) or []
    parts = its if its else [usage]
    cost = 0.0
    tin = tout = 0
    for i, it in enumerate(parts):
        m = getattr(it, "model", None) or model
        ti, to = getattr(it, "input_tokens", 0) or 0, getattr(it, "output_tokens", 0) or 0
        cr = getattr(it, "cache_read_input_tokens", 0) or 0
        cw = getattr(it, "cache_creation_input_tokens", 0) or 0
        try:
            cost += price_tokens(m, ti, to, searches if i == 0 else 0, cr, cw)
        except LLMError:
            cost += price_tokens(model, ti, to, searches if i == 0 else 0, cr, cw)   # modelo sin precio: usa el pedido
        tin, tout = tin + ti, tout + to
    return cost, tin, tout, searches


def estimate_cost(model: str, prompt_chars: int, max_tokens: int, web_search_uses: int) -> float:
    """Costo ESPERADO de una llamada (estimaciones y dry-run): ~3.5 chars/token, 60% de max_tokens."""
    est_in = prompt_chars / 3.5 + web_search_uses * SEARCH_RESULT_TOKENS   # resultados de búsqueda = input
    return price_tokens(model, int(est_in), int(max_tokens * 0.6), web_search_uses)


def request_bound(model: str, input_tokens: float, max_tokens: int, web_search_uses: int) -> float:
    """Cota superior de UNA solicitud: todo max_tokens de salida, todas las búsquedas y sus resultados con el
    recargo de escritura de caché. Se usa para el control de presupuesto (antes de cada solicitud)."""
    return price_tokens(model, int(input_tokens), max_tokens, web_search_uses,
                        cache_write=web_search_uses * SEARCH_RESULT_TOKENS)


def max_cost(model: str, prompt_chars: int, max_tokens: int, web_search_uses: int) -> float:
    """Cota de la PRIMERA solicitud de una llamada (~3 chars/token). Las continuaciones `pause_turn` se
    controlan dentro de `complete` con el presupuesto restante."""
    return request_bound(model, prompt_chars / 3.0, max_tokens, web_search_uses)


# --------------------------------------------------------------------------- esquemas estrictos
def strict_schema(model: type[BaseModel]) -> dict:
    """JSON Schema estricto a partir de Pydantic: $defs inlineados, additionalProperties=false, todo required.

    Se quitan restricciones no soportadas por structured outputs (min/max, etc.); la validación completa
    se hace después con Pydantic, que sí las aplica.
    """
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            out = {k: walk(v) for k, v in node.items() if k not in UNSUPPORTED_SCHEMA_KEYS and k != "title"}
            if out.get("type") == "object" or "properties" in out:
                out["type"] = "object"
                out["additionalProperties"] = False
                out["required"] = list(out.get("properties", {}).keys())
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(raw)


# --------------------------------------------------------------------------- Anthropic
class AnthropicLLM:
    def __init__(self, client: Any | None = None, max_pause_turns: int = 4):
        if client is None:
            import anthropic  # noqa: PLC0415
            client = anthropic.Anthropic()
        self.client = client
        self.max_pause_turns = max_pause_turns
        self.server_side_fallback = settings()["llm"].get("server_side_fallback", True)

    def complete(self, *, agent: str, model: str, system: str, user: str, schema: dict, effort: str,
                 max_tokens: int, web_search_uses: int = 0, budget_usd: float | None = None) -> LLMResponse:
        """`budget_usd`: gasto máximo de ESTA llamada. Antes de cada continuación `pause_turn` se verifica que lo
        gastado más la cota de la próxima solicitud no lo supere; si no, se corta con LLMError (costo incluido)."""
        import anthropic  # noqa: PLC0415

        transport = (anthropic.APIError,) + ((_httpx.HTTPError,) if _httpx is not None else ()) + (OSError,)

        tools = []
        if web_search_uses > 0:
            tools.append({"type": "web_search_20260209", "name": "web_search", "max_uses": web_search_uses})
        kwargs: dict[str, Any] = dict(
            model=model, max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
        if tools:
            kwargs["tools"] = tools
        betas = []
        if self.server_side_fallback and model in FALLBACK_MODELS:
            betas.append("server-side-fallback-2026-07-01")
            kwargs["fallbacks"] = "default"
        messages: list[dict] = [{"role": "user", "content": user}]
        tot_in = tot_out = searches = 0
        cost = 0.0
        sources: list[str] = []
        final = None

        def fail(cls: type[LLMError], msg: str) -> LLMError:  # el costo ya facturado viaja en la excepción
            return cls(msg, cost_usd=cost, input_tokens=tot_in, output_tokens=tot_out, web_searches=searches)

        def add_partial(st: Any) -> None:
            """Stream cortado a mitad: lo ya consumido (input + output emitido) se factura igual; se toma del
            snapshot parcial del SDK si existe."""
            nonlocal cost, tot_in, tot_out, searches
            try:
                snap = st.current_message_snapshot if st is not None else None
                if snap is not None and getattr(snap, "usage", None) is not None:
                    c, ti, to, ns = usage_cost(snap.usage, model)
                    cost, tot_in, tot_out, searches = cost + c, tot_in + ti, tot_out + to, searches + ns
            except Exception:  # noqa: BLE001 — sin snapshot (falló antes de message_start)
                pass

        last_in = last_out = 0
        for i in range(self.max_pause_turns + 1):
            if i and budget_usd is not None:
                nxt = request_bound(model, last_in + last_out, max_tokens, web_search_uses)
                if cost + nxt > budget_usd:
                    raise fail(LLMError, f"{agent}: presupuesto — se corta la continuación pause_turn "
                                         f"(gastado ${cost:.3f} + cota ${nxt:.3f} > ${budget_usd:.3f})")
            stream = None
            try:
                with self.client.beta.messages.stream(messages=messages, betas=betas, **kwargs) as stream:
                    final = stream.get_final_message()
            except anthropic.RateLimitError as e:
                raise fail(LLMError, f"rate limit: {e}") from e
            except anthropic.APIStatusError as e:
                add_partial(stream)
                raise fail(LLMError, f"API {e.status_code}: {e.message}") from e
            except anthropic.APIConnectionError as e:
                add_partial(stream)
                raise fail(LLMError, f"conexión: {e}") from e
            except transport as e:            # p.ej. stream SSE cortado a mitad del cuerpo
                add_partial(stream)
                raise fail(LLMError, f"transporte: {type(e).__name__}: {e}") from e
            except Exception as e:            # stream 200 vacío, evento SSE truncado, HTML de un proxy...
                add_partial(stream)
                raise fail(LLMError, f"stream inválido: {type(e).__name__}: {e}") from e
            except BaseException as e:        # Ctrl-C, SystemExit: el costo ya facturado viaja en la excepción
                add_partial(stream)
                e.qa_cost = (cost, tot_in, tot_out, searches)
                raise
            c, ti, to, ns = usage_cost(final.usage, model)
            last_in, last_out = ti, to
            cost, tot_in, tot_out, searches = cost + c, tot_in + ti, tot_out + to, searches + ns
            for block in final.content:
                if getattr(block, "type", "") == "web_search_tool_result" and isinstance(block.content, list):
                    sources += [r.url for r in block.content if getattr(r, "url", None)]
            if final.stop_reason == "pause_turn":           # turno largo con server tools: continuar
                messages = messages + [{"role": "assistant", "content": final.content}]
                continue
            break
        if final.stop_reason == "refusal":
            raise fail(LLMRefusal, f"{agent}: rechazo ({getattr(final.stop_details, 'category', None)})")
        if final.stop_reason == "max_tokens":
            raise fail(LLMError, f"{agent}: max_tokens alcanzado — subir max_tokens del tier")
        if final.stop_reason == "pause_turn":
            raise fail(LLMError, f"{agent}: excedió {self.max_pause_turns} continuaciones (pause_turn)")
        text = next((b.text for b in reversed(final.content) if getattr(b, "type", "") == "text"), None)
        if text is None:
            raise fail(LLMError, f"{agent}: respuesta sin bloque de texto")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise fail(LLMError, f"{agent}: JSON inválido: {e}") from e
        return LLMResponse(data=data, model=getattr(final, "model", model), input_tokens=tot_in, output_tokens=tot_out,
                           web_searches=searches, cost_usd=cost, web_sources=sorted(set(sources)),
                           stop_reason=final.stop_reason)


# --------------------------------------------------------------------------- scripted (tests/demo)
class ScriptedLLM:
    """Devuelve respuestas pregrabadas por agente. Uso exclusivo en tests y en `qa demo` (datos sintéticos).

    `responses`: {agent_name: dict | callable(user_payload: dict) -> dict}
    """

    def __init__(self, responses: dict[str, dict | Callable[[dict], dict]], tokens: tuple[int, int] = (2000, 800)):
        self.responses = responses
        self.tokens = tokens
        self.calls: list[str] = []

    def complete(self, *, agent: str, model: str, system: str, user: str, schema: dict, effort: str,
                 max_tokens: int, web_search_uses: int = 0, budget_usd: float | None = None) -> LLMResponse:
        self.calls.append(agent)
        r = self.responses.get(agent)
        if r is None:
            raise LLMError(f"ScriptedLLM: sin respuesta para {agent}")
        payload = json.loads(user.split("\n<data>", 1)[1].rsplit("</data>", 1)[0]) if "\n<data>" in user else {}
        data = r(payload) if callable(r) else copy.deepcopy(r)
        ti, to = self.tokens
        return LLMResponse(data=data, model=model, input_tokens=ti, output_tokens=to,
                           cost_usd=price_tokens(model, ti, to), stop_reason="end_turn")
