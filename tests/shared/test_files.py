import io
from typing import Any

import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pytest
from PIL import Image

from app.shared import files
from app.shared.files import (
    MAX_IMAGE_SIDE,
    FileProblem,
    FileRejectedError,
    detect_mime_type,
    prepare_file,
    read_limited,
)
from tests.factories import image_bytes, pdf_bytes

EXIF_ORIENTATION = 0x0112


class _Stream:
    """Imitación mínima de `UploadFile.read`."""

    def __init__(self, data: bytes) -> None:
        self._buffer = io.BytesIO(data)
        self.reads = 0

    async def read(self, size: int = -1) -> bytes:
        self.reads += 1
        return self._buffer.read(size)


# --- read_limited --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_limited_reads_in_chunks() -> None:
    data = b"x" * (files.READ_CHUNK_SIZE * 3 + 10)
    stream = _Stream(data)

    assert await read_limited(stream, max_bytes=len(data)) == data
    assert stream.reads == 5  # 4 bloques con datos + lectura final vacía


@pytest.mark.anyio
async def test_read_limited_accepts_exact_limit() -> None:
    assert await read_limited(_Stream(b"a" * 100), max_bytes=100) == b"a" * 100


@pytest.mark.anyio
async def test_read_limited_rejects_over_limit_without_reading_everything() -> None:
    mb = 1024 * 1024
    stream = _Stream(b"a" * (10 * mb))

    with pytest.raises(FileRejectedError) as error:
        await read_limited(stream, max_bytes=mb)

    assert error.value.code is FileProblem.FILE_TOO_LARGE
    assert error.value.params == {"max_mb": 1}
    assert stream.reads < (10 * mb) // files.READ_CHUNK_SIZE


# --- detect_mime_type ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (image_bytes("JPEG"), "image/jpeg"),
        (image_bytes("PNG"), "image/png"),
        (image_bytes("WEBP"), "image/webp"),
        (image_bytes("GIF"), "image/gif"),
        (image_bytes("BMP"), "image/bmp"),
        (image_bytes("TIFF"), "image/tiff"),
        (pdf_bytes(), "application/pdf"),
        (b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16, "image/heic"),
        (b"basura inicial\n" + pdf_bytes(), "application/pdf"),
        (b"hola, esto es texto", None),
        (b"", None),
    ],
)
def test_detect_mime_type_by_content(data: bytes, expected: str | None) -> None:
    assert detect_mime_type(data) == expected


# --- prepare_file: imágenes ----------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_accepted_images_become_single_jpeg_page(fmt: str) -> None:
    prepared = prepare_file(image_bytes(fmt), max_pdf_pages=5)

    assert prepared.source_mime_type == f"image/{fmt.lower()}"
    assert len(prepared.pages) == 1
    page = prepared.pages[0]
    assert page.mime_type == "image/jpeg"
    assert (page.width, page.height) == (120, 80)
    with Image.open(io.BytesIO(page.data)) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"


def test_declared_extension_is_irrelevant_gif_is_rejected() -> None:
    with pytest.raises(FileRejectedError) as error:
        prepare_file(image_bytes("GIF"), max_pdf_pages=5)

    assert error.value.code is FileProblem.UNSUPPORTED_FILE_TYPE
    assert error.value.params == {"mime_type": "image/gif"}


def test_unknown_content_reports_null_mime_type() -> None:
    with pytest.raises(FileRejectedError) as error:
        prepare_file(b"%!PS-Adobe-3.0 no soportado", max_pdf_pages=5)

    assert error.value.params == {"mime_type": None}


def test_exif_orientation_is_applied_and_metadata_removed() -> None:
    exif = Image.Exif()
    exif[EXIF_ORIENTATION] = 6  # rotada 90°
    data = image_bytes("JPEG", size=(100, 50), exif=exif.tobytes())

    page = prepare_file(data, max_pdf_pages=5).pages[0]

    assert (page.width, page.height) == (50, 100)
    with Image.open(io.BytesIO(page.data)) as image:
        assert EXIF_ORIENTATION not in image.getexif()


def test_large_images_are_downscaled_keeping_aspect_ratio() -> None:
    page = prepare_file(image_bytes("PNG", size=(4096, 1024)), max_pdf_pages=5).pages[0]

    assert (page.width, page.height) == (MAX_IMAGE_SIDE, MAX_IMAGE_SIDE // 4)


@pytest.mark.parametrize("mode", ["RGBA", "P", "L", "CMYK"])
def test_color_modes_are_converted_to_rgb(mode: str) -> None:
    fmt = "JPEG" if mode == "CMYK" else "PNG"
    save: dict[str, Any] = {"transparency": 0} if mode == "P" else {}

    page = prepare_file(image_bytes(fmt, mode=mode, **save), max_pdf_pages=5).pages[0]

    with Image.open(io.BytesIO(page.data)) as image:
        assert image.mode == "RGB"


def test_transparency_is_flattened_on_white() -> None:
    image = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    output = io.BytesIO()
    image.save(output, format="PNG")

    page = prepare_file(output.getvalue(), max_pdf_pages=5).pages[0]

    with Image.open(io.BytesIO(page.data)) as result:
        assert result.getpixel((5, 5)) == (255, 255, 255)


def test_truncated_image_is_unreadable() -> None:
    data = image_bytes("PNG", size=(300, 300))

    with pytest.raises(FileRejectedError) as error:
        prepare_file(data[: len(data) // 2], max_pdf_pages=5)

    assert error.value.code is FileProblem.UNREADABLE_FILE


def test_corrupt_image_with_valid_signature_is_unreadable() -> None:
    with pytest.raises(FileRejectedError) as error:
        prepare_file(b"\xff\xd8\xff" + b"\x00" * 100, max_pdf_pages=5)

    assert error.value.code is FileProblem.UNREADABLE_FILE


def test_decompression_bomb_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(files, "MAX_SOURCE_PIXELS", 100 * 100)

    with pytest.raises(FileRejectedError) as error:
        prepare_file(image_bytes("PNG", size=(101, 100)), max_pdf_pages=5)

    assert error.value.code is FileProblem.UNREADABLE_FILE


# --- prepare_file: PDF ------------------------------------------------------------


def test_pdf_pages_are_rendered() -> None:
    prepared = prepare_file(pdf_bytes(pages=3), max_pdf_pages=5)

    assert prepared.source_mime_type == "application/pdf"
    assert len(prepared.pages) == 3
    for page in prepared.pages:
        assert page.height > page.width  # A4 vertical
        assert max(page.width, page.height) <= MAX_IMAGE_SIDE


def test_pdf_at_page_limit_is_accepted() -> None:
    assert len(prepare_file(pdf_bytes(pages=5), max_pdf_pages=5).pages) == 5


def test_pdf_over_page_limit_is_rejected() -> None:
    with pytest.raises(FileRejectedError) as error:
        prepare_file(pdf_bytes(pages=6), max_pdf_pages=5)

    assert error.value.code is FileProblem.PDF_TOO_MANY_PAGES
    assert error.value.params == {"max_pages": 5, "pages": 6}


def test_huge_pdf_page_is_rendered_within_bounds() -> None:
    page = prepare_file(pdf_bytes(pagesize=(14400, 14400)), max_pdf_pages=5).pages[0]

    assert (page.width, page.height) == (MAX_IMAGE_SIDE, MAX_IMAGE_SIDE)


def test_corrupt_pdf_is_unreadable() -> None:
    with pytest.raises(FileRejectedError) as error:
        prepare_file(b"%PDF-1.7\nesto no es un pdf", max_pdf_pages=5)

    assert error.value.code is FileProblem.UNREADABLE_FILE


def test_password_protected_pdf_is_unreadable() -> None:
    with pytest.raises(FileRejectedError) as error:
        prepare_file(pdf_bytes(encrypt="secreto"), max_pdf_pages=5)

    assert error.value.code is FileProblem.UNREADABLE_FILE


def test_pdf_without_pages_is_unreadable() -> None:
    empty = pdfium.PdfDocument.new()
    output = io.BytesIO()
    empty.save(output)
    empty.close()

    with pytest.raises(FileRejectedError) as error:
        prepare_file(output.getvalue(), max_pdf_pages=5)

    assert error.value.code is FileProblem.UNREADABLE_FILE
