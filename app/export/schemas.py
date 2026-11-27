"""Esquemas de las peticiones de exportación (sección 2.4 del contrato). No importa FastAPI."""

from typing import Annotated

from pydantic import Field, StringConstraints

from app.export.i18n import ExportLanguage
from app.invoices.schemas import Invoice, Schema


class ExportOptions(Schema):
    language: ExportLanguage = "es"
    include_line_items: bool = True
    include_alerts: bool = True


class ExportInvoice(Invoice):
    """Factura a exportar. `source_file_name` (opcional) rellena la columna *Archivo*."""

    source_file_name: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] | None
    ) = Field(default=None, description="Nombre del archivo original (el de `/extract`).")


class ExportRequest(Schema):
    invoices: list[ExportInvoice] = Field(min_length=1)
    options: ExportOptions = Field(default_factory=ExportOptions)
