"""Tests contra un Ollama real. Excluidos por defecto (`-m "not live"`) y del CI.

Ejecutar en local con Ollama arrancado y el modelo descargado (`ollama pull qwen3-vl:8b`),
`OLLAMA_ENABLED=true` y `OLLAMA_BASE_URL` apuntando a él (p. ej. `http://localhost:11434`):

    uv run pytest -m live -v tests/invoices/test_ollama_live.py
"""

from decimal import Decimal

import pytest

from app.core.config import Settings
from app.invoices.adapters.ollama import OllamaExtractor, OllamaStatus, new_client
from app.shared.files import prepare_file
from tests.factories import invoice_pdf_bytes

pytestmark = [pytest.mark.live, pytest.mark.anyio]


@pytest.fixture
async def extractor() -> OllamaExtractor:
    settings = Settings()
    if not settings.ollama_enabled:
        pytest.skip("Requiere OLLAMA_ENABLED=true en .env")
    client = new_client(settings.ollama_base_url, timeout_s=settings.ollama_timeout_s)
    if not await OllamaStatus(client, model=settings.ollama_model).available():
        pytest.skip(f"Ollama no responde o falta el modelo {settings.ollama_model}")
    return OllamaExtractor(client, model=settings.ollama_model)


async def test_extracts_synthetic_invoice(extractor: OllamaExtractor) -> None:
    pages = prepare_file(invoice_pdf_bytes(), max_pdf_pages=5).pages

    output = await extractor.extract(pages)

    invoice = output.invoice
    assert invoice.currency == "MXN"
    assert invoice.total == Decimal("1160.00")
    assert invoice.issue_date is not None
    assert invoice.issue_date.isoformat() == "2026-11-20"
