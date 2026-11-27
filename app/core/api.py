"""Endpoints de servicio: salud del proceso y metadatos públicos."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.meta import MetaResponse, build_meta
from app.invoices.providers import get_ollama_available

router = APIRouter(tags=["service"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


@router.get("/health", summary="Comprobación de salud")
def health() -> HealthResponse:
    return HealthResponse()


@router.get("/meta", summary="Límites, formatos aceptados, proveedores e idiomas de exportación")
async def meta(
    settings: Annotated[Settings, Depends(get_settings)],
    ollama_available: Annotated[bool, Depends(get_ollama_available)],
) -> MetaResponse:
    return build_meta(settings, ollama_available=ollama_available)
