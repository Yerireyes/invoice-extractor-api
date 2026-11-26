"""Endpoints `/invoices/*`. Delgados: parsean la petición, llaman al dominio y responden."""

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.core.config import LLMProvider, Settings, get_settings
from app.core.errors import AppError, ErrorCode, problem_responses
from app.invoices.ports import ExtractorFactory
from app.invoices.providers import get_extractor_factory
from app.invoices.schemas import ExtractResponse, ValidateRequest, ValidateResponse
from app.invoices.service import ExtractionService, IncomingFile
from app.invoices.validation import validate_invoice

MB = 1024 * 1024

router = APIRouter(tags=["invoices"])
extract_router = APIRouter(tags=["invoices"])
"""Router aparte para aplicarle su propio límite de tamaño de body (ver `create_app`)."""


def get_today() -> date:
    """Fecha actual (UTC). Dependencia para poder fijarla en los tests."""
    return datetime.now(UTC).date()


@router.post(
    "/validate",
    summary="Revalida una factura editada (no llama a la IA)",
    responses=problem_responses(413, 422),
)
def validate(
    request: ValidateRequest, today: Annotated[date, Depends(get_today)]
) -> ValidateResponse:
    return ValidateResponse(validation=validate_invoice(request.invoice, today=today))


@extract_router.post(
    "/extract",
    summary="Sube 1..N facturas (imágenes o PDF) y devuelve sus datos extraídos y validados",
    responses=problem_responses(413, 422, 503),
)
async def extract(
    settings: Annotated[Settings, Depends(get_settings)],
    extractor_factory: Annotated[ExtractorFactory, Depends(get_extractor_factory)],
    today: Annotated[date, Depends(get_today)],
    files: Annotated[
        list[UploadFile] | None, File(description="Imágenes (JPEG, PNG, WebP) o PDF")
    ] = None,
    provider: Annotated[
        LLMProvider | None, Form(description="Proveedor de IA; por defecto el de `/meta`")
    ] = None,
) -> ExtractResponse:
    uploads = files or []
    if not uploads:
        raise AppError(ErrorCode.NO_FILES)
    if len(uploads) > settings.max_files_per_request:
        raise AppError(
            ErrorCode.TOO_MANY_FILES, params={"max_files": settings.max_files_per_request}
        )

    service = ExtractionService(
        extractor_factory(provider),
        max_file_size_bytes=settings.max_file_size_mb * MB,
        max_pdf_pages=settings.max_pdf_pages,
        today=today,
    )
    return await service.extract([IncomingFile(name=f.filename, stream=f) for f in uploads])


def extract_body_limit(settings: Settings) -> int:
    """Body máximo de `/extract`: todos los archivos al tamaño máximo + 1 MB de margen."""
    return settings.max_files_per_request * settings.max_file_size_mb * MB + MB
