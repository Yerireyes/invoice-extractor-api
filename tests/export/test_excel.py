import io
import zipfile
from datetime import datetime

import pytest
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from app.export.adapters.excel import XLSX_MEDIA_TYPE, ExcelExporter
from app.export.ports import ExportDocument
from app.export.schemas import ExportOptions
from app.export.service import build_documents
from tests.export.conftest import TODAY, export_invoice

SUMMARY_HEADERS_ES = [
    "Ref",
    "Archivo",
    "Tipo",
    "Número",
    "Fecha emisión",
    "Vencimiento",
    "Emisor",
    "ID fiscal emisor",
    "Receptor",
    "ID fiscal receptor",
    "Moneda",
    "Subtotal",
    "Descuentos",
    "Impuestos",
    "Total",
    "Método de pago",
    "Estado validación",
    "Nº alertas",
]


def _export(documents: list[ExportDocument], **options: object) -> bytes:
    return ExcelExporter().export(documents, ExportOptions.model_validate(options))


def _open(content: bytes) -> Workbook:
    return load_workbook(io.BytesIO(content))


def _rows(ws: Worksheet) -> list[list[object]]:
    return [list(row) for row in ws.iter_rows(min_row=2, values_only=True)]


def _headers(ws: Worksheet) -> list[object]:
    return [cell.value for cell in ws[1]]


# --- Estructura ------------------------------------------------------------------------------


def test_metadata() -> None:
    exporter = ExcelExporter()

    assert exporter.media_type == XLSX_MEDIA_TYPE
    assert exporter.extension == "xlsx"


def test_sheets_in_spanish(documents: list[ExportDocument]) -> None:
    workbook = _open(_export(documents))

    assert workbook.sheetnames == [
        "Resumen",
        "Conceptos",
        "Impuestos",
        "Totales por moneda",
        "Alertas",
    ]
    assert _headers(workbook["Resumen"]) == SUMMARY_HEADERS_ES


def test_sheets_and_headers_in_english(documents: list[ExportDocument]) -> None:
    workbook = _open(_export(documents, language="en"))

    assert workbook.sheetnames == ["Summary", "Line items", "Taxes", "Totals by currency", "Alerts"]
    assert _headers(workbook["Summary"])[:5] == ["Ref", "File", "Type", "Number", "Issue date"]
    assert _headers(workbook["Totals by currency"]) == [
        "Currency",
        "Invoices",
        "Subtotal",
        "Discounts",
        "Taxes",
        "Total",
    ]


def test_optional_sheets_can_be_omitted(documents: list[ExportDocument]) -> None:
    workbook = _open(_export(documents, include_line_items=False, include_alerts=False))

    assert workbook.sheetnames == ["Resumen", "Impuestos", "Totales por moneda"]


def test_every_sheet_is_a_table_with_frozen_bold_header(documents: list[ExportDocument]) -> None:
    workbook = _open(_export(documents))

    names = set()
    for ws in workbook.worksheets:
        assert len(ws.tables) == 1, ws.title
        table = next(iter(ws.tables.values()))
        names.add(table.displayName)
        assert table.ref.startswith("A1:")
        assert ws.freeze_panes == "A2"
        assert all(cell.font.bold for cell in ws[1])
        widths = [
            ws.column_dimensions[get_column_letter(i)].width for i in range(1, ws.max_column + 1)
        ]
        assert all(width >= 8 for width in widths)
    assert len(names) == 5


def test_formulas_recalculate_on_open(documents: list[ExportDocument]) -> None:
    workbook = _open(_export(documents))

    assert workbook.calculation.fullCalcOnLoad


# --- Resumen -------------------------------------------------------------------------------


def test_summary_rows_and_types(documents: list[ExportDocument]) -> None:
    ws = _open(_export(documents))["Resumen"]

    rows = _rows(ws)
    assert [row[0] for row in rows] == ["F-001", "F-002", "F-003", "F-004"]
    first = dict(zip(SUMMARY_HEADERS_ES, rows[0], strict=True))
    assert first["Archivo"] == "luz.jpg"
    assert first["Tipo"] == "Factura"
    assert isinstance(first["Fecha emisión"], datetime)
    assert first["Fecha emisión"].isoformat() == "2026-11-20T00:00:00"
    assert first["Subtotal"] == 1000
    assert first["Impuestos"] == 160
    assert first["Total"] == 1160
    assert first["Estado validación"] == "Válida"
    assert first["Nº alertas"] == 0
    assert isinstance(first["Total"], int | float)
    assert ws["E2"].number_format == "yyyy-mm-dd"
    assert ws["O2"].number_format == "#,##0.00"


def test_summary_statuses_and_alert_counts(documents: list[ExportDocument]) -> None:
    rows = _rows(_open(_export(documents))["Resumen"])

    assert [(row[16], row[17]) for row in rows] == [
        ("Válida", 0),
        ("Inválida", 1),
        ("Con avisos", 1),
        ("Válida", 0),
    ]
    assert rows[3][2] == "Ticket"
    assert rows[3][3] is None


def test_zero_decimal_currency_format(documents: list[ExportDocument]) -> None:
    ws = _open(_export(documents))["Resumen"]

    assert ws["K5"].value == "JPY"
    assert ws["O5"].number_format == "#,##0"


def test_conditional_formatting_highlights_invalid_and_warnings(
    documents: list[ExportDocument],
) -> None:
    ws = _open(_export(documents))["Resumen"]

    formulas = sorted(
        rule.formula[0] for rules in ws.conditional_formatting for rule in rules.rules
    )
    assert formulas == ['$Q2="Con avisos"', '$Q2="Inválida"']
    ranges = {str(rules.sqref) for rules in ws.conditional_formatting}
    assert ranges == {"A2:R5"}


def test_text_is_never_written_as_a_formula() -> None:
    malicious = export_invoice(
        issuer={"name": '=HYPERLINK("http://malo.example","clic")'},
        invoice_number="+1+1",
        notes="@SUM(A1)",
    )
    content = _export(build_documents([malicious], today=TODAY))

    ws = _open(content)["Resumen"]
    assert ws["G2"].value == '=HYPERLINK("http://malo.example","clic")'
    assert ws["G2"].data_type == "s"
    assert ws["D2"].data_type == "s"
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode()
    assert "<f>" not in sheet_xml


# --- Conceptos, impuestos y alertas -----------------------------------------------------------


def test_line_items_are_numbered_per_invoice(documents: list[ExportDocument]) -> None:
    rows = _rows(_open(_export(documents))["Conceptos"])

    eur = [row for row in rows if row[0] == "F-003"]
    assert [(row[3], row[4], row[10], row[11]) for row in eur] == [
        (1, "Licencia", 100, "EUR"),
        (2, "Soporte", 50, "EUR"),
    ]
    assert len(rows) == 5


def test_taxes_sheet(documents: list[ExportDocument]) -> None:
    rows = _rows(_open(_export(documents))["Impuestos"])

    assert [row[0] for row in rows] == ["F-001", "F-002", "F-003"]  # el ticket no tiene impuestos
    assert rows[2][3:] == ["IVA", 21, 140, 29.4, "EUR"]


def test_empty_sheet_keeps_a_valid_table() -> None:
    invoice = export_invoice(taxes=[], total="1000.00")
    ws = _open(_export(build_documents([invoice], today=TODAY)))["Impuestos"]

    assert next(iter(ws.tables.values())).ref == "A1:H2"


def test_alerts_are_translated(documents: list[ExportDocument]) -> None:
    rows = _rows(_open(_export(documents))["Alertas"])

    assert rows == [
        [
            "F-002",
            "A-2",
            "Error",
            "TOTAL_MISMATCH",
            "Total",
            "El total no cuadra: subtotal − descuentos + impuestos = 1160.00, "
            "pero la factura indica 1150.00.",
        ],
        [
            "F-003",
            "2026/7",
            "Aviso",
            "DUE_BEFORE_ISSUE",
            "Vencimiento",
            "La fecha de vencimiento es anterior a la de emisión.",
        ],
    ]


# --- Totales por moneda -----------------------------------------------------------------------


def test_totals_use_formulas_per_currency(documents: list[ExportDocument]) -> None:
    ws = _open(_export(documents))["Totales por moneda"]

    rows = _rows(ws)
    assert [row[0] for row in rows] == ["MXN", "EUR", "JPY"]
    count, subtotal, discounts, taxes, total = rows[0][1:]
    summary = "'Resumen'!$K$2:$K$5"
    assert count == f"=COUNTIF({summary},$A2)"
    assert subtotal == f"=SUMIF({summary},$A2,'Resumen'!$L$2:$L$5)"
    assert discounts == f"=SUMIF({summary},$A2,'Resumen'!$M$2:$M$5)"
    assert taxes == f"=SUMIF({summary},$A2,'Resumen'!$N$2:$N$5)"
    assert total == f"=SUMIF({summary},$A2,'Resumen'!$O$2:$O$5)"
    assert ws["F4"].number_format == "#,##0"  # JPY
    assert ws["F2"].number_format == "#,##0.00"


def test_totals_formulas_add_up_per_currency(documents: list[ExportDocument]) -> None:
    """Evalúa las fórmulas SUMIF sobre los valores de Resumen: nunca se mezclan monedas."""
    workbook = _open(_export(documents))
    summary = _rows(workbook["Resumen"])

    def sumif(currency: str, column: int) -> float:
        return sum(row[column] or 0 for row in summary if row[10] == currency)  # type: ignore[misc]

    expected = {"MXN": 1160 + 1150, "EUR": 169.4, "JPY": 320}
    for row in _rows(workbook["Totales por moneda"]):
        currency = str(row[0])
        assert str(row[5]).endswith("$O$2:$O$5)")
        assert sumif(currency, 14) == pytest.approx(expected[currency])


def test_invoices_without_currency_are_not_totalled() -> None:
    documents = build_documents([export_invoice(currency=None)], today=TODAY)

    ws = _open(_export(documents))["Totales por moneda"]

    assert _rows(ws) == []
    assert next(iter(ws.tables.values())).ref == "A1:F2"
