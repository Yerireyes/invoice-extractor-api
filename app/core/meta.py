"""Metadatos públicos del servicio: límites, formatos aceptados y proveedores de IA."""

from pydantic import BaseModel

from app.core.config import LLMProvider, Settings
from app.core.version import APP_VERSION
from app.export.i18n import EXPORT_LANGUAGES, ExportLanguage
from app.shared.files import ACCEPTED_MIME_TYPES


class Limits(BaseModel):
    max_files_per_request: int
    max_file_size_mb: int
    max_pdf_pages: int
    max_invoices_per_export: int


class ProviderInfo(BaseModel):
    id: LLMProvider
    available: bool
    default: bool


class MetaResponse(BaseModel):
    version: str
    limits: Limits
    accepted_mime_types: list[str]
    providers: list[ProviderInfo]
    export_languages: list[ExportLanguage]


def _providers(settings: Settings, *, ollama_available: bool) -> list[ProviderInfo]:
    default = settings.llm_provider
    providers = [
        ProviderInfo(
            id="gemini",
            available=settings.gemini_configured,
            default=default == "gemini",
        ),
        ProviderInfo(id="ollama", available=ollama_available, default=default == "ollama"),
    ]
    # El extractor falso solo se anuncia cuando está activo (desarrollo sin clave de Gemini).
    if default == "fake":
        providers.insert(0, ProviderInfo(id="fake", available=True, default=True))
    return providers


def build_meta(settings: Settings, *, ollama_available: bool = False) -> MetaResponse:
    return MetaResponse(
        version=APP_VERSION,
        limits=Limits(
            max_files_per_request=settings.max_files_per_request,
            max_file_size_mb=settings.max_file_size_mb,
            max_pdf_pages=settings.max_pdf_pages,
            max_invoices_per_export=settings.max_invoices_per_export,
        ),
        accepted_mime_types=list(ACCEPTED_MIME_TYPES),
        providers=_providers(settings, ollama_available=ollama_available),
        export_languages=list(EXPORT_LANGUAGES),
    )
