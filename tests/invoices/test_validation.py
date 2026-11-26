from datetime import date
from typing import Any

import pytest

from app.invoices.schemas import Invoice, Issue, IssueCode, Validation
from app.invoices.validation import validate_invoice, validation_status
from tests.factories import invoice_payload

TODAY = date(2026, 11, 25)


def _validate(field_confidence: dict[str, float] | None = None, **overrides: Any) -> Validation:
    invoice = Invoice.model_validate(invoice_payload(**overrides))
    return validate_invoice(invoice, today=TODAY, field_confidence=field_confidence)


def _codes(validation: Validation) -> list[IssueCode]:
    return [issue.code for issue in validation.issues]


def _only(validation: Validation, code: IssueCode) -> Issue:
    matches = [issue for issue in validation.issues if issue.code == code]
    assert len(matches) == 1, validation.issues
    return matches[0]


def _line(**overrides: Any) -> dict[str, Any]:
    line: dict[str, Any] = {
        "description": "Concepto",
        "quantity": "2",
        "unit_price": "500.00",
        "discount": "0.00",
        "amount": "1000.00",
    }
    line.update(overrides)
    return line


# --- Factura válida y estado ---------------------------------------------------------


def test_contract_example_is_valid() -> None:
    validation = _validate()

    assert validation.status == "valid"
    assert validation.issues == []


@pytest.mark.parametrize(
    ("severities", "expected"),
    [
        ([], "valid"),
        (["info"], "warnings"),
        (["warning", "info"], "warnings"),
        (["warning", "error"], "invalid"),
        (["error"], "invalid"),
    ],
)
def test_validation_status(severities: list[Any], expected: str) -> None:
    issues = [Issue(code=IssueCode.FUTURE_DATE, severity=severity) for severity in severities]

    assert validation_status(issues) == expected


def test_minimal_invoice_reports_all_missing_required_fields() -> None:
    validation = validate_invoice(Invoice(), today=TODAY)

    assert validation.status == "invalid"
    assert [(issue.code, issue.field) for issue in validation.issues] == [
        (IssueCode.MISSING_REQUIRED_FIELD, "issue_date"),
        (IssueCode.MISSING_REQUIRED_FIELD, "currency"),
        (IssueCode.MISSING_REQUIRED_FIELD, "total"),
        (IssueCode.MISSING_REQUIRED_FIELD, "issuer.name"),
    ]
    assert all(issue.severity == "error" and issue.params == {} for issue in validation.issues)


# --- MISSING_REQUIRED_FIELD / INVALID_CURRENCY -----------------------------------------


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"issue_date": None}, "issue_date"),
        ({"currency": None}, "currency"),
        ({"total": None}, "total"),
        ({"issuer": {"name": None}}, "issuer.name"),
        ({"issuer": {"name": "   "}}, "issuer.name"),
    ],
)
def test_missing_required_field(overrides: dict[str, Any], field: str) -> None:
    validation = _validate(**overrides)

    issue = _only(validation, IssueCode.MISSING_REQUIRED_FIELD)
    assert issue.field == field
    assert issue.severity == "error"
    assert validation.status == "invalid"


def test_invalid_currency() -> None:
    validation = _validate(currency="pesos")

    issue = _only(validation, IssueCode.INVALID_CURRENCY)
    assert issue.field == "currency"
    assert issue.params == {"value": "PESOS"}
    assert validation.status == "invalid"


def test_valid_non_major_currency() -> None:
    assert _validate(currency="XCG").status == "valid"


# --- TOTAL_MISMATCH -------------------------------------------------------------------


def test_total_mismatch() -> None:
    validation = _validate(total="1150.00")

    issue = _only(validation, IssueCode.TOTAL_MISMATCH)
    assert issue.field == "total"
    assert issue.severity == "error"
    assert issue.params == {"expected": "1160.00", "actual": "1150.00"}


@pytest.mark.parametrize(
    ("total", "flagged"),
    [
        ("1160.02", False),  # en el borde de la tolerancia
        ("1159.98", False),
        ("1160.021", True),  # apenas fuera
        ("1159.979", True),
        ("1160.03", True),
    ],
)
def test_total_tolerance_edges(total: str, flagged: bool) -> None:
    validation = _validate(total=total)

    assert (IssueCode.TOTAL_MISMATCH in _codes(validation)) is flagged


def test_total_includes_discount_and_multiple_taxes() -> None:
    validation = _validate(
        line_items=[],
        subtotal="1000.00",
        discount_total="100.00",
        taxes=[
            {"name": "IVA", "rate": "16", "base": "900.00", "amount": "144.00"},
            {"name": "IEPS", "rate": "8", "base": "900.00", "amount": "72.00"},
        ],
        total="1116.00",
    )

    assert validation.status == "valid"


def test_total_with_null_discount_counts_as_zero() -> None:
    assert _validate(discount_total=None).status == "valid"


def test_total_with_zero_taxes() -> None:
    validation = _validate(taxes=[], total="1000.00")

    assert validation.status == "valid"


def test_total_rule_skipped_without_subtotal() -> None:
    validation = _validate(line_items=[], subtotal=None, total="999.99")

    assert IssueCode.TOTAL_MISMATCH not in _codes(validation)


def test_credit_note_with_negative_amounts() -> None:
    validation = _validate(
        document_type="credit_note",
        line_items=[_line(quantity="-2", amount="-1000.00")],
        subtotal="-1000.00",
        taxes=[{"name": "IVA", "rate": "16", "base": "-1000.00", "amount": "-160.00"}],
        total="-1160.00",
    )

    assert validation.status == "valid"


# --- SUBTOTAL_MISMATCH ------------------------------------------------------------------


def test_subtotal_mismatch() -> None:
    validation = _validate(
        line_items=[_line(), _line(quantity="1", amount="500.00")],
        subtotal="1000.00",
        total="1160.00",
    )

    issue = _only(validation, IssueCode.SUBTOTAL_MISMATCH)
    assert issue.field == "subtotal"
    assert issue.params == {"expected": "1500.00", "actual": "1000.00"}


def test_subtotal_rule_skipped_without_lines() -> None:
    validation = _validate(line_items=[])

    assert validation.status == "valid"


def test_subtotal_sum_has_no_float_errors() -> None:
    validation = _validate(
        line_items=[
            _line(quantity="1", unit_price="0.1", amount="0.1"),
            _line(quantity="1", unit_price="0.2", amount="0.2"),
        ],
        subtotal="0.3",
        taxes=[],
        total="0.3",
    )

    assert validation.status == "valid"


# --- LINE_AMOUNT_MISMATCH ----------------------------------------------------------------


def test_line_amount_mismatch_is_warning_with_zero_based_line() -> None:
    validation = _validate(
        line_items=[_line(), _line(quantity="3", amount="1000.00")],
        subtotal="2000.00",
        taxes=[],
        total="2000.00",
    )

    issue = _only(validation, IssueCode.LINE_AMOUNT_MISMATCH)
    assert issue.severity == "warning"
    assert issue.field == "line_items.1.amount"
    assert issue.params == {"line": 1, "expected": "1500.00", "actual": "1000.00"}
    assert validation.status == "warnings"


def test_line_amount_applies_discount() -> None:
    validation = _validate(
        line_items=[_line(discount="100.00", amount="900.00")],
        subtotal="900.00",
        taxes=[],
        total="900.00",
    )

    assert validation.status == "valid"


def test_line_amount_null_discount_counts_as_zero() -> None:
    assert _validate(line_items=[_line(discount=None)]).status == "valid"


@pytest.mark.parametrize("missing", ["quantity", "unit_price"])
def test_line_amount_rule_skipped_when_operand_is_null(missing: str) -> None:
    validation = _validate(line_items=[_line(**{missing: None, "amount": "1000.00"})])

    assert validation.status == "valid"


def test_line_amount_rounding_is_tolerated() -> None:
    validation = _validate(
        line_items=[_line(quantity="3", unit_price="33.333", amount="100.00")],
        subtotal="100.00",
        taxes=[],
        total="100.00",
    )

    assert validation.status == "valid"


# --- TAX_MISMATCH --------------------------------------------------------------------------


def test_tax_mismatch_is_warning_with_index() -> None:
    validation = _validate(
        taxes=[{"name": "IVA", "rate": "21", "base": "1000.00", "amount": "160.00"}],
    )

    issue = _only(validation, IssueCode.TAX_MISMATCH)
    assert issue.severity == "warning"
    assert issue.field == "taxes.0.amount"
    assert issue.params == {"index": 0, "expected": "210.00", "actual": "160.00"}


@pytest.mark.parametrize("missing", ["rate", "base"])
def test_tax_rule_skipped_when_operand_is_null(missing: str) -> None:
    tax = {"name": "IVA", "rate": "16", "base": "1000.00", "amount": "160.00", missing: None}

    assert _validate(taxes=[tax]).status == "valid"


def test_tax_expected_is_rounded_for_display_only() -> None:
    validation = _validate(
        line_items=[],
        subtotal="10.00",
        taxes=[{"name": "VAT", "rate": "7.7", "base": "10.00", "amount": "0.50"}],
        total="10.50",
    )

    issue = _only(validation, IssueCode.TAX_MISMATCH)
    assert issue.params["expected"] == "0.77"


# --- Fechas -------------------------------------------------------------------------------


def test_due_before_issue() -> None:
    validation = _validate(issue_date="2026-11-20", due_date="2026-11-19")

    issue = _only(validation, IssueCode.DUE_BEFORE_ISSUE)
    assert issue.field == "due_date"
    assert issue.severity == "warning"


def test_due_same_day_is_fine() -> None:
    assert _validate(issue_date="2026-11-20", due_date="2026-11-20").status == "valid"


@pytest.mark.parametrize(
    ("issue_date", "flagged"),
    [("2026-11-25", False), ("2026-11-26", False), ("2026-11-27", True)],
)
def test_future_date_with_one_day_grace(issue_date: str, flagged: bool) -> None:
    validation = _validate(issue_date=issue_date, due_date=None)

    assert (IssueCode.FUTURE_DATE in _codes(validation)) is flagged


def test_date_rules_skipped_without_issue_date() -> None:
    validation = _validate(issue_date=None, due_date="2020-01-01")

    assert _codes(validation) == [IssueCode.MISSING_REQUIRED_FIELD]


# --- LOW_CONFIDENCE ---------------------------------------------------------------------------


def test_low_confidence_is_info_sorted_by_field() -> None:
    validation = _validate(
        field_confidence={"total": 0.98, "issuer.tax_id": 0.62, "currency": 0.5, "notes": 0.7}
    )

    assert [(issue.field, issue.params) for issue in validation.issues] == [
        ("currency", {"confidence": 0.5}),
        ("issuer.tax_id", {"confidence": 0.62}),
    ]
    assert all(issue.severity == "info" for issue in validation.issues)
    assert validation.status == "warnings"


# --- Varias reglas a la vez ---------------------------------------------------------------------


def test_issues_follow_rule_order() -> None:
    validation = _validate(
        currency="ZZZ",
        total="1.00",
        taxes=[{"name": "IVA", "rate": "16", "base": "1000.00", "amount": "1.00"}],
        due_date="2026-01-01",
    )

    assert _codes(validation) == [
        IssueCode.INVALID_CURRENCY,
        IssueCode.TOTAL_MISMATCH,
        IssueCode.TAX_MISMATCH,
        IssueCode.DUE_BEFORE_ISSUE,
    ]
    assert validation.status == "invalid"
