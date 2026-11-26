"""Punto de entrada de la aplicación FastAPI."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.api import router as core_router
from app.core.config import Settings, get_settings
from app.core.errors import problem_responses, register_error_handlers
from app.core.logging import configure_logging
from app.core.version import APP_VERSION

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construye la aplicación: logging, CORS, handlers de error y routers.

    Si se pasa `settings`, sustituye a `get_settings` en todas las dependencias.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Invoice Extractor API",
        summary="Extrae datos de facturas de cualquier país con IA y los exporta a Excel y PDF.",
        version=APP_VERSION,
        # Un solo esquema por modelo (sin `Invoice-Input`/`Invoice-Output`): nombres estables
        # para los tipos que genera el frontend.
        separate_input_output_schemas=False,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )
    register_error_handlers(app)
    app.include_router(core_router, prefix=API_PREFIX, responses=problem_responses(500))
    return app


app = create_app()
