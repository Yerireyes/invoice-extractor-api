import asyncio
import io
from collections.abc import Sequence
from datetime import date

import pytest

from app.core.config import LLMProvider
from app.core.errors import AppError, ErrorCode
from app.invoices.ports import ExtractionFailedError, ExtractionOutput, NotAnInvoiceError
from app.invoices.schemas import ExtractionFailure, ExtractionSuccess, Invoice
from app.invoices.service import ExtractionService, IncomingFile
from app.shared.files import PageImage
from tests.factories import image_bytes, invoice_payload, pdf_bytes

pytestmark = pytest.mark.anyio

TODAY = date(2026, 11, 25)


class _Stream:
    def __init__(self, data: bytes) -> None:
        self._buffer = io.BytesIO(data)

    async def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)


class StubExtractor:
    """Doble de prueba: el comportamiento depende del ancho de la primera página."""

    def __init__(self, delay: float = 0) -> None:
        self.delay = delay
        self.active = 0
        self.max_active = 0

    @property
    def provider(self) -> LLMProvider:
        return "fake"

    async def extract(self, pages: Sequence[PageImage]) -> ExtractionOutput:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            match pages[0].width:
                case 11:
                    raise NotAnInvoiceError
                case 12:
                    raise ExtractionFailedError
                case 13:
                    raise AppError(ErrorCode.LLM_QUOTA_EXCEEDED)
                case 14:
                    msg = "fallo inesperado del adaptador"
                    raise RuntimeError(msg)
            return ExtractionOutput(
                invoice=Invoice.model_validate(invoice_payload(total="1150.00")),
                model="stub",
                field_confidence={"total": 0.5},
            )
        finally:
            self.active -= 1


def _file(data: bytes, name: str | None = "factura.png") -> IncomingFile:
    return IncomingFile(name=name, stream=_Stream(data))


def _png(width: int = 10) -> bytes:
    return image_bytes("PNG", size=(width, 10))


def _service(extractor: StubExtractor, **overrides: int) -> ExtractionService:
    options = {"max_file_size_bytes": 1024 * 1024, "max_pdf_pages": 5} | overrides
    return ExtractionService(extractor, today=TODAY, **options)


async def test_successful_file_is_extracted_and_validated() -> None:
    response = await _service(StubExtractor()).extract([_file(_png())])

    result = response.results[0].root
    assert isinstance(result, ExtractionSuccess)
    assert result.file_name == "factura.png"
    assert result.meta.provider == "fake"
    assert result.meta.model == "stub"
    assert result.meta.pages == 1
    assert result.meta.processing_ms >= 0
    codes = [issue.code for issue in result.validation.issues]
    assert codes == ["TOTAL_MISMATCH", "LOW_CONFIDENCE"]
    assert result.validation.status == "invalid"
    assert response.summary.model_dump() == {"total": 1, "succeeded": 1, "failed": 0}


async def test_pdf_pages_are_counted() -> None:
    response = await _service(StubExtractor()).extract([_file(pdf_bytes(pages=3), "f.pdf")])

    result = response.results[0].root
    assert isinstance(result, ExtractionSuccess)
    assert result.meta.pages == 3


@pytest.mark.parametrize(
    ("data", "code", "params"),
    [
        (_png(11), "NOT_AN_INVOICE", {}),
        (_png(12), "EXTRACTION_FAILED", {}),
        (_png(14), "EXTRACTION_FAILED", {}),
        (image_bytes("GIF"), "UNSUPPORTED_FILE_TYPE", {"mime_type": "image/gif"}),
        (b"\x89PNG\r\n\x1a\n roto", "UNREADABLE_FILE", {}),
        (pdf_bytes(pages=6), "PDF_TOO_MANY_PAGES", {"max_pages": 5, "pages": 6}),
        (b"x" * (2 * 1024 * 1024), "FILE_TOO_LARGE", {"max_mb": 1}),
    ],
)
async def test_file_errors_become_per_file_failures(
    data: bytes, code: str, params: dict[str, object]
) -> None:
    response = await _service(StubExtractor()).extract([_file(data)])

    result = response.results[0].root
    assert isinstance(result, ExtractionFailure)
    assert result.error.code == code
    assert result.error.params == params


async def test_partial_success_keeps_order_and_summary() -> None:
    files = [_file(_png(), "a.png"), _file(_png(11), "b.png"), _file(_png(), "c.png")]

    response = await _service(StubExtractor()).extract(files)

    assert [result.root.file_name for result in response.results] == ["a.png", "b.png", "c.png"]
    assert [result.root.status for result in response.results] == ["success", "error", "success"]
    assert response.summary.model_dump() == {"total": 3, "succeeded": 2, "failed": 1}


async def test_provider_errors_abort_the_whole_request() -> None:
    files = [_file(_png()), _file(_png(13))]

    with pytest.raises(AppError) as error:
        await _service(StubExtractor(delay=0.01)).extract(files)

    assert error.value.code is ErrorCode.LLM_QUOTA_EXCEEDED


async def test_concurrency_is_limited() -> None:
    extractor = StubExtractor(delay=0.02)

    await _service(extractor, concurrency=2).extract([_file(_png()) for _ in range(6)])

    assert extractor.max_active == 2


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("C:\\Users\\ana\\facturas\\luz.jpg", "luz.jpg"),
        ("../../etc/passwd", "passwd"),
        ("   ", "archivo-1"),
        (None, "archivo-1"),
        ("a" * 300 + ".png", "a" * 255),
    ],
)
async def test_file_names_are_sanitized(name: str | None, expected: str) -> None:
    response = await _service(StubExtractor()).extract([_file(_png(), name)])

    assert response.results[0].root.file_name == expected


async def test_logs_contain_metadata_but_not_invoice_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")

    await _service(StubExtractor()).extract([_file(_png(), "privado.png")])

    assert "proveedor=fake resultado=success" in caplog.text
    assert "Comercial XYZ" not in caplog.text
    assert "privado.png" not in caplog.text
