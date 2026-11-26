from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from app.invoices.currencies import is_iso_4217
from app.invoices.schemas import (
    ExtractionFailure,
    ExtractionResult,
    ExtractionSuccess,
    ExtractResponse,
    Invoice,
    Issue,
    IssueCode,
    LineItem,
    Tax,
)


def _invoice_payload(**overrides: Any) -> dict[str, Any]:
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


# --- Round trip --------------------------------------------------------------


def test_contract_example_round_trips_unchanged() -> None:
    payload = _invoice_payload()

    invoice = Invoice.model_validate(payload)

    assert invoice.model_dump(mode="json") == payload


def test_amounts_are_decimal_in_python_and_strings_in_json() -> None:
    invoice = Invoice.model_validate(_invoice_payload())

    assert invoice.total == Decimal("1160.00")
    assert isinstance(invoice.line_items[0].quantity, Decimal)
    assert invoice.issue_date == date(2026, 11, 20)
    dumped = invoice.model_dump(mode="json")
    assert dumped["total"] == "1160.00"
    assert dumped["taxes"][0]["rate"] == "16"


def test_decimal_arithmetic_has_no_float_errors() -> None:
    line_a = LineItem.model_validate({"amount": "0.1"})
    line_b = LineItem.model_validate({"amount": "0.2"})

    total = line_a.amount + line_b.amount

    assert total == Decimal("0.3")
    assert format(total, "f") == "0.3"


def test_decimal_serialization_never_uses_exponent() -> None:
    tax = Tax(amount=Decimal("1E+3"), base=Decimal("0.0000001"))

    dumped = tax.model_dump(mode="json")

    assert dumped["amount"] == "1000"
    assert dumped["base"] == "0.0000001"


def test_negative_amounts_are_allowed_for_credit_notes() -> None:
    invoice = Invoice.model_validate(_invoice_payload(document_type="credit_note", total="-50.00"))

    assert invoice.total == Decimal("-50.00")


# --- Formato decimal estricto --------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        1160,  # número JSON entero
        1160.0,  # número JSON con decimales
        True,
        "1e3",  # exponente
        "1e999999999",
        "NaN",
        "Infinity",
        "1,160.00",  # separador de miles
        "1.160,00",
        "$1160",
        "",
        "1.1234567",  # más de 6 decimales
        "1234567890123456",  # más de 15 dígitos enteros
    ],
)
def test_rejects_non_string_or_malformed_decimals(value: object) -> None:
    with pytest.raises(ValidationError):
        Invoice.model_validate(_invoice_payload(total=value))


def test_decimal_strings_are_trimmed() -> None:
    invoice = Invoice.model_validate(_invoice_payload(total=" 1160.00 "))

    assert invoice.total == Decimal("1160.00")


def test_rejects_non_finite_python_decimal() -> None:
    with pytest.raises(ValidationError):
        Tax(amount=Decimal("NaN"))


# --- Campos obligatorios como nullable -----------------------------------------


def test_semantically_required_fields_accept_null() -> None:
    invoice = Invoice.model_validate(
        _invoice_payload(issue_date=None, currency=None, total=None, issuer={"name": None})
    )

    assert invoice.issue_date is None
    assert invoice.currency is None
    assert invoice.total is None
    assert invoice.issuer.name is None


def test_minimal_invoice_uses_defaults() -> None:
    invoice = Invoice.model_validate({})

    assert invoice.document_type == "invoice"
    assert invoice.line_items == []
    assert invoice.taxes == []
    assert invoice.issuer.name is None
    assert invoice.recipient is None


def test_line_item_amount_is_required() -> None:
    with pytest.raises(ValidationError):
        LineItem.model_validate({"description": "Sin importe"})


# --- Normalización y códigos -----------------------------------------------------


def test_codes_are_normalized() -> None:
    invoice = Invoice.model_validate(
        _invoice_payload(currency=" mxn ", country="mx", language="ES")
    )

    assert invoice.currency == "MXN"
    assert invoice.country == "MX"
    assert invoice.language == "es"


def test_non_iso_currency_is_accepted_for_validation_to_flag() -> None:
    invoice = Invoice.model_validate(_invoice_payload(currency="Pesos"))

    assert invoice.currency == "PESOS"
    assert not is_iso_4217(invoice.currency)


@pytest.mark.parametrize(
    ("field", "value"),
    [("country", "MEX"), ("country", 52), ("language", "spa"), ("document_type", "bill")],
)
def test_rejects_invalid_codes(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Invoice.model_validate(_invoice_payload(**{field: value}))


@pytest.mark.parametrize(
    ("code", "expected"),
    [("MXN", True), ("EUR", True), ("XCG", True), ("HRK", True), ("XXX", False), (None, False)],
)
def test_is_iso_4217(code: str | None, expected: bool) -> None:
    assert is_iso_4217(code) is expected


def test_unknown_fields_are_ignored() -> None:
    invoice = Invoice.model_validate(
        _invoice_payload(id="local-123", issuer={"name": "X", "logo": "..."})
    )

    assert "id" not in invoice.model_dump()
    assert "logo" not in invoice.issuer.model_dump()


# --- Validación y extracción --------------------------------------------------------


def test_issue_serializes_code_and_params() -> None:
    issue = Issue(
        code=IssueCode.TOTAL_MISMATCH,
        severity="error",
        field="total",
        params={"expected": "1160.00", "actual": "1150.00"},
    )

    assert issue.model_dump(mode="json") == {
        "code": "TOTAL_MISMATCH",
        "severity": "error",
        "field": "total",
        "params": {"expected": "1160.00", "actual": "1150.00"},
    }


def test_extract_response_uses_discriminated_union() -> None:
    payload = {
        "results": [
            {
                "file_name": "factura_luz.jpg",
                "status": "success",
                "invoice": _invoice_payload(),
                "validation": {"status": "valid", "issues": []},
                "field_confidence": {"issuer.tax_id": 0.62, "total": 0.98},
                "meta": {"provider": "gemini", "model": "m", "pages": 1, "processing_ms": 2840},
            },
            {
                "file_name": "foto_perro.png",
                "status": "error",
                "error": {"code": "NOT_AN_INVOICE", "params": {}},
            },
        ],
        "summary": {"total": 2, "succeeded": 1, "failed": 1},
    }

    response = ExtractResponse.model_validate(payload)

    success, failure = (result.root for result in response.results)
    assert isinstance(success, ExtractionSuccess)
    assert isinstance(failure, ExtractionFailure)
    assert response.model_dump(mode="json") == payload


def test_discriminator_rejects_mixed_shapes() -> None:
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(
            {"file_name": "x.png", "status": "success", "error": {"code": "NOT_AN_INVOICE"}}
        )


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_confidence_must_be_between_0_and_1(confidence: float) -> None:
    with pytest.raises(ValidationError):
        ExtractionSuccess.model_validate(
            {
                "file_name": "f.jpg",
                "invoice": {},
                "validation": {"status": "valid"},
                "field_confidence": {"total": confidence},
                "meta": {"provider": "fake", "model": "fake", "pages": 1, "processing_ms": 0},
            }
        )


# --- Esquema JSON (lo que verá el frontend en openapi.json) -------------------------


def test_decimal_json_schema_is_string_only() -> None:
    schema = Invoice.model_json_schema()

    total = schema["properties"]["total"]
    assert total["anyOf"][0]["type"] == "string"
    assert total["anyOf"][1] == {"type": "null"}


def test_extraction_result_schema_has_discriminator() -> None:
    schema = TypeAdapter(ExtractResponse).json_schema()

    result = schema["$defs"]["ExtractionResult"]
    assert result["discriminator"]["propertyName"] == "status"
    assert {ref["$ref"] for ref in result["oneOf"]} == {
        "#/$defs/ExtractionSuccess",
        "#/$defs/ExtractionFailure",
    }
