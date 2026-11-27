import io
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.core.config import Settings
from app.core.errors import PROBLEM_JSON
from app.export.adapters.excel import XLSX_MEDIA_TYPE
from app.export.api import get_now
from app.export.service import build_documents, export_file_name
from app.main import create_app
from tests.export.conftest import TODAY, export_invoice
from tests.factories import invoice_payload

URL = "/api/v1/exports/excel"
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


def test_returns_xlsx_attachment(api: TestClient) -> None:
    response = api.post(URL, json=_body(2, language="en"))

    assert response.status_code == 200
    assert response.headers["content-type"] == XLSX_MEDIA_TYPE
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="facturas_20261126_0905.xlsx"'
    )
    workbook = load_workbook(io.BytesIO(response.content))
    assert workbook.sheetnames[0] == "Summary"
    assert workbook["Summary"].max_row == 3


def test_content_disposition_is_exposed_to_the_browser(api: TestClient) -> None:
    response = api.post(URL, json=_body(), headers={"Origin": "http://localhost:5173"})

    assert response.headers["access-control-expose-headers"] == "Content-Disposition"


def test_default_options(api: TestClient) -> None:
    response = api.post(URL, json={"invoices": [invoice_payload(id="local-1")]})

    workbook = load_workbook(io.BytesIO(response.content))
    assert workbook.sheetnames[0] == "Resumen"
    assert "Alertas" in workbook.sheetnames


def test_source_file_name_fills_the_file_column(api: TestClient) -> None:
    body = {"invoices": [invoice_payload(source_file_name="  factura_luz.jpg ")]}

    workbook = load_workbook(io.BytesIO(api.post(URL, json=body).content))

    assert workbook["Resumen"]["B2"].value == "factura_luz.jpg"


def test_too_many_invoices() -> None:
    with _client(Settings(_env_file=None, max_invoices_per_export=2)) as client:
        response = client.post(URL, json=_body(3))

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "TOO_MANY_INVOICES"
    assert response.json()["params"] == {"max_invoices": 2}


@pytest.mark.parametrize(
    "body",
    [
        {"invoices": []},
        {"invoices": [invoice_payload()], "options": {"language": "fr"}},
        {"invoices": [invoice_payload(total=1160)]},
    ],
    ids=["empty", "unsupported-language", "numeric-amount"],
)
def test_invalid_bodies_are_validation_errors(api: TestClient, body: dict[str, Any]) -> None:
    response = api.post(URL, json=body)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_export_revalidates_instead_of_trusting_the_client(api: TestClient) -> None:
    invoice = invoice_payload(total="1.00", validation={"status": "valid", "issues": []})

    workbook = load_workbook(io.BytesIO(api.post(URL, json={"invoices": [invoice]}).content))

    assert workbook["Resumen"]["Q2"].value == "Inválida"


def test_get_now_is_utc() -> None:
    assert get_now().tzinfo is UTC


def test_refs_grow_with_the_number_of_invoices() -> None:
    documents = build_documents([export_invoice()] * 1000, today=TODAY)

    assert documents[0].ref == "F-0001"
    assert documents[-1].ref == "F-1000"


def test_file_name_uses_the_exporter_extension() -> None:
    class Dummy:
        media_type = "text/plain"
        extension = "txt"

    assert export_file_name(Dummy(), NOW) == "facturas_20261126_0905.txt"  # type: ignore[arg-type]
