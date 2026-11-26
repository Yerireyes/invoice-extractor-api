"""Datos de prueba sintéticos compartidos."""

from typing import Any


def invoice_payload(**overrides: Any) -> dict[str, Any]:
    """Factura del ejemplo del contrato (válida y cuadrada). `overrides` reemplaza claves."""
    payload: dict[str, Any] = {
        "document_type": "invoice",
        "invoice_number": "A-00123",
        "issue_date": "2026-11-20",
        "due_date": "2026-12-20",
        "currency": "MXN",
        "country": "MX",
        "language": "es",
        "issuer": {
            "name": "Comercial XYZ S.A.",
            "tax_id": "XYZ010101ABC",
            "tax_id_type": "RFC",
            "address": "Av. Siempre Viva 123, CDMX",
            "email": None,
            "phone": None,
        },
        "recipient": None,
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
        "notes": None,
    }
    payload.update(overrides)
    return payload
