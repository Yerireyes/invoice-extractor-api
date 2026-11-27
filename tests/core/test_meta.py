from typing import Any

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.core.version import APP_VERSION
from app.invoices.providers import get_ollama_available
from app.main import create_app


def _get_meta(settings: Settings, *, ollama_up: bool | None = None) -> dict[str, Any]:
    """`ollama_up` sustituye el sondeo real a Ollama (`None` = dependencia real)."""
    app = create_app(settings)
    if ollama_up is not None:
        app.dependency_overrides[get_ollama_available] = lambda: ollama_up
    with TestClient(app) as client:
        response = client.get("/api/v1/meta")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def test_meta_matches_contract(client: TestClient) -> None:
    response = client.get("/api/v1/meta")

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == APP_VERSION
    assert body["limits"] == {
        "max_files_per_request": 10,
        "max_file_size_mb": 10,
        "max_pdf_pages": 5,
        "max_invoices_per_export": 100,
    }
    assert body["accepted_mime_types"] == [
        "image/jpeg",
        "image/png",
        "image/webp",
        "application/pdf",
    ]
    assert body["export_languages"] == ["es", "en"]


def test_meta_limits_come_from_settings() -> None:
    settings = Settings(_env_file=None, max_files_per_request=3, max_pdf_pages=2)

    limits = _get_meta(settings)["limits"]

    assert limits["max_files_per_request"] == 3
    assert limits["max_pdf_pages"] == 2


def test_meta_lists_fake_provider_when_active() -> None:
    settings = Settings(_env_file=None, app_env="development", llm_provider="fake")

    providers = _get_meta(settings)["providers"]

    assert providers == [
        {"id": "fake", "available": True, "default": True},
        {"id": "gemini", "available": False, "default": False},
        {"id": "ollama", "available": False, "default": False},
    ]


def test_meta_with_gemini_key_has_gemini_as_default() -> None:
    settings = Settings(
        _env_file=None, llm_provider="gemini", gemini_api_key=SecretStr("clave"), gemini_model="m"
    )

    providers = _get_meta(settings)["providers"]

    assert providers == [
        {"id": "gemini", "available": True, "default": True},
        {"id": "ollama", "available": False, "default": False},
    ]


def test_meta_reflects_ollama_real_availability() -> None:
    settings = Settings(
        _env_file=None, app_env="production", llm_provider="ollama", ollama_enabled=True
    )

    assert _get_meta(settings, ollama_up=True)["providers"] == [
        {"id": "gemini", "available": False, "default": False},
        {"id": "ollama", "available": True, "default": True},
    ]
    # Habilitado pero caído o sin el modelo descargado
    assert _get_meta(settings, ollama_up=False)["providers"][1] == {
        "id": "ollama",
        "available": False,
        "default": True,
    }


def test_meta_does_not_probe_ollama_when_disabled() -> None:
    settings = Settings(_env_file=None, ollama_enabled=False, ollama_base_url="http://no-existe:1")

    providers = _get_meta(settings)["providers"]

    assert {"id": "ollama", "available": False, "default": False} in providers
