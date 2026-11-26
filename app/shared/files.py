"""Manejo de archivos de entrada (validación, PDF → imágenes, normalización)."""

ACCEPTED_MIME_TYPES: tuple[str, ...] = (
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
)
