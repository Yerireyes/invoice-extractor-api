"""Punto de entrada de la aplicación FastAPI."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.formparsers import MultiPartParser

from app.core.api import router as core_router
from app.core.config import Settings, get_settings
from app.core.errors import (
    RequestTooLargeProblemMiddleware,
    problem_responses,
    register_error_handlers,
)
from app.core.logging import configure_logging
from app.core.version import APP_VERSION
from app.invoices.api import MB, extract_body_limit, extract_router
from app.invoices.api import router as invoices_router

API_PREFIX = "/api/v1"
JSON_BODY_LIMIT = 10 * MB
"""Body máximo por defecto (JSON de `/validate` y `/exports/*`)."""


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
    # Orden: el último añadido es el más externo. CORS envuelve al 413 reescrito.
    app.add_middleware(RequestTooLargeProblemMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )
    register_error_handlers(app)
    app.include_router(core_router, prefix=API_PREFIX, responses=problem_responses(500))
    # Límites de body por router (leídos en streaming, sin fiarse de Content-Length → 413).
    # No se usa el límite global de FastAPI: se aplica fuera de CORS y el navegador no podría
    # leer el 413.
    app.include_router(
        invoices_router,
        prefix=f"{API_PREFIX}/invoices",
        responses=problem_responses(500),
        max_body_size=JSON_BODY_LIMIT,
    )

    upload_limit = extract_body_limit(settings)
    # Privacidad: Starlette vuelca a disco las partes multipart mayores que `spool_max_size`.
    # Con el umbral por encima del body máximo, los archivos subidos nunca tocan el disco.
    MultiPartParser.spool_max_size = max(MultiPartParser.spool_max_size, upload_limit)
    app.include_router(
        extract_router,
        prefix=f"{API_PREFIX}/invoices",
        responses=problem_responses(500),
        max_body_size=upload_limit,
    )
    return app


app = create_app()
