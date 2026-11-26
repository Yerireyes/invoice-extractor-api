"""Archivos de entrada: lectura con límite, detección de tipo por contenido y normalización.

Todo ocurre en memoria: nada se escribe en disco. Las imágenes resultantes se reescriben como JPEG
sin metadatos (se descartan EXIF y GPS) y con un tamaño acotado para enviarlas al LLM.

No importa FastAPI: `read_limited` acepta cualquier objeto con `async read(size)`
(p. ej. `UploadFile`).
"""

import io
import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import pypdfium2 as pdfium  # type: ignore[import-untyped]
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

ACCEPTED_MIME_TYPES: tuple[str, ...] = (
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
)

READ_CHUNK_SIZE = 64 * 1024
MAX_IMAGE_SIDE = 2048
"""Lado mayor de las imágenes enviadas al LLM (px). Suficiente para leer texto pequeño."""
MAX_SOURCE_PIXELS = 50_000_000
"""Rechaza imágenes con más píxeles (protección contra "decompression bombs")."""
PDF_RENDER_DPI = 200
JPEG_QUALITY = 85

# PDFium no es seguro entre hilos: se serializa su uso.
_PDFIUM_LOCK = threading.Lock()


class FileProblem(StrEnum):
    """Errores de archivo (coinciden con los códigos por archivo del contrato)."""

    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
    UNREADABLE_FILE = "UNREADABLE_FILE"
    PDF_TOO_MANY_PAGES = "PDF_TOO_MANY_PAGES"


class FileRejectedError(Exception):
    """El archivo no se puede procesar. `params` sigue el contrato (sección 2.2)."""

    def __init__(self, code: FileProblem, **params: Any) -> None:
        self.code = code
        self.params = params
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PageImage:
    data: bytes
    width: int
    height: int
    mime_type: str = "image/jpeg"


@dataclass(frozen=True, slots=True)
class PreparedFile:
    """Archivo validado y normalizado: una imagen por página."""

    source_mime_type: str
    size_bytes: int
    pages: tuple[PageImage, ...]


class AsyncReadable(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


# ---------------------------------------------------------------------------
# Lectura con límite
# ---------------------------------------------------------------------------


async def read_limited(stream: AsyncReadable, *, max_bytes: int) -> bytes:
    """Lee `stream` por bloques y falla en cuanto supera `max_bytes` (no confía en cabeceras)."""
    buffer = bytearray()
    while chunk := await stream.read(READ_CHUNK_SIZE):
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise FileRejectedError(FileProblem.FILE_TOO_LARGE, max_mb=max_bytes // (1024 * 1024))
    return bytes(buffer)


# ---------------------------------------------------------------------------
# Detección de tipo por contenido (magic bytes)
# ---------------------------------------------------------------------------

_SIGNATURES: Sequence[tuple[bytes, str]] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)
_HEIC_BRANDS = (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1")
_PDF_HEADER_WINDOW = 1024
"""Los lectores de PDF aceptan basura antes de `%PDF-` dentro del primer kilobyte."""


def detect_mime_type(data: bytes) -> str | None:
    """Tipo MIME según el contenido, o `None` si no se reconoce."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in _HEIC_BRANDS:
        return "image/heic"
    for signature, mime_type in _SIGNATURES:
        if data.startswith(signature):
            return mime_type
    if b"%PDF-" in data[:_PDF_HEADER_WINDOW]:
        return "application/pdf"
    return None


# ---------------------------------------------------------------------------
# Normalización
# ---------------------------------------------------------------------------


def prepare_file(data: bytes, *, max_pdf_pages: int) -> PreparedFile:
    """Valida el contenido y lo convierte en imágenes JPEG por página.

    Es CPU-intensivo y síncrono: desde código async, llamarlo con `asyncio.to_thread`.
    """
    mime_type = detect_mime_type(data)
    if mime_type not in ACCEPTED_MIME_TYPES:
        raise FileRejectedError(FileProblem.UNSUPPORTED_FILE_TYPE, mime_type=mime_type)

    if mime_type == "application/pdf":
        pages = _render_pdf(data, max_pages=max_pdf_pages)
    else:
        pages = (_normalize_image(data),)

    logger.debug("Archivo preparado: %s, %d bytes, %d páginas", mime_type, len(data), len(pages))
    return PreparedFile(source_mime_type=mime_type, size_bytes=len(data), pages=pages)


def _normalize_image(data: bytes) -> PageImage:
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_SOURCE_PIXELS:
                raise FileRejectedError(FileProblem.UNREADABLE_FILE)
            image.load()
            oriented = ImageOps.exif_transpose(image)
            return _to_page(oriented)
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise FileRejectedError(FileProblem.UNREADABLE_FILE) from exc


def _render_pdf(data: bytes, *, max_pages: int) -> tuple[PageImage, ...]:
    with _PDFIUM_LOCK:
        try:
            document = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:  # corrupto o protegido con contraseña
            raise FileRejectedError(FileProblem.UNREADABLE_FILE) from exc
        try:
            page_count = len(document)
            if page_count == 0:  # pragma: no cover - PDFium ya rechaza PDFs sin páginas al abrirlos
                raise FileRejectedError(FileProblem.UNREADABLE_FILE)
            if page_count > max_pages:
                raise FileRejectedError(
                    FileProblem.PDF_TOO_MANY_PAGES, max_pages=max_pages, pages=page_count
                )
            return tuple(_render_page(document[index]) for index in range(page_count))
        finally:
            document.close()


def _render_page(page: Any) -> PageImage:
    try:
        width_pt, height_pt = page.get_size()
        scale = min(PDF_RENDER_DPI / 72, MAX_IMAGE_SIDE / max(width_pt, height_pt, 1))
        image = page.render(scale=scale).to_pil()
        return _to_page(image)
    finally:
        page.close()


def _to_page(image: Image.Image) -> PageImage:
    """Convierte a RGB (fondo blanco si hay transparencia), acota el tamaño y codifica JPEG."""
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        image = background
    elif image.mode != "RGB":
        image = image.convert("RGB")

    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)

    output = io.BytesIO()
    image.save(output, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return PageImage(data=output.getvalue(), width=image.width, height=image.height)
