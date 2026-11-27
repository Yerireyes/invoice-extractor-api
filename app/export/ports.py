"""Puerto de exportación: lo que el caso de uso necesita de cualquier formato (Excel, PDF)."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.export.schemas import ExportInvoice, ExportOptions
from app.invoices.schemas import Validation


@dataclass(frozen=True, slots=True)
class ExportDocument:
    """Factura lista para exportar: con su Ref (`F-001`...) y su validación recalculada."""

    ref: str
    invoice: ExportInvoice
    validation: Validation


class Exporter(Protocol):
    @property
    def media_type(self) -> str: ...

    @property
    def extension(self) -> str: ...

    def export(self, documents: Sequence[ExportDocument], options: ExportOptions) -> bytes: ...
