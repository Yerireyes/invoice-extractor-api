"""Extractor falso y determinista, para tests y para desarrollar sin clave de IA.

Devuelve una de varias facturas de ejemplo elegida a partir del contenido del archivo: el mismo
archivo produce siempre el mismo resultado, y archivos distintos dan variedad (una factura
mexicana con confianza baja en el RFC, una española válida y un ticket de EE. UU. que no cuadra).
"""

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from app.core.config import LLMProvider
from app.invoices.ports import ExtractionOutput
from app.invoices.schemas import Invoice
from app.shared.files import PageImage

FAKE_MODEL = "fake-extractor-v1"

_SAMPLES: tuple[tuple[dict[str, Any], Mapping[str, float]], ...] = (
    (
        {
            "document_type": "invoice",
            "invoice_number": "A-00123",
            "issue_date": "2026-11-20",
            "due_date": "2026-12-20",
            "currency": "MXN",
            "country": "MX",
            "language": "es",
            "issuer": {
                "name": "Comercial Ejemplo S.A. de C.V.",
                "tax_id": "CEJ010101AB1",
                "tax_id_type": "RFC",
                "address": "Av. Siempre Viva 123, CDMX",
            },
            "recipient": {"name": "Cliente Demo", "tax_id": "XAXX010101000", "tax_id_type": "RFC"},
            "line_items": [
                {
                    "description": "Servicio de consultoría",
                    "quantity": "2",
                    "unit": "h",
                    "unit_price": "500.00",
                    "discount": "0.00",
                    "tax_rate": "16",
                    "amount": "1000.00",
                }
            ],
            "subtotal": "1000.00",
            "discount_total": "0.00",
            "taxes": [{"name": "IVA", "rate": "16", "base": "1000.00", "amount": "160.00"}],
            "total": "1160.00",
            "payment_method": "Transferencia",
        },
        {"issuer.tax_id": 0.62, "total": 0.98, "issue_date": 0.95},
    ),
    (
        {
            "document_type": "invoice",
            "invoice_number": "2026/0457",
            "issue_date": "2026-10-03",
            "due_date": "2026-11-02",
            "currency": "EUR",
            "country": "ES",
            "language": "es",
            "issuer": {
                "name": "Ejemplo Servicios S.L.",
                "tax_id": "B00000000",
                "tax_id_type": "NIF",
            },
            "line_items": [
                {
                    "description": "Licencia anual",
                    "quantity": "1",
                    "unit_price": "250.00",
                    "tax_rate": "21",
                    "amount": "250.00",
                },
                {
                    "description": "Soporte técnico",
                    "quantity": "3",
                    "unit": "h",
                    "unit_price": "40.00",
                    "tax_rate": "21",
                    "amount": "120.00",
                },
            ],
            "subtotal": "370.00",
            "taxes": [{"name": "IVA", "rate": "21", "base": "370.00", "amount": "77.70"}],
            "total": "447.70",
            "payment_method": "Domiciliación bancaria",
        },
        {"total": 0.99, "issuer.tax_id": 0.91},
    ),
    (
        {
            "document_type": "receipt",
            "invoice_number": None,
            "issue_date": "2026-11-18",
            "currency": "USD",
            "country": "US",
            "language": "en",
            "issuer": {"name": "Sample Coffee Co.", "address": "1 Main St, Springfield"},
            "line_items": [
                {"description": "Latte", "quantity": "2", "unit_price": "4.50", "amount": "9.00"},
                {"description": "Bagel", "quantity": "1", "unit_price": "3.25", "amount": "3.25"},
            ],
            "subtotal": "12.25",
            "taxes": [{"name": "Sales tax", "rate": "8.25", "base": "12.25", "amount": "1.01"}],
            "total": "13.62",
            "payment_method": "Card",
        },
        {"total": 0.55, "taxes.0.amount": 0.8},
    ),
)


class FakeExtractor:
    """Implementa `InvoiceExtractor` sin llamar a ninguna IA."""

    @property
    def provider(self) -> LLMProvider:
        return "fake"

    async def extract(self, pages: Sequence[PageImage]) -> ExtractionOutput:
        digest = hashlib.sha256(b"".join(page.data for page in pages)).digest()
        invoice_data, confidence = _SAMPLES[digest[0] % len(_SAMPLES)]
        return ExtractionOutput(
            invoice=Invoice.model_validate(invoice_data),
            model=FAKE_MODEL,
            field_confidence=dict(confidence),
        )
