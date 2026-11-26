"""Endpoints de servicio (salud del proceso)."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["service"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


@router.get("/health", summary="Comprobación de salud")
def health() -> HealthResponse:
    return HealthResponse()
