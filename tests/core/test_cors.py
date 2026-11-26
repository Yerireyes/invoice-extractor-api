from fastapi.testclient import TestClient

ALLOWED = "http://localhost:5173"


def test_preflight_from_allowed_origin(client: TestClient) -> None:
    response = client.options(
        "/api/v1/health",
        headers={"Origin": ALLOWED, "Access-Control-Request-Method": "GET"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED


def test_response_exposes_content_disposition(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"Origin": ALLOWED})

    assert response.headers["access-control-allow-origin"] == ALLOWED
    assert response.headers["access-control-expose-headers"] == "Content-Disposition"


def test_disallowed_origin_gets_no_cors_headers(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"Origin": "https://malicioso.example"})

    assert "access-control-allow-origin" not in response.headers
