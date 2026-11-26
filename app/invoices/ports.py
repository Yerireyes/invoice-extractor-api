"""Puerto de extracción: lo que el caso de uso necesita de cualquier proveedor de IA."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.core.config import LLMProvider
from app.invoices.schemas import Invoice
from app.shared.files import PageImage


@dataclass(frozen=True, slots=True)
class ExtractionOutput:
    invoice: Invoice
    model: str
    field_confidence: Mapping[str, float] = field(default_factory=dict)


class NotAnInvoiceError(Exception):
    """El documento no es una factura, ticket ni nota de crédito."""


class ExtractionFailedError(Exception):
    """El proveedor respondió, pero sin un resultado utilizable."""


class InvoiceExtractor(Protocol):
    """Extrae una factura de las páginas (imágenes) de un documento.

    Errores esperados: `NotAnInvoiceError`, `ExtractionFailedError` (por archivo) y `AppError`
    con `LLM_QUOTA_EXCEEDED` / `LLM_UNAVAILABLE` (afectan a toda la petición).
    """

    @property
    def provider(self) -> LLMProvider: ...

    async def extract(self, pages: Sequence[PageImage]) -> ExtractionOutput: ...


type ExtractorFactory = Callable[[LLMProvider | None], InvoiceExtractor]
"""Devuelve el extractor del proveedor pedido (o el por defecto si es `None`)."""
