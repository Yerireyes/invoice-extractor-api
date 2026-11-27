from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import PROBLEM_JSON
from app.export.adapters.pdf import PDF_MEDIA_TYPE
from app.export.api import get_now
from app.main import create_app
from tests.factories import invoice_payload

URL = "/api/v1/exports/pdf"
NOW = datetime(2026, 11, 26, 9, 5, tzinfo=UTC)


def _client(settings: Settings) -> TestClient:
    app = create_app(settings)
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app)


@pytest.fixture
def api(settings: Settings) -> Iterator[TestClient]:
    with _client(settings) as client:
        yield client


def _body(count: int = 1, **options: Any) -> dict[str, Any]:
    return {"invoices": [invoice_payload() for _ in range(count)], "options": options}


def _texts(content: bytes) -> list[str]:
    pdf = pdfium.PdfDocument(content)
    try:
        return [page.get_textpage().get_text_range() for page in pdf]
    finally:
        pdf.close()


def test_returns_pdf_attachment(api: TestClient) -> None:
    response = api.post(URL, json=_body(3, language="en"))

    assert response.status_code == 200
    assert response.headers["content-type"] == PDF_MEDIA_TYPE
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="facturas_20261126_0905.pdf"'
    )
    pages = _texts(response.content)
    assert len(pages) == 3
    assert "Invoice A-00123" in pages[0]
    # La fecha del pie sale del reloj inyectado (`get_now`)
    assert "Generated on 2026-11-26" in pages[0]
    assert "Page 3 of 3" in pages[2]


def test_default_options_are_spanish_with_alerts(api: TestClient) -> None:
    response = api.post(URL, json={"invoices": [invoice_payload(total="1.00")]})

    (page,) = _texts(response.content)
    assert "Factura A-00123" in page
    assert "Inválida" in page
    assert "Alertas" in page


def test_too_many_invoices() -> None:
    with _client(Settings(_env_file=None, max_invoices_per_export=2)) as client:
        response = client.post(URL, json=_body(3))

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "TOO_MANY_INVOICES"
    assert response.json()["params"] == {"max_invoices": 2}


def test_invalid_body_is_validation_error(api: TestClient) -> None:
    response = api.post(URL, json={"invoices": []})

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
