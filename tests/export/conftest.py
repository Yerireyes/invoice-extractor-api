from datetime import date
from typing import Any

import pytest

from app.export.ports import ExportDocument
from app.export.schemas import ExportInvoice
from app.export.service import build_documents
from tests.factories import invoice_payload

TODAY = date(2026, 11, 26)


def export_invoice(**overrides: Any) -> ExportInvoice:
    return ExportInvoice.model_validate(invoice_payload(**overrides))


@pytest.fixture
def documents() -> list[ExportDocument]:
    """Cuatro facturas: dos en MXN (una inválida), una en EUR y un ticket en JPY sin impuestos."""
    invoices = [
        export_invoice(source_file_name="luz.jpg"),
        export_invoice(invoice_number="A-2", total="1150.00"),  # TOTAL_MISMATCH → inválida
        export_invoice(
            invoice_number="2026/7",
            currency="EUR",
            issuer={"name": "Ejemplo S.L.", "tax_id": "B0000000"},
            line_items=[
                {"description": "Licencia", "quantity": "1", "unit_price": "100", "amount": "100"},
                {"description": "Soporte", "quantity": "2", "unit_price": "25", "amount": "50"},
            ],
            subtotal="150.00",
            discount_total="10.00",
            taxes=[{"name": "IVA", "rate": "21", "base": "140.00", "amount": "29.40"}],
            total="169.40",
            due_date="2026-11-01",  # anterior a la emisión → aviso
        ),
        export_invoice(
            document_type="receipt",
            invoice_number=None,
            currency="JPY",
            issuer={"name": "Sample Mart"},
            recipient=None,
            line_items=[
                {"description": "Tea", "quantity": "2", "unit_price": "160", "amount": "320"}
            ],
            subtotal="320",
            discount_total=None,
            taxes=[],
            total="320",
            due_date=None,
        ),
    ]
    return build_documents(invoices, today=TODAY)
