"""Datos de prueba sintéticos compartidos (nunca facturas reales)."""

import io
from typing import Any

from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def invoice_payload(**overrides: Any) -> dict[str, Any]:
    """Factura del ejemplo del contrato (válida y cuadrada). `overrides` reemplaza claves."""
    payload: dict[str, Any] = {
        "document_type": "invoice",
        "invoice_number": "A-00123",
        "issue_date": "2026-11-20",
        "due_date": "2026-12-20",
        "currency": "MXN",
        "country": "MX",
        "language": "es",
        "issuer": {
            "name": "Comercial XYZ S.A.",
            "tax_id": "XYZ010101ABC",
            "tax_id_type": "RFC",
            "address": "Av. Siempre Viva 123, CDMX",
            "email": None,
            "phone": None,
        },
        "recipient": None,
        "line_items": [
            {
                "description": "Servicio de consultoría",
                "quantity": "2",
                "unit": "h",
                "unit_price": "500.00",
                "discount": "0.00",
                "tax_rate": "16",
                "amount": "1000.00",
            }
        ],
        "subtotal": "1000.00",
        "discount_total": "0.00",
        "taxes": [{"name": "IVA", "rate": "16", "base": "1000.00", "amount": "160.00"}],
        "total": "1160.00",
        "payment_method": "Transferencia",
        "notes": None,
    }
    payload.update(overrides)
    return payload


def image_bytes(
    fmt: str, size: tuple[int, int] = (120, 80), mode: str = "RGB", **save: Any
) -> bytes:
    """Imagen sintética de un color en el formato indicado (JPEG, PNG, WEBP...)."""
    color: object = (200, 30, 30, 128) if mode == "RGBA" else "red"
    if mode == "P":
        color = 1
    image = Image.new(mode, size, color)  # type: ignore[arg-type]
    output = io.BytesIO()
    image.save(output, format=fmt, **save)
    return output.getvalue()


def pdf_bytes(
    pages: int = 1, *, encrypt: str | None = None, pagesize: tuple[float, float] = A4
) -> bytes:
    """PDF sintético con `pages` páginas de texto (opcionalmente cifrado)."""
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=pagesize, encrypt=encrypt)
    for number in range(1, pages + 1):
        pdf.drawString(72, 720, f"Factura sintética - página {number}")
        pdf.showPage()
    pdf.save()
    return output.getvalue()


_COLUMNS = ((60, "Cantidad"), (130, "Concepto"), (340, "P. unitario"), (450, "Importe"))


def invoice_pdf_bytes() -> bytes:
    """Factura sintética (datos ficticios) renderizada como un PDF legible por un LLM.

    Cuadra con el ejemplo del contrato: 2 h x 500.00 = 1000.00 + IVA 16 % 160.00 = 1160.00 MXN.
    """
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    bold, regular = "Helvetica-Bold", "Helvetica"
    rows: list[tuple[str, int, int, int, str]] = [
        (bold, 18, 60, 760, "FACTURA"),
        (regular, 11, 60, 735, "Folio: A-00123"),
        (regular, 11, 60, 718, "Fecha de emision: 20/11/2026"),
        (regular, 11, 60, 701, "Fecha de vencimiento: 20/12/2026"),
        (bold, 12, 60, 670, "Emisor: Comercial Ejemplo S.A. de C.V."),
        (regular, 11, 60, 653, "RFC: CEJ010101AB1"),
        (regular, 11, 60, 636, "Av. Siempre Viva 123, Ciudad de Mexico, Mexico"),
        (bold, 12, 60, 605, "Cliente: Cliente Demo"),
        (regular, 11, 60, 588, "RFC: XAXX010101000"),
        *((bold, 11, x, 550, text) for x, text in _COLUMNS),
        (regular, 11, 60, 532, "2 h"),
        (regular, 11, 130, 532, "Servicio de consultoria"),
        (regular, 11, 340, 532, "$500.00"),
        (regular, 11, 450, 532, "$1,000.00"),
        (regular, 11, 340, 490, "Subtotal:"),
        (regular, 11, 450, 490, "$1,000.00"),
        (regular, 11, 340, 472, "IVA 16%:"),
        (regular, 11, 450, 472, "$160.00"),
        (bold, 12, 340, 450, "Total:"),
        (bold, 12, 450, 450, "$1,160.00 MXN"),
        (regular, 11, 60, 420, "Forma de pago: Transferencia"),
    ]
    for font, size, x, y, text in rows:
        pdf.setFont(font, size)
        pdf.drawString(x, y, text)
    pdf.showPage()
    pdf.save()
    return output.getvalue()
