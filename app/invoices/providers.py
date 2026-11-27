"""Selección del adaptador de IA a partir de la configuración (inyectada con `Depends`)."""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.core.config import LLMProvider, Settings, get_settings
from app.core.errors import AppError, ErrorCode
from app.invoices.adapters.fake import FakeExtractor
from app.invoices.adapters.gemini import GeminiExtractor
from app.invoices.adapters.ollama import OllamaExtractor, OllamaStatus, new_client
from app.invoices.ports import ExtractorFactory, InvoiceExtractor


@lru_cache(maxsize=4)
def _gemini(api_key: str, model: str, timeout_s: float) -> GeminiExtractor:
    """Un cliente por configuración: reutiliza conexiones HTTP entre peticiones."""
    return GeminiExtractor.from_api_key(api_key, model=model, timeout_s=timeout_s)


@lru_cache(maxsize=4)
def _ollama(base_url: str, model: str, timeout_s: float) -> OllamaExtractor:
    return OllamaExtractor(new_client(base_url, timeout_s=timeout_s), model=model)


@lru_cache(maxsize=4)
def _ollama_status(base_url: str, model: str) -> OllamaStatus:
    """Un estado por configuración: la caché del sondeo se comparte entre peticiones."""
    return OllamaStatus(new_client(base_url, timeout_s=10), model=model)


async def get_ollama_available(settings: Annotated[Settings, Depends(get_settings)]) -> bool:
    """Dependencia de `/meta`: habilitado (`OLLAMA_ENABLED`), responde y tiene el modelo."""
    if not settings.ollama_enabled:
        return False
    return await _ollama_status(settings.ollama_base_url, settings.ollama_model.strip()).available()


def get_extractor_factory(settings: Annotated[Settings, Depends(get_settings)]) -> ExtractorFactory:
    """Dependencia de FastAPI. En tests se sobrescribe con `app.dependency_overrides`."""

    def build(requested: LLMProvider | None) -> InvoiceExtractor:
        provider = requested or settings.llm_provider
        # El extractor falso solo existe cuando es el proveedor activo (nunca en producción).
        if provider == "fake" and settings.llm_provider == "fake":
            return FakeExtractor()
        if provider == "gemini" and settings.gemini_configured:
            return _gemini(
                settings.gemini_api_key.get_secret_value(),
                settings.gemini_model.strip(),
                settings.gemini_timeout_s,
            )
        # Ollama solo depende del flag: si está caído, `/extract` responde `LLM_UNAVAILABLE`.
        if provider == "ollama" and settings.ollama_enabled:
            return _ollama(
                settings.ollama_base_url,
                settings.ollama_model.strip(),
                settings.ollama_timeout_s,
            )
        raise AppError(ErrorCode.PROVIDER_NOT_AVAILABLE, params={"provider": provider})

    return build
