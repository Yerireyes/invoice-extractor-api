import pytest
from pydantic import SecretStr

from app.core.config import Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("APP_ENV", "LLM_PROVIDER", "GEMINI_API_KEY", "ALLOWED_ORIGINS"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_match_contract_limits() -> None:
    settings = Settings(_env_file=None)

    assert settings.max_files_per_request == 10
    assert settings.max_file_size_mb == 10
    assert settings.max_pdf_pages == 5
    assert settings.max_invoices_per_export == 100


def test_allowed_origins_parsed_from_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:5173, https://facturas.vercel.app")

    settings = Settings(_env_file=None)

    assert settings.allowed_origins == ["http://localhost:5173", "https://facturas.vercel.app"]


def test_development_without_gemini_key_falls_back_to_fake() -> None:
    settings = Settings(_env_file=None, app_env="development", llm_provider="gemini")

    assert settings.llm_provider == "fake"


def test_development_with_gemini_key_keeps_gemini() -> None:
    settings = Settings(
        _env_file=None, llm_provider="gemini", gemini_api_key=SecretStr("k"), gemini_model="m"
    )

    assert settings.llm_provider == "gemini"


def test_production_never_falls_back_to_fake() -> None:
    settings = Settings(_env_file=None, app_env="production", llm_provider="gemini")

    assert settings.llm_provider == "gemini"


def test_development_with_key_but_no_model_falls_back_to_fake() -> None:
    settings = Settings(_env_file=None, llm_provider="gemini", gemini_api_key=SecretStr("k"))

    assert settings.llm_provider == "fake"
    assert not settings.gemini_configured
