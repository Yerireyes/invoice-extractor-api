"""Tests contra la API real de Gemini. Excluidos por defecto (`-m "not live"`) y del CI.

Ejecutar en local, con `GEMINI_API_KEY` y `GEMINI_MODEL` en `.env`:

    uv run pytest -m live -v
"""

from datetime import date
from decimal import Decimal

import pytest

from app.core.config import Settings
from app.invoices.adapters.gemini import GeminiExtractor
from app.invoices.ports import NotAnInvoiceError
from app.invoices.validation import validate_invoice
from app.shared.files import prepare_file
from tests.factories import image_bytes, invoice_pdf_bytes

pytestmark = [pytest.mark.live, pytest.mark.anyio]


@pytest.fixture
def extractor() -> GeminiExtractor:
    settings = Settings()
    if not settings.gemini_configured:
        pytest.skip("Requiere GEMINI_API_KEY y GEMINI_MODEL en .env")
    return GeminiExtractor.from_api_key(
        settings.gemini_api_key.get_secret_value(),
        model=settings.gemini_model,
        timeout_s=settings.gemini_timeout_s,
    )


async def test_extracts_synthetic_invoice(extractor: GeminiExtractor) -> None:
    pages = prepare_file(invoice_pdf_bytes(), max_pdf_pages=5).pages

    output = await extractor.extract(pages)

    invoice = output.invoice
    assert invoice.currency == "MXN"
    assert invoice.total == Decimal("1160.00")
    assert invoice.subtotal == Decimal("1000.00")
    assert invoice.issue_date is not None
    assert invoice.issue_date.isoformat() == "2026-11-20"
    assert invoice.issuer.tax_id == "CEJ010101AB1"
    assert len(invoice.line_items) == 1
    assert output.field_confidence
    validation = validate_invoice(
        invoice, today=date(2026, 11, 26), field_confidence=output.field_confidence
    )
    assert validation.status != "invalid", validation.issues


async def test_detects_non_invoice(extractor: GeminiExtractor) -> None:
    pages = prepare_file(image_bytes("PNG", size=(400, 300)), max_pdf_pages=5).pages

    with pytest.raises(NotAnInvoiceError):
        await extractor.extract(pages)
