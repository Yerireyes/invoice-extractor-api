"""Selección del adaptador de IA a partir de la configuración (inyectada con `Depends`)."""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.core.config import LLMProvider, Settings, get_settings
from app.core.errors import AppError, ErrorCode
from app.invoices.adapters.fake import FakeExtractor
from app.invoices.adapters.gemini import GeminiExtractor
from app.invoices.ports import ExtractorFactory, InvoiceExtractor


@lru_cache(maxsize=4)
def _gemini(api_key: str, model: str, timeout_s: float) -> GeminiExtractor:
    """Un cliente por configuración: reutiliza conexiones HTTP entre peticiones."""
    return GeminiExtractor.from_api_key(api_key, model=model, timeout_s=timeout_s)


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
        # El adaptador de Ollama llega en API-INV-0013.
        raise AppError(ErrorCode.PROVIDER_NOT_AVAILABLE, params={"provider": provider})

    return build
