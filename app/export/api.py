"""Endpoints `/exports/*`: validan límites, llaman al caso de uso y devuelven el archivo."""

from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.core.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode, problem_responses
from app.export.adapters.excel import XLSX_MEDIA_TYPE, ExcelExporter
from app.export.adapters.pdf import PDF_MEDIA_TYPE, PdfExporter
from app.export.ports import Exporter
from app.export.schemas import ExportRequest
from app.export.service import export_file_name, export_invoices

router = APIRouter(tags=["exports"])


def get_now() -> datetime:
    """Instante actual (UTC). Dependencia para poder fijarlo en los tests."""
    return datetime.now(UTC)


def get_excel_exporter() -> Exporter:
    return ExcelExporter()


def get_pdf_exporter(now: Annotated[datetime, Depends(get_now)]) -> Exporter:
    return PdfExporter(generated_on=now.date())


def _file_response(media_type: str) -> dict[int | str, dict[str, Any]]:
    return {
        200: {
            "description": "Archivo generado (`Content-Disposition: attachment`)",
            "content": {media_type: {"schema": {"type": "string", "format": "binary"}}},
        },
        **problem_responses(413, 422),
    }


def _export(
    request: ExportRequest, exporter: Exporter, settings: Settings, now: datetime
) -> Response:
    if len(request.invoices) > settings.max_invoices_per_export:
        raise AppError(
            ErrorCode.TOO_MANY_INVOICES,
            params={"max_invoices": settings.max_invoices_per_export},
        )
    today: date = now.date()
    content = export_invoices(exporter, request.invoices, request.options, today=today)
    file_name = export_file_name(exporter, now)
    return Response(
        content=content,
        media_type=exporter.media_type,
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@router.post(
    "/excel",
    summary="Exporta facturas a Excel (.xlsx)",
    response_class=Response,
    responses=_file_response(XLSX_MEDIA_TYPE),
)
def export_excel(
    request: ExportRequest,
    exporter: Annotated[Exporter, Depends(get_excel_exporter)],
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_now)],
) -> Response:
    return _export(request, exporter, settings, now)


@router.post(
    "/pdf",
    summary="Exporta facturas a PDF (una factura por página)",
    response_class=Response,
    responses=_file_response(PDF_MEDIA_TYPE),
)
def export_pdf(
    request: ExportRequest,
    exporter: Annotated[Exporter, Depends(get_pdf_exporter)],
    settings: Annotated[Settings, Depends(get_settings)],
    now: Annotated[datetime, Depends(get_now)],
) -> Response:
    return _export(request, exporter, settings, now)
