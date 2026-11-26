"""Endpoints de servicio: salud del proceso y metadatos públicos."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.meta import MetaResponse, build_meta

router = APIRouter(tags=["service"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


@router.get("/health", summary="Comprobación de salud")
def health() -> HealthResponse:
    return HealthResponse()


@router.get("/meta", summary="Límites, formatos aceptados, proveedores e idiomas de exportación")
def meta(settings: Annotated[Settings, Depends(get_settings)]) -> MetaResponse:
    return build_meta(settings)
