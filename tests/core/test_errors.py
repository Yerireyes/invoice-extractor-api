from collections.abc import Iterator

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import PROBLEM_JSON, AppError, ErrorCode
from app.main import create_app


class _Body(BaseModel):
    amount: int


def _app_with_test_routes(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/boom/app-error")
    def app_error() -> None:
        raise AppError(ErrorCode.TOO_MANY_FILES, params={"max_files": 10}, detail="Max 10 files")

    @app.get("/boom/app-error-headers")
    def app_error_headers() -> None:
        raise AppError(
            ErrorCode.RATE_LIMITED, params={"retry_after_s": 30}, headers={"Retry-After": "30"}
        )

    @app.get("/boom/unhandled")
    def unhandled() -> None:
        msg = "secreto interno: /etc/passwd"
        raise RuntimeError(msg)

    @app.post("/boom/body")
    def body(payload: _Body) -> _Body:
        return payload

    return app


@pytest.fixture
def error_client(settings: Settings) -> Iterator[TestClient]:
    app = _app_with_test_routes(settings)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_app_error_returns_problem_details(error_client: TestClient) -> None:
    response = error_client.get("/boom/app-error")

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json() == {
        "type": "about:blank",
        "title": "Unprocessable Content",
        "status": 422,
        "code": "TOO_MANY_FILES",
        "detail": "Max 10 files",
        "params": {"max_files": 10},
    }


def test_app_error_uses_contract_status_and_keeps_headers(error_client: TestClient) -> None:
    response = error_client.get("/boom/app-error-headers")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"
    assert response.json()["code"] == "RATE_LIMITED"


def test_app_error_accepts_explicit_status() -> None:
    error = AppError(ErrorCode.LLM_UNAVAILABLE, 504)

    assert error.status == 504


def test_request_validation_error_hides_client_input(error_client: TestClient) -> None:
    response = error_client.post("/boom/body", json={"amount": "dato-sensible"})

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    problem = response.json()
    assert problem["code"] == "VALIDATION_ERROR"
    assert problem["params"]["errors"] == [
        {
            "loc": ["body", "amount"],
            "msg": "Input should be a valid integer, unable to parse string as an integer",
            "type": "int_parsing",
        }
    ]
    assert "dato-sensible" not in response.text


def test_malformed_json_is_validation_error(error_client: TestClient) -> None:
    response = error_client.post(
        "/boom/body", content=b"{no es json", headers={"content-type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_unknown_route_is_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/no-existe")

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "NOT_FOUND"


def test_wrong_method_is_method_not_allowed(client: TestClient) -> None:
    response = client.delete("/api/v1/health")

    assert response.status_code == 405
    assert response.json()["code"] == "METHOD_NOT_ALLOWED"
    assert "GET" in response.headers["allow"]


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "VALIDATION_ERROR"), (502, "INTERNAL_ERROR")],
)
def test_other_http_exceptions_are_problem_details(
    settings: Settings, status: int, code: str
) -> None:
    app = create_app(settings)

    @app.get("/boom/http")
    def http_error() -> None:
        raise HTTPException(status_code=status, detail="detalle interno")

    with TestClient(app) as test_client:
        response = test_client.get("/boom/http")

    assert response.status_code == status
    assert response.json()["code"] == code
    assert "detalle interno" not in response.text


def test_unhandled_exception_is_internal_error_without_details(
    error_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    response = error_client.get("/boom/unhandled")

    assert response.status_code == 500
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json() == {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": 500,
        "code": "INTERNAL_ERROR",
        "detail": None,
        "params": {},
    }
    assert "secreto interno" not in response.text
    assert "Error no controlado en GET /boom/unhandled" in caplog.text
