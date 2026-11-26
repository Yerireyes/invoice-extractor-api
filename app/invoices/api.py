"""Endpoints `/invoices/*`. Delgados: parsean la petición, llaman al dominio y responden."""

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.errors import problem_responses
from app.invoices.schemas import ValidateRequest, ValidateResponse
from app.invoices.validation import validate_invoice

router = APIRouter(tags=["invoices"])


def get_today() -> date:
    """Fecha actual (UTC). Dependencia para poder fijarla en los tests."""
    return datetime.now(UTC).date()


@router.post(
    "/validate",
    summary="Revalida una factura editada (no llama a la IA)",
    responses=problem_responses(422),
)
def validate(
    request: ValidateRequest, today: Annotated[date, Depends(get_today)]
) -> ValidateResponse:
    return ValidateResponse(validation=validate_invoice(request.invoice, today=today))
