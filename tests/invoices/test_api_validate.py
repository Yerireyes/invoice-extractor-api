from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import PROBLEM_JSON
from app.invoices.api import get_today
from app.main import create_app
from tests.factories import invoice_payload

URL = "/api/v1/invoices/validate"


@pytest.fixture
def api(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    app.dependency_overrides[get_today] = lambda: date(2026, 11, 25)
    with TestClient(app) as client:
        yield client


def test_valid_invoice(api: TestClient) -> None:
    response = api.post(URL, json={"invoice": invoice_payload()})

    assert response.status_code == 200
    assert response.json() == {"validation": {"status": "valid", "issues": []}}


def test_invalid_invoice_returns_issues(api: TestClient) -> None:
    response = api.post(URL, json={"invoice": invoice_payload(total="1150.00", currency=None)})

    assert response.status_code == 200
    assert response.json() == {
        "validation": {
            "status": "invalid",
            "issues": [
                {
                    "code": "MISSING_REQUIRED_FIELD",
                    "severity": "error",
                    "field": "currency",
                    "params": {},
                },
                {
                    "code": "TOTAL_MISMATCH",
                    "severity": "error",
                    "field": "total",
                    "params": {"expected": "1160.00", "actual": "1150.00"},
                },
            ],
        }
    }


def test_unknown_fields_are_ignored(api: TestClient) -> None:
    response = api.post(
        URL, json={"invoice": invoice_payload(id="local-1"), "client_version": "1.2"}
    )

    assert response.status_code == 200


def test_numeric_amount_is_validation_error(api: TestClient) -> None:
    response = api.post(URL, json={"invoice": invoice_payload(total=1160)})

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    problem = response.json()
    assert problem["code"] == "VALIDATION_ERROR"
    assert problem["params"]["errors"][0]["loc"] == ["body", "invoice", "total"]


def test_missing_invoice_is_validation_error(api: TestClient) -> None:
    response = api.post(URL, json={})

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_get_today_returns_a_date() -> None:
    assert isinstance(get_today(), date)
