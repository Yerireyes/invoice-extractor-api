"""Caso de uso de exportación: facturas → Ref + validación → archivo."""

from collections.abc import Sequence
from datetime import date, datetime

from app.export.ports import ExportDocument, Exporter
from app.export.schemas import ExportInvoice, ExportOptions
from app.invoices.validation import validate_invoice


def build_documents(invoices: Sequence[ExportInvoice], *, today: date) -> list[ExportDocument]:
    """Asigna Refs por orden (`F-001`, `F-002`...) y revalida cada factura.

    La exportación no confía en una validación enviada por el cliente: la recalcula.
    """
    width = max(3, len(str(len(invoices))))
    return [
        ExportDocument(
            ref=f"F-{index:0{width}d}",
            invoice=invoice,
            validation=validate_invoice(invoice, today=today),
        )
        for index, invoice in enumerate(invoices, start=1)
    ]


def export_invoices(
    exporter: Exporter,
    invoices: Sequence[ExportInvoice],
    options: ExportOptions,
    *,
    today: date,
) -> bytes:
    return exporter.export(build_documents(invoices, today=today), options)


def export_file_name(exporter: Exporter, now: datetime) -> str:
    """`facturas_AAAAMMDD_HHMM.<ext>` (sección 2.4 del contrato)."""
    return f"facturas_{now:%Y%m%d_%H%M}.{exporter.extension}"
