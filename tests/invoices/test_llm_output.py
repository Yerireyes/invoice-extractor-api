"""Esquema del LLM y su conversión al dominio (común a Gemini y Ollama)."""

from decimal import Decimal
from typing import Any

import pytest

from app.invoices.adapters.llm_output import (
    LlmExtraction,
    LlmFieldConfidence,
    LlmLineItem,
    LlmParty,
    LlmTax,
    to_extraction_output,
)
from app.invoices.ports import NotAnInvoiceError

MODEL = "llm-test"


def llm_extraction(**overrides: Any) -> LlmExtraction:
    data: dict[str, Any] = {
        "is_invoice": True,
        "document_type": "invoice",
        "invoice_number": "A-1",
        "issue_date": "2026-11-20",
        "currency": "mxn",
        "country": "MX",
        "language": "es",
        "issuer": {"name": "Comercial XYZ", "tax_id": "XYZ010101ABC", "tax_id_type": "RFC"},
        "line_items": [
            {"description": "Servicio", "quantity": "2", "unit_price": "500", "amount": "1000.00"}
        ],
        "subtotal": "1000.00",
        "taxes": [{"name": "IVA", "rate": "16", "base": "1000.00", "amount": "160.00"}],
        "total": "1160.00",
        "field_confidence": [{"field": "total", "confidence": 0.97}],
    }
    data.update(overrides)
    return LlmExtraction.model_validate(data)


# --- Mapeo LLM → dominio ---------------------------------------------------------------


def test_maps_llm_output_to_domain_invoice() -> None:
    output = to_extraction_output(llm_extraction(), model=MODEL)

    invoice = output.invoice
    assert output.model == MODEL
    assert invoice.currency == "MXN"
    assert invoice.total == Decimal("1160.00")
    assert invoice.line_items[0].unit_price == Decimal(500)
    assert invoice.issuer.tax_id_type == "RFC"
    assert output.field_confidence == {"total": 0.97}


def test_not_an_invoice() -> None:
    with pytest.raises(NotAnInvoiceError):
        to_extraction_output(llm_extraction(is_invoice=False), model=MODEL)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1234.50", "1234.50"),
        ("1,234.50", "1234.50"),
        ("1.234,50", "1234.50"),
        ("1.234.567", "1234567"),
        ("1,234", "1234"),
        ("12,5", "12.5"),
        ("$ 99.90", "99.90"),
        ("€1.000,00", "1000.00"),
        ("-50.00", "-50.00"),
        ("(50.00)", "-50.00"),
        ("16 %", "16"),
        ("abc", None),
        ("", None),
        (None, None),
    ],
)
def test_decimal_normalization(raw: str | None, expected: str | None) -> None:
    output = to_extraction_output(llm_extraction(total=raw), model=MODEL)

    assert output.invoice.total == (Decimal(expected) if expected else None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2026-11-20", "2026-11-20"), ("2026-11-20T10:00:00", "2026-11-20"), ("20/11/2026", None)],
)
def test_dates_must_be_iso(raw: str, expected: str | None) -> None:
    invoice = to_extraction_output(llm_extraction(issue_date=raw), model=MODEL).invoice

    assert (invoice.issue_date.isoformat() if invoice.issue_date else None) == expected


def test_invalid_codes_become_null_but_currency_is_kept_for_validation() -> None:
    invoice = to_extraction_output(
        llm_extraction(country="Mexico", language="spanish", currency="pesos"), model=MODEL
    ).invoice

    assert invoice.country is None
    assert invoice.language is None
    assert invoice.currency == "PESOS"


def test_texts_are_collapsed_and_truncated() -> None:
    invoice = to_extraction_output(
        llm_extraction(invoice_number="  A \n 1  ", notes="x" * 6000), model=MODEL
    ).invoice

    assert invoice.invoice_number == "A 1"
    assert invoice.notes is not None
    assert len(invoice.notes) == 5000


def test_empty_parties_become_defaults() -> None:
    invoice = to_extraction_output(
        llm_extraction(issuer=LlmParty(), recipient=LlmParty()), model=MODEL
    )

    assert invoice.invoice.issuer.name is None
    assert invoice.invoice.recipient is None


def test_line_amount_is_computed_or_line_dropped() -> None:
    lines = [
        LlmLineItem(description="Sin importe", quantity="3", unit_price="10", discount="5"),
        LlmLineItem(description="Ilegible"),
        LlmLineItem(description="Normal", amount="7"),
    ]

    items = to_extraction_output(llm_extraction(line_items=lines), model=MODEL).invoice.line_items

    assert [(item.description, item.amount) for item in items] == [
        ("Sin importe", Decimal(25)),
        ("Normal", Decimal(7)),
    ]


def test_tax_amount_is_computed_or_tax_dropped() -> None:
    taxes = [LlmTax(name="IVA", rate="16", base="99.99"), LlmTax(name="Ilegible")]

    result = to_extraction_output(llm_extraction(taxes=taxes), model=MODEL).invoice.taxes

    assert [(tax.name, tax.amount) for tax in result] == [("IVA", Decimal("16.00"))]


def test_confidence_is_clamped_and_paths_filtered() -> None:
    confidence = [
        LlmFieldConfidence(field="issuer.tax_id", confidence=1.4),
        LlmFieldConfidence(field="line_items.0.amount", confidence=-0.2),
        LlmFieldConfidence(field="DROP TABLE", confidence=0.5),
        LlmFieldConfidence(field="is_invoice", confidence=1.0),
    ]

    output = to_extraction_output(llm_extraction(field_confidence=confidence), model=MODEL)

    assert output.field_confidence == {"issuer.tax_id": 1.0, "line_items.0.amount": 0.0}


def test_missing_codes_are_null() -> None:
    invoice = to_extraction_output(
        llm_extraction(country=None, language="", currency=""), model=MODEL
    )

    assert invoice.invoice.country is None
    assert invoice.invoice.language is None
    assert invoice.invoice.currency is None
