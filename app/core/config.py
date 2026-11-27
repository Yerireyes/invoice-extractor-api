"""Configuración de la aplicación, leída de variables de entorno (y `.env` en desarrollo)."""

from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

type AppEnv = Literal["development", "production"]
type LLMProvider = Literal["gemini", "ollama", "fake"]
type LogLevel = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]


class Settings(BaseSettings):
    """Ajustes del servicio. Cada campo se corresponde con una variable de entorno en mayúsculas."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: AppEnv = "development"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: LogLevel = "INFO"

    # Proveedor de IA
    llm_provider: LLMProvider = "gemini"
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = ""
    gemini_timeout_s: float = Field(default=60, gt=0)
    ollama_enabled: bool = False
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen2.5vl"

    # HTTP
    allowed_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
    rate_limit_extract: str = "10/minute"

    # Límites v1 (sección 6 del contrato)
    max_files_per_request: int = Field(default=10, ge=1)
    max_file_size_mb: int = Field(default=10, ge=1)
    max_pdf_pages: int = Field(default=5, ge=1)
    max_invoices_per_export: int = Field(default=100, ge=1)

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def gemini_configured(self) -> bool:
        """Hay clave y modelo de Gemini (sin ambos, el proveedor no está disponible)."""
        return bool(self.gemini_api_key.get_secret_value() and self.gemini_model.strip())

    @model_validator(mode="after")
    def _fallback_to_fake_provider(self) -> Self:
        """Sin clave o modelo de Gemini, en desarrollo se usa el extractor falso."""
        if (
            self.app_env == "development"
            and self.llm_provider == "gemini"
            and not self.gemini_configured
        ):
            self.llm_provider = "fake"
        return self


@lru_cache
def get_settings() -> Settings:
    """Devuelve la configuración (una sola instancia por proceso). Úsese como dependencia."""
    return Settings()
