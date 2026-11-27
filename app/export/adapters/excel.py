"""Exportador Excel (sección 7 del contrato) con openpyxl. No importa FastAPI.

- Hojas: Resumen, Conceptos (opcional), Impuestos, Totales por moneda y Alertas (opcional).
- Cada hoja es una Tabla de Excel con filtros, encabezado en negrita y congelado, anchos ajustados.
- Fechas como fechas reales y montos como números (`#,##0.00`, o `#,##0` en monedas sin decimales).
- *Totales por moneda* usa fórmulas `COUNTIF`/`SUMIF` sobre *Resumen*: nunca se suman monedas.
- Seguridad: los textos (que vienen de la IA o del usuario) se guardan siempre como texto, nunca
  como fórmula, aunque empiecen por `=`, `+`, `-` o `@` (evita la inyección de fórmulas).
"""

import io
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.workbook.properties import CalcProperties
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.worksheet.worksheet import Worksheet

from app.export.i18n import ExportLanguage, field_label, issue_message, text
from app.export.ports import ExportDocument
from app.export.schemas import ExportOptions
from app.invoices.currencies import has_decimals

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

DATE_FORMAT = "yyyy-mm-dd"
MONEY_FORMAT = "#,##0.00"
MONEY_FORMAT_NO_DECIMALS = "#,##0"
TABLE_STYLE = "TableStyleMedium2"
INVALID_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
WARNINGS_FILL = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
MIN_WIDTH, MAX_WIDTH = 8, 60


@dataclass(frozen=True, slots=True)
class Column:
    key: str
    kind: str = "text"  # text | date | money | number | int


SUMMARY_COLUMNS = (
    Column("col.ref"),
    Column("col.file"),
    Column("col.type"),
    Column("col.number"),
    Column("col.issue_date", "date"),
    Column("col.due_date", "date"),
    Column("col.issuer"),
    Column("col.issuer_tax_id"),
    Column("col.recipient"),
    Column("col.recipient_tax_id"),
    Column("col.currency"),
    Column("col.subtotal", "money"),
    Column("col.discounts", "money"),
    Column("col.taxes", "money"),
    Column("col.total", "money"),
    Column("col.payment_method"),
    Column("col.validation"),
    Column("col.alert_count", "int"),
)
LINE_COLUMNS = (
    Column("col.ref"),
    Column("col.invoice_number"),
    Column("col.issuer"),
    Column("col.line", "int"),
    Column("col.description"),
    Column("col.quantity", "number"),
    Column("col.unit"),
    Column("col.unit_price", "money"),
    Column("col.discount", "money"),
    Column("col.tax_rate", "number"),
    Column("col.amount", "money"),
    Column("col.currency"),
)
TAX_COLUMNS = (
    Column("col.ref"),
    Column("col.invoice_number"),
    Column("col.issuer"),
    Column("col.tax"),
    Column("col.rate", "number"),
    Column("col.base", "money"),
    Column("col.amount", "money"),
    Column("col.currency"),
)
TOTALS_COLUMNS = (
    Column("col.currency"),
    Column("col.invoice_count", "int"),
    Column("col.subtotal", "money"),
    Column("col.discounts", "money"),
    Column("col.taxes", "money"),
    Column("col.total", "money"),
)
ALERT_COLUMNS = (
    Column("col.ref"),
    Column("col.invoice_number"),
    Column("col.severity"),
    Column("col.code"),
    Column("col.field"),
    Column("col.message"),
)

# Posición (1-based) en *Resumen* de las columnas que usan las fórmulas de totales.
_SUMMARY_INDEX = {column.key: index for index, column in enumerate(SUMMARY_COLUMNS, start=1)}


class ExcelExporter:
    """Implementa el puerto `Exporter` para `.xlsx`."""

    @property
    def media_type(self) -> str:
        return XLSX_MEDIA_TYPE

    @property
    def extension(self) -> str:
        return "xlsx"

    def export(self, documents: Sequence[ExportDocument], options: ExportOptions) -> bytes:
        lang = options.language
        workbook = Workbook()
        workbook.properties.creator = "Invoice Extractor API"
        workbook.calculation = CalcProperties(fullCalcOnLoad=True)

        summary = workbook.worksheets[0]
        _fill_summary(summary, documents, lang)
        if options.include_line_items:
            _fill_line_items(workbook.create_sheet(), documents, lang)
        _fill_taxes(workbook.create_sheet(), documents, lang)
        _fill_totals(workbook.create_sheet(), documents, lang, summary_title=summary.title)
        if options.include_alerts:
            _fill_alerts(workbook.create_sheet(), documents, lang)

        output = io.BytesIO()
        workbook.save(output)
        return output.getvalue()


# ---------------------------------------------------------------------------
# Hojas
# ---------------------------------------------------------------------------


def _fill_summary(ws: Worksheet, documents: Sequence[ExportDocument], lang: ExportLanguage) -> None:
    rows = []
    for doc in documents:
        invoice = doc.invoice
        recipient = invoice.recipient
        rows.append(
            (
                [
                    doc.ref,
                    invoice.source_file_name,
                    text(lang, f"type.{invoice.document_type}"),
                    invoice.invoice_number,
                    invoice.issue_date,
                    invoice.due_date,
                    invoice.issuer.name,
                    invoice.issuer.tax_id,
                    recipient.name if recipient else None,
                    recipient.tax_id if recipient else None,
                    invoice.currency,
                    invoice.subtotal,
                    invoice.discount_total,
                    sum((tax.amount for tax in invoice.taxes), Decimal(0)),
                    invoice.total,
                    invoice.payment_method,
                    text(lang, f"status.{doc.validation.status}"),
                    len(doc.validation.issues),
                ],
                invoice.currency,
            )
        )
    last_row = _write_sheet(ws, "sheet.summary", "tblSummary", SUMMARY_COLUMNS, rows, lang=lang)

    status_col = get_column_letter(_SUMMARY_INDEX["col.validation"])
    area = f"A2:{get_column_letter(len(SUMMARY_COLUMNS))}{last_row}"
    for status, fill in (("invalid", INVALID_FILL), ("warnings", WARNINGS_FILL)):
        formula = f'${status_col}2="{text(lang, f"status.{status}")}"'
        rule = FormulaRule(formula=[formula], fill=fill)  # type: ignore[no-untyped-call]  # stubs
        ws.conditional_formatting.add(area, rule)


def _fill_line_items(
    ws: Worksheet, documents: Sequence[ExportDocument], lang: ExportLanguage
) -> None:
    rows = [
        (
            [
                doc.ref,
                doc.invoice.invoice_number,
                doc.invoice.issuer.name,
                number,
                line.description,
                line.quantity,
                line.unit,
                line.unit_price,
                line.discount,
                line.tax_rate,
                line.amount,
                doc.invoice.currency,
            ],
            doc.invoice.currency,
        )
        for doc in documents
        for number, line in enumerate(doc.invoice.line_items, start=1)
    ]
    _write_sheet(ws, "sheet.line_items", "tblLineItems", LINE_COLUMNS, rows, lang=lang)


def _fill_taxes(ws: Worksheet, documents: Sequence[ExportDocument], lang: ExportLanguage) -> None:
    rows = [
        (
            [
                doc.ref,
                doc.invoice.invoice_number,
                doc.invoice.issuer.name,
                tax.name,
                tax.rate,
                tax.base,
                tax.amount,
                doc.invoice.currency,
            ],
            doc.invoice.currency,
        )
        for doc in documents
        for tax in doc.invoice.taxes
    ]
    _write_sheet(ws, "sheet.taxes", "tblTaxes", TAX_COLUMNS, rows, lang=lang)


def _fill_totals(
    ws: Worksheet,
    documents: Sequence[ExportDocument],
    lang: ExportLanguage,
    *,
    summary_title: str,
) -> None:
    """Una fila por moneda con fórmulas sobre *Resumen* (se recalculan al abrir el archivo)."""
    last = len(documents) + 1

    def column_range(key: str) -> str:
        letter = get_column_letter(_SUMMARY_INDEX[key])
        return f"'{summary_title}'!${letter}$2:${letter}${last}"

    currencies = list(
        dict.fromkeys(doc.invoice.currency for doc in documents if doc.invoice.currency)
    )
    rows = []
    for row_number, currency in enumerate(currencies, start=2):
        key = f"$A{row_number}"
        currency_range = column_range("col.currency")
        rows.append(
            (
                [
                    currency,
                    f"=COUNTIF({currency_range},{key})",
                    *(
                        f"=SUMIF({currency_range},{key},{column_range(column)})"
                        for column in ("col.subtotal", "col.discounts", "col.taxes", "col.total")
                    ),
                ],
                currency,
            )
        )
    _write_sheet(ws, "sheet.totals", "tblTotals", TOTALS_COLUMNS, rows, lang=lang, formulas=True)


def _fill_alerts(ws: Worksheet, documents: Sequence[ExportDocument], lang: ExportLanguage) -> None:
    rows = [
        (
            [
                doc.ref,
                doc.invoice.invoice_number,
                text(lang, f"severity.{issue.severity}"),
                issue.code.value,
                field_label(lang, issue.field),
                issue_message(lang, issue),
            ],
            None,
        )
        for doc in documents
        for issue in doc.validation.issues
    ]
    _write_sheet(ws, "sheet.alerts", "tblAlerts", ALERT_COLUMNS, rows, lang=lang)


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------


def _write_sheet(
    ws: Worksheet,
    title_key: str,
    table_name: str,
    columns: Sequence[Column],
    rows: Sequence[tuple[Sequence[Any], str | None]],
    *,
    lang: ExportLanguage,
    formulas: bool = False,
) -> int:
    """Escribe encabezado + filas como Tabla de Excel. Devuelve la última fila de la tabla."""
    ws.title = text(lang, title_key)
    headers = [text(lang, column.key) for column in columns]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    widths = [len(header) for header in headers]
    for row_index, (values, currency) in enumerate(rows, start=2):
        for col_index, (column, value) in enumerate(zip(columns, values, strict=True), start=1):
            cell = ws.cell(row=row_index, column=col_index)
            is_formula = formulas and isinstance(value, str) and value.startswith("=")
            cell.value = value
            if isinstance(value, str) and not is_formula:
                cell.data_type = "s"  # nunca interpretar texto como fórmula
            cell.number_format = _number_format(column, currency)
            if not is_formula:
                widths[col_index - 1] = max(widths[col_index - 1], len(_display(value)))

    last_row = max(len(rows) + 1, 2)  # una Tabla de Excel necesita al menos una fila de datos
    reference = f"A1:{get_column_letter(len(columns))}{last_row}"
    table = Table(displayName=table_name, ref=reference)
    table.tableStyleInfo = TableStyleInfo(name=TABLE_STYLE, showRowStripes=True)
    ws.add_table(table)
    ws.freeze_panes = "A2"
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = min(
            max(width + 2, MIN_WIDTH), MAX_WIDTH
        )
    return last_row


def _number_format(column: Column, currency: str | None) -> str:
    match column.kind:
        case "date":
            return DATE_FORMAT
        case "money":
            return MONEY_FORMAT if has_decimals(currency) else MONEY_FORMAT_NO_DECIMALS
        case "int":
            return "0"
        case _:
            return "General"


def _display(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    return str(value)
