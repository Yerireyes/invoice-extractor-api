"""Adaptador de Ollama (modo privado: el modelo corre en local) para el puerto `InvoiceExtractor`.

- Mismo prompt (`prompts.py`) y mismo esquema de salida (`llm_output.LlmExtraction`) que Gemini:
  Ollama restringe la respuesta al JSON Schema (`format`) y se convierte al dominio igual.
- Todas las páginas de un documento van en un solo mensaje.
- Un reintento con backoff solo si Ollama no acepta la conexión o responde 5xx (p. ej. arrancando
  o cargando el modelo). Un timeout no se reintenta: en CPU significaría esperar el doble.
- `OllamaStatus` comprueba (con caché) que el servidor responde y que el modelo está descargado;
  `/meta` lo usa para anunciar si el proveedor está disponible de verdad.
- Privacidad: no se registran ni el contenido enviado ni la respuesta; solo metadatos.
"""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

import httpx
import ollama
from pydantic import ValidationError

from app.core.config import LLMProvider
from app.core.errors import AppError, ErrorCode
from app.invoices.adapters.llm_output import LlmExtraction, to_extraction_output
from app.invoices.adapters.prompts import SYSTEM_PROMPT, USER_PROMPT
from app.invoices.ports import ExtractionFailedError, ExtractionOutput
from app.shared.files import PageImage

logger = logging.getLogger(__name__)

TRANSIENT_STATUS_CODES = frozenset({500, 502, 503, 504})
RETRY_BASE_DELAY_S = 1.0
CONTEXT_TOKENS = 16_384
"""Ventana de contexto pedida al modelo: imágenes de varias páginas + JSON de salida."""
MAX_OUTPUT_TOKENS = 8_192
STATUS_TTL_S = 30.0
STATUS_TIMEOUT_S = 2.0

LLM_SCHEMA: dict[str, Any] = LlmExtraction.model_json_schema()


class OllamaApi(Protocol):
    """Lo que usa el adaptador de `ollama.AsyncClient` (sustituible en los tests)."""

    async def chat(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        format: Mapping[str, Any],  # noqa: A002 - nombre del parámetro en el SDK
        options: Mapping[str, Any],
    ) -> ollama.ChatResponse: ...

    async def list(self) -> ollama.ListResponse: ...


type Sleep = Callable[[float], Awaitable[None]]


def new_client(base_url: str, *, timeout_s: float) -> OllamaApi:
    client: Any = ollama.AsyncClient(host=base_url, timeout=timeout_s)
    return client  # type: ignore[no-any-return]  # las sobrecargas de `chat` no encajan en el Protocol


class OllamaExtractor:
    """Implementa `InvoiceExtractor` con un modelo de visión servido por Ollama."""

    def __init__(self, client: OllamaApi, *, model: str, sleep: Sleep = asyncio.sleep) -> None:
        self._client = client
        self._model = model
        self._sleep = sleep

    @property
    def provider(self) -> LLMProvider:
        return "ollama"

    async def extract(self, pages: Sequence[PageImage]) -> ExtractionOutput:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT, "images": [page.data for page in pages]},
        ]
        started = time.perf_counter()
        response = await self._chat_with_retry(messages)
        logger.info(
            "Ollama %s: %d ms, tokens entrada=%s salida=%s",
            self._model,
            round((time.perf_counter() - started) * 1000),
            response.prompt_eval_count,
            response.eval_count,
        )
        return to_extraction_output(_parse(response), model=self._model)

    async def _chat_with_retry(self, messages: list[dict[str, Any]]) -> ollama.ChatResponse:
        for attempt in (1, 2):
            try:
                return await self._client.chat(
                    model=self._model,
                    messages=messages,
                    format=LLM_SCHEMA,
                    options={
                        "temperature": 0,
                        "num_ctx": CONTEXT_TOKENS,
                        "num_predict": MAX_OUTPUT_TOKENS,
                    },
                )
            except Exception as exc:
                if attempt == 1 and _is_transient(exc):
                    delay = RETRY_BASE_DELAY_S * (1 + random.random())  # noqa: S311 - jitter
                    logger.warning("Ollama: error transitorio, reintento en %.1f s", delay)
                    await self._sleep(delay)
                    continue
                raise _map_error(exc) from exc
        raise AssertionError  # pragma: no cover - el bucle siempre retorna o lanza


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, ollama.ResponseError):
        return exc.status_code in TRANSIENT_STATUS_CODES
    # El SDK convierte `httpx.ConnectError` en `ConnectionError` (servidor caído o arrancando).
    return isinstance(exc, (ConnectionError, httpx.NetworkError))


def _map_error(exc: Exception) -> Exception:
    """Traduce errores del SDK a errores del contrato."""
    if isinstance(exc, ollama.ResponseError):
        if exc.status_code == 404:  # noqa: PLR2004
            logger.error("Ollama: el modelo no está descargado (ollama pull)")
            return AppError(ErrorCode.LLM_UNAVAILABLE)
        if exc.status_code == 400:  # noqa: PLR2004 - p. ej. imagen rechazada por el modelo
            logger.warning("Ollama rechazó la petición (400)")
            return ExtractionFailedError()
        logger.error("Ollama no disponible (%s)", exc.status_code)
        return AppError(ErrorCode.LLM_UNAVAILABLE)
    if isinstance(exc, (ConnectionError, httpx.NetworkError, httpx.TimeoutException)):
        logger.error("Ollama no responde (%s)", type(exc).__name__)
        return AppError(ErrorCode.LLM_UNAVAILABLE)
    return exc


def _parse(response: ollama.ChatResponse) -> LlmExtraction:
    if response.done_reason not in (None, "stop"):
        logger.warning(
            "Ollama terminó sin resultado utilizable (done_reason=%s)", response.done_reason
        )
        raise ExtractionFailedError
    text = response.message.content
    if not text:
        raise ExtractionFailedError
    try:
        return LlmExtraction.model_validate_json(text)
    except ValidationError as exc:
        logger.warning("Ollama devolvió un JSON que no cumple el esquema")
        raise ExtractionFailedError from exc


class OllamaStatus:
    """¿Responde Ollama y tiene el modelo descargado? Resultado en caché `ttl_s` segundos."""

    def __init__(
        self,
        client: OllamaApi,
        *,
        model: str,
        ttl_s: float = STATUS_TTL_S,
        timeout_s: float = STATUS_TIMEOUT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._model = _with_tag(model)
        self._ttl_s = ttl_s
        self._timeout_s = timeout_s
        self._clock = clock
        self._cached: tuple[float, bool] | None = None

    async def available(self) -> bool:
        now = self._clock()
        if self._cached and now - self._cached[0] < self._ttl_s:
            return self._cached[1]
        result = await self._check()
        self._cached = (now, result)
        return result

    async def _check(self) -> bool:
        try:
            response = await asyncio.wait_for(self._client.list(), self._timeout_s)
        except Exception as exc:
            logger.warning("Ollama no responde al comprobar su estado (%s)", type(exc).__name__)
            return False
        models = {_with_tag(item.model) for item in response.models if item.model}
        if self._model not in models:
            logger.warning("Ollama responde, pero el modelo no está descargado")
            return False
        return True


def _with_tag(name: str) -> str:
    """`qwen2.5vl` y `qwen2.5vl:latest` son el mismo modelo para Ollama."""
    return name if ":" in name else f"{name}:latest"
