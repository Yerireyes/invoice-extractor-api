"""Versión de la aplicación, leída de los metadatos del paquete instalado (`pyproject.toml`)."""

from importlib.metadata import version

APP_VERSION = version("invoice-extractor-api")
