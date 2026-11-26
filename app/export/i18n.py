"""Textos de exportación (encabezados y mensajes de issues) en los idiomas soportados."""

from typing import Literal

type ExportLanguage = Literal["es", "en"]

EXPORT_LANGUAGES: tuple[ExportLanguage, ...] = ("es", "en")
