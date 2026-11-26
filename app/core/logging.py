"""Configuración de logging.

Regla de privacidad: nunca se registra el contenido de las facturas, solo metadatos
(tamaño, páginas, proveedor, tiempo).
"""

import logging

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: str) -> None:
    """Configura el logger raíz con un formato uniforme."""
    logging.basicConfig(level=level, format=LOG_FORMAT, force=True)
