"""Selección del adaptador de IA a partir de la configuración (inyectada con `Depends`)."""

from typing import Annotated

from fastapi import Depends

from app.core.config import LLMProvider, Settings, get_settings
from app.core.errors import AppError, ErrorCode
from app.invoices.adapters.fake import FakeExtractor
from app.invoices.ports import ExtractorFactory, InvoiceExtractor


def get_extractor_factory(settings: Annotated[Settings, Depends(get_settings)]) -> ExtractorFactory:
    """Dependencia de FastAPI. En tests se sobrescribe con `app.dependency_overrides`."""

    def build(requested: LLMProvider | None) -> InvoiceExtractor:
        provider = requested or settings.llm_provider
        # El extractor falso solo existe cuando es el proveedor activo (nunca en producción).
        if provider == "fake" and settings.llm_provider == "fake":
            return FakeExtractor()
        # Los adaptadores de Gemini y Ollama llegan en API-INV-0008 y API-INV-0013.
        raise AppError(ErrorCode.PROVIDER_NOT_AVAILABLE, params={"provider": provider})

    return build
