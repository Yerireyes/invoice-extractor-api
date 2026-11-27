"""Adaptador de Gemini (SDK `google-genai`) para el puerto `InvoiceExtractor`.

- Salida estructurada con el esquema propio para el LLM (`llm_output.LlmExtraction`), que se
  convierte al dominio con `to_extraction_output`.
- Todas las páginas de un documento van en una sola llamada.
- Un reintento con backoff solo para errores transitorios (5xx, timeout, red). Nunca se reintenta
  un 429, para no gastar más cuota. El reintento propio del SDK (hasta 5 intentos, 429 incluido)
  se desactiva para que esta sea la única política.
- Privacidad: no se registran ni el contenido enviado ni la respuesta; solo metadatos.
"""

import asyncio
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

import httpx
import httpx2
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import ValidationError

from app.core.config import LLMProvider
from app.core.errors import AppError, ErrorCode
from app.invoices.adapters.llm_output import LlmExtraction, to_extraction_output
from app.invoices.adapters.prompts import SYSTEM_PROMPT, USER_PROMPT
from app.invoices.ports import ExtractionFailedError, ExtractionOutput
from app.shared.files import PageImage

logger = logging.getLogger(__name__)

TRANSIENT_STATUS_CODES = frozenset({500, 502, 503, 504})
NETWORK_ERRORS = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx2.TimeoutException,
    httpx2.NetworkError,
)
"""El SDK usa `httpx2`; se incluye `httpx` por si un transporte personalizado lo usa."""
RETRY_BASE_DELAY_S = 1.0
MAX_OUTPUT_TOKENS = 16_384

# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------


class AsyncModels(Protocol):
    async def generate_content(
        self, *, model: str, contents: Any, config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse: ...


type Sleep = Callable[[float], Awaitable[None]]


def http_options(timeout_s: float) -> types.HttpOptions:
    """Timeout por llamada y reintentos del SDK desactivados (`attempts=1`)."""
    return types.HttpOptions(
        timeout=int(timeout_s * 1000), retry_options=types.HttpRetryOptions(attempts=1)
    )


class GeminiExtractor:
    """Implementa `InvoiceExtractor` con Gemini."""

    def __init__(
        self,
        models: AsyncModels,
        *,
        model: str,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._models = models
        self._model = model
        self._sleep = sleep

    @classmethod
    def from_api_key(cls, api_key: str, *, model: str, timeout_s: float) -> GeminiExtractor:
        client = genai.Client(api_key=api_key, http_options=http_options(timeout_s))
        return cls(client.aio.models, model=model)

    @property
    def provider(self) -> LLMProvider:
        return "gemini"

    async def extract(self, pages: Sequence[PageImage]) -> ExtractionOutput:
        contents = [
            *(types.Part.from_bytes(data=page.data, mime_type=page.mime_type) for page in pages),
            USER_PROMPT,
        ]
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=LlmExtraction,
            temperature=0,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        started = time.perf_counter()
        response = await self._generate_with_retry(contents, config)
        _log_usage(response, self._model, started)
        return to_extraction_output(_parse(response), model=self._model)

    async def _generate_with_retry(
        self, contents: list[Any], config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        for attempt in (1, 2):
            try:
                return await self._models.generate_content(
                    model=self._model, contents=contents, config=config
                )
            except Exception as exc:
                if attempt == 1 and _is_transient(exc):
                    delay = RETRY_BASE_DELAY_S * (1 + random.random())  # noqa: S311 - jitter
                    logger.warning("Gemini: error transitorio, reintento en %.1f s", delay)
                    await self._sleep(delay)
                    continue
                raise _map_error(exc) from exc
        raise AssertionError  # pragma: no cover - el bucle siempre retorna o lanza


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, genai_errors.APIError):
        return exc.code in TRANSIENT_STATUS_CODES
    return isinstance(exc, NETWORK_ERRORS)


def _map_error(exc: Exception) -> Exception:
    """Traduce errores del SDK a errores del contrato."""
    if isinstance(exc, genai_errors.APIError):
        if exc.code == 429:  # noqa: PLR2004
            retry_after = _retry_after_s(exc.details)
            params = {"retry_after_s": retry_after} if retry_after else {}
            headers = {"Retry-After": str(retry_after)} if retry_after else {}
            return AppError(ErrorCode.LLM_QUOTA_EXCEEDED, params=params, headers=headers)
        if exc.code == 400:  # noqa: PLR2004 - p. ej. imagen rechazada por el modelo
            logger.warning("Gemini rechazó la petición (400 %s)", exc.status)
            return ExtractionFailedError()
        logger.error("Gemini no disponible (%s %s)", exc.code, exc.status)
        return AppError(ErrorCode.LLM_UNAVAILABLE)
    if isinstance(exc, NETWORK_ERRORS):
        logger.error("Gemini no responde (%s)", type(exc).__name__)
        return AppError(ErrorCode.LLM_UNAVAILABLE)
    return exc


def _retry_after_s(details: Any) -> int | None:
    """Lee `RetryInfo.retryDelay` (p. ej. `"31s"`) del cuerpo de error de Google."""
    error = details.get("error", details) if isinstance(details, dict) else {}
    for detail in error.get("details", []) if isinstance(error, dict) else []:
        delay = detail.get("retryDelay") if isinstance(detail, dict) else None
        if isinstance(delay, str) and (match := re.fullmatch(r"(\d+)(\.\d+)?s", delay)):
            return int(match.group(1)) + (1 if match.group(2) else 0)
    return None


def _parse(response: types.GenerateContentResponse) -> LlmExtraction:
    candidate = response.candidates[0] if response.candidates else None
    finish = candidate.finish_reason if candidate else None
    if finish not in (None, types.FinishReason.STOP):
        logger.warning("Gemini terminó sin resultado utilizable (finish_reason=%s)", finish)
        raise ExtractionFailedError
    text = response.text
    if not text:
        raise ExtractionFailedError
    try:
        return LlmExtraction.model_validate_json(text)
    except ValidationError as exc:
        logger.warning("Gemini devolvió un JSON que no cumple el esquema")
        raise ExtractionFailedError from exc


def _log_usage(response: types.GenerateContentResponse, model: str, started: float) -> None:
    usage = response.usage_metadata
    logger.info(
        "Gemini %s: %d ms, tokens entrada=%s salida=%s razonamiento=%s",
        model,
        round((time.perf_counter() - started) * 1000),
        usage.prompt_token_count if usage else None,
        usage.candidates_token_count if usage else None,
        usage.thoughts_token_count if usage else None,
    )
