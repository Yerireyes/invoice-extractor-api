from datetime import date
from decimal import Decimal
from typing import Any

import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pytest

from app.export.adapters.pdf import PDF_MEDIA_TYPE, PdfExporter, _money, _number
from app.export.ports import ExportDocument
from app.export.schemas import ExportOptions
from app.export.service import build_documents
from tests.export.conftest import TODAY, export_invoice

GENERATED_ON = date(2026, 11, 27)


def _render(documents: list[ExportDocument], **options: Any) -> bytes:
    return PdfExporter(generated_on=GENERATED_ON).export(
        documents, ExportOptions.model_validate(options)
    )


def _pages(content: bytes) -> list[str]:
    """Texto de cada página, extraído con pypdfium2 (demuestra que la fuente está incrustada)."""
    pdf = pdfium.PdfDocument(content)
    try:
        return [page.get_textpage().get_text_range() for page in pdf]
    finally:
        pdf.close()


def _flat(page: str) -> str:
    """Une las líneas: un párrafo largo puede partirse en varias."""
    return " ".join(page.split())


def test_exporter_metadata() -> None:
    exporter = PdfExporter(generated_on=GENERATED_ON)
    assert exporter.media_type == PDF_MEDIA_TYPE == "application/pdf"
    assert exporter.extension == "pdf"


def test_one_invoice_per_page(documents: list[ExportDocument]) -> None:
    content = _render(documents)

    assert content.startswith(b"%PDF-")
    pages = _pages(content)
    assert len(pages) == len(documents) == 4
    for number, (page, doc) in enumerate(zip(pages, documents, strict=True), start=1):
        assert doc.ref in page
        assert f"Página {number} de 4" in page
        assert "Generado el 2026-11-27" in page


def test_page_content(documents: list[ExportDocument]) -> None:
    first, second, third, ticket = (_flat(page) for page in _pages(_render(documents)))

    # Encabezado, emisor y datos generales
    assert "Factura A-00123" in first
    assert "F-001 · Válida" in first
    assert "Fecha emisión: 2026-11-20" in first
    assert "Vencimiento: 2026-12-20" in first
    assert "Archivo: luz.jpg" in first
    assert "Comercial XYZ S.A." in first
    assert "ID fiscal: XYZ010101ABC (RFC)" in first
    # Conceptos, impuestos y totales
    assert "Servicio de consultoría" in first
    assert "500.00" in first
    assert "IVA" in first
    assert "1,160.00 MXN" in first
    assert "Sin alertas" in first

    assert "F-002 · Inválida" in second
    assert "El total no cuadra" in second

    assert "Factura 2026/7" in third
    assert "F-003 · Con avisos" in third
    assert "169.40 EUR" in third
    assert "La fecha de vencimiento es anterior a la de emisión." in third

    # Ticket en JPY: sin número, sin receptor, sin impuestos y sin decimales
    assert "Ticket" in ticket
    assert "320 JPY" in ticket
    assert "320.00" not in ticket


def test_english(documents: list[ExportDocument]) -> None:
    first = _flat(_pages(_render(documents, language="en"))[0])

    assert "Invoice A-00123" in first
    assert "Issuer" in first
    assert "Recipient" in first
    assert "Issue date: 2026-11-20" in first
    assert "No alerts" in first
    assert "Page 1 of 4" in first
    assert "Generated on 2026-11-27" in first


def test_optional_sections(documents: list[ExportDocument]) -> None:
    pages = [_flat(page) for page in _pages(_render(documents, include_line_items=False))]
    assert "Servicio de consultoría" not in pages[0]
    assert "Conceptos" not in pages[0]
    assert "1,160.00 MXN" in pages[0]  # los totales siguen

    pages = [_flat(page) for page in _pages(_render(documents, include_alerts=False))]
    assert all("Alertas" not in page for page in pages)
    assert "El total no cuadra" not in pages[1]


def test_unicode_text() -> None:
    invoice = export_invoice(
        currency="EUR",
        issuer={"name": "Panadería Ñandú — Café €", "address": "Straße 5, Zürich"},
        recipient={"name": "Ελληνική Εταιρεία", "address": "Москва"},
        line_items=[
            {"description": "Тест ₹ ₩ ₫ ₪ ₺", "quantity": "1", "unit_price": "10", "amount": "10"}
        ],
        subtotal="10",
        discount_total=None,
        taxes=[],
        total="10",
    )
    page = _flat(_pages(_render(build_documents([invoice], today=TODAY)))[0])

    for value in (
        "Panadería Ñandú — Café €",
        "Straße 5, Zürich",
        "Ελληνική Εταιρεία",
        "Москва",
        "Тест ₹ ₩ ₫ ₪ ₺",
    ):
        assert value in page


def test_text_is_not_interpreted_as_markup() -> None:
    invoice = export_invoice(
        issuer={"name": '<b>ACME</b> & Co <font color="red">x</font>'},
        notes="<br/>Pago a 30 días & sin recargo",
    )
    page = _flat(_pages(_render(build_documents([invoice], today=TODAY)))[0])

    assert '<b>ACME</b> & Co <font color="red">x</font>' in page
    assert "Notas" in page
    assert "<br/>Pago a 30 días & sin recargo" in page


def test_missing_data_does_not_break() -> None:
    invoice = export_invoice(
        invoice_number=None,
        issue_date=None,
        currency=None,
        issuer={},
        recipient={"name": None},
        line_items=[{"description": "", "amount": "5"}],
        subtotal=None,
        discount_total=None,
        taxes=[{"amount": "1"}],
        total=None,
        payment_method=None,
    )
    page = _flat(_pages(_render(build_documents([invoice], today=TODAY)))[0])

    assert "Factura" in page
    assert "Fecha emisión: —" in page
    assert "Moneda: —" in page
    assert "Falta un dato obligatorio" in page


def test_long_invoice_continues_and_next_starts_on_new_page() -> None:
    lines = [
        {"description": f"Concepto {n}", "quantity": "1", "unit_price": "1", "amount": "1"}
        for n in range(1, 81)
    ]
    long_invoice = export_invoice(
        line_items=lines,
        subtotal="80",
        taxes=[{"name": "IVA", "rate": "16", "base": "80", "amount": "12.80"}],
        total="92.80",
    )
    documents = build_documents([long_invoice, export_invoice(invoice_number="B-1")], today=TODAY)
    pages = [_flat(page) for page in _pages(_render(documents))]

    assert len(pages) >= 3
    assert "Concepto 1 " in pages[0]
    assert "Concepto 80" in pages[-2]
    # El encabezado de la tabla se repite en la página de continuación
    assert "Descripción" in pages[1]
    assert "Factura B-1" in pages[-1]
    assert "Factura B-1" not in " ".join(pages[:-1])
    assert f"Página {len(pages)} de {len(pages)}" in pages[-1]


@pytest.mark.parametrize(
    ("value", "currency", "expected"),
    [
        ("1234.5", "MXN", "1,234.50"),
        ("1234.567", "EUR", "1,234.57"),
        ("1500", "JPY", "1,500"),
        ("0", None, "0.00"),
    ],
)
def test_money_format(value: str, currency: str | None, expected: str) -> None:
    assert _money(Decimal(value), currency) == expected


@pytest.mark.parametrize(
    ("value", "expected"), [("2.000", "2"), ("16.50", "16.5"), ("100", "100"), ("0.25", "0.25")]
)
def test_number_format(value: str, expected: str) -> None:
    assert _number(Decimal(value)) == expected
