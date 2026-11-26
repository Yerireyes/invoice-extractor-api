"""Punto de entrada de la aplicación FastAPI."""

from fastapi import FastAPI

from app.core.api import router as core_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construye la aplicación: logging, routers y (en historias posteriores) middlewares."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Invoice Extractor API",
        summary="Extrae datos de facturas de cualquier país con IA y los exporta a Excel y PDF.",
        version="0.1.0",
    )
    app.include_router(core_router, prefix=API_PREFIX)
    return app


app = create_app()
