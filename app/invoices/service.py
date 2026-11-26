"""Caso de uso de extracción: archivos → páginas → extractor → validación.

Cada archivo se procesa de forma independiente (éxito parcial): un archivo ilegible no impide
extraer los demás. Solo los errores del proveedor de IA (`AppError`: cuota agotada, caída)
abortan toda la petición.

Privacidad: solo se registran metadatos (índice, tamaño, páginas, proveedor, tiempo, resultado).
"""

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import PurePath

from app.core.errors import AppError
from app.invoices.ports import ExtractionFailedError, InvoiceExtractor, NotAnInvoiceError
from app.invoices.schemas import (
    ExtractionFailure,
    ExtractionMeta,
    ExtractionResult,
    ExtractionSuccess,
    ExtractResponse,
    ExtractSummary,
    FileError,
    FileErrorCode,
)
from app.invoices.validation import validate_invoice
from app.shared.files import AsyncReadable, FileRejectedError, prepare_file, read_limited

logger = logging.getLogger(__name__)

MAX_CONCURRENT_FILES = 3
"""Archivos procesados a la vez (protege la cuota del proveedor y la CPU)."""
MAX_FILE_NAME_LENGTH = 255


@dataclass(frozen=True, slots=True)
class IncomingFile:
    name: str | None
    stream: AsyncReadable


class ExtractionService:
    def __init__(
        self,
        extractor: InvoiceExtractor,
        *,
        max_file_size_bytes: int,
        max_pdf_pages: int,
        today: date,
        concurrency: int = MAX_CONCURRENT_FILES,
    ) -> None:
        self._extractor = extractor
        self._max_file_size_bytes = max_file_size_bytes
        self._max_pdf_pages = max_pdf_pages
        self._today = today
        self._semaphore = asyncio.Semaphore(concurrency)

    async def extract(self, files: Sequence[IncomingFile]) -> ExtractResponse:
        try:
            async with asyncio.TaskGroup() as group:
                tasks = [
                    group.create_task(self._process(index, file))
                    for index, file in enumerate(files)
                ]
        except* AppError as errors:
            raise errors.exceptions[0] from None

        results = [task.result() for task in tasks]
        succeeded = sum(isinstance(result.root, ExtractionSuccess) for result in results)
        return ExtractResponse(
            results=results,
            summary=ExtractSummary(
                total=len(results), succeeded=succeeded, failed=len(results) - succeeded
            ),
        )

    async def _process(self, index: int, file: IncomingFile) -> ExtractionResult:
        file_name = _safe_file_name(file.name, index)
        async with self._semaphore:
            started = time.perf_counter()
            result: ExtractionSuccess | ExtractionFailure
            try:
                result = await self._extract_one(file_name, file.stream, started)
            except FileRejectedError as exc:
                result = _failure(file_name, FileErrorCode(exc.code.value), exc.params)
            except NotAnInvoiceError:
                result = _failure(file_name, FileErrorCode.NOT_AN_INVOICE)
            except ExtractionFailedError:
                result = _failure(file_name, FileErrorCode.EXTRACTION_FAILED)
            except AppError:
                raise
            except Exception:
                logger.exception("Fallo inesperado extrayendo el archivo #%d", index)
                result = _failure(file_name, FileErrorCode.EXTRACTION_FAILED)

        outcome = "success" if isinstance(result, ExtractionSuccess) else result.error.code
        logger.info(
            "Archivo #%d procesado: proveedor=%s resultado=%s tiempo_ms=%d",
            index,
            self._extractor.provider,
            outcome,
            _elapsed_ms(started),
        )
        return ExtractionResult(result)

    async def _extract_one(
        self, file_name: str, stream: AsyncReadable, started: float
    ) -> ExtractionSuccess:
        data = await read_limited(stream, max_bytes=self._max_file_size_bytes)
        prepared = await asyncio.to_thread(prepare_file, data, max_pdf_pages=self._max_pdf_pages)
        output = await self._extractor.extract(prepared.pages)
        validation = validate_invoice(
            output.invoice, today=self._today, field_confidence=output.field_confidence
        )
        logger.debug(
            "Documento leído: %d bytes, %d páginas", prepared.size_bytes, len(prepared.pages)
        )
        return ExtractionSuccess(
            file_name=file_name,
            invoice=output.invoice,
            validation=validation,
            field_confidence=dict(output.field_confidence),
            meta=ExtractionMeta(
                provider=self._extractor.provider,
                model=output.model,
                pages=len(prepared.pages),
                processing_ms=_elapsed_ms(started),
            ),
        )


def _failure(
    file_name: str, code: FileErrorCode, params: dict[str, object] | None = None
) -> ExtractionFailure:
    return ExtractionFailure(file_name=file_name, error=FileError(code=code, params=params or {}))


def _safe_file_name(name: str | None, index: int) -> str:
    """Solo el nombre base (sin rutas del cliente), acotado; uno genérico si no hay nombre."""
    base = PurePath((name or "").replace("\\", "/")).name.strip()
    return base[:MAX_FILE_NAME_LENGTH] or f"archivo-{index + 1}"


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
