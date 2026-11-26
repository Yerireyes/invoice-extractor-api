"""Reglas de validación de facturas (sección 4 del contrato).

Funciones puras: no importan FastAPI ni dependen de la IA. La fecha de "hoy" se recibe como
parámetro para que el resultado sea determinista y testeable.

Convenciones:
- Tolerancia de redondeo: ±0.02 en la moneda de la factura (una diferencia de 0.02 es válida).
- Las reglas aritméticas se omiten si falta algún campo implicado (`null`). Excepción:
  `discount_total` y `line_items[].discount` en `null` cuentan como 0.
- `params.expected` / `params.actual` son strings decimales; `expected` se redondea a 2 decimales
  para mostrarlo, pero la comparación usa el valor exacto.
- `params.line` y `params.index` son posiciones 0-based (igual que en `field`).
"""

from collections.abc import Iterator, Mapping
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.invoices.currencies import is_iso_4217
from app.invoices.schemas import Invoice, Issue, IssueCode, Severity, Validation, ValidationStatus

TOLERANCE = Decimal("0.02")
LOW_CONFIDENCE_THRESHOLD = 0.7
FUTURE_DATE_GRACE = timedelta(days=1)
"""Margen para facturas emitidas en husos horarios por delante de UTC."""

_CENT = Decimal("0.01")

SEVERITY: Mapping[IssueCode, Severity] = {
    IssueCode.MISSING_REQUIRED_FIELD: "error",
    IssueCode.INVALID_CURRENCY: "error",
    IssueCode.TOTAL_MISMATCH: "error",
    IssueCode.SUBTOTAL_MISMATCH: "error",
    IssueCode.LINE_AMOUNT_MISMATCH: "warning",
    IssueCode.TAX_MISMATCH: "warning",
    IssueCode.DUE_BEFORE_ISSUE: "warning",
    IssueCode.FUTURE_DATE: "warning",
    IssueCode.LOW_CONFIDENCE: "info",
}


def validate_invoice(
    invoice: Invoice,
    *,
    today: date,
    field_confidence: Mapping[str, float] | None = None,
) -> Validation:
    """Aplica todas las reglas y calcula el estado global.

    `field_confidence` solo se pasa desde `/extract` (genera `LOW_CONFIDENCE`).
    """
    issues = [
        *_required_fields(invoice),
        *_currency(invoice),
        *_total(invoice),
        *_subtotal(invoice),
        *_line_amounts(invoice),
        *_taxes(invoice),
        *_dates(invoice, today),
        *_low_confidence(field_confidence or {}),
    ]
    return Validation(status=validation_status(issues), issues=issues)


def validation_status(issues: list[Issue]) -> ValidationStatus:
    """`invalid` si hay algún error; `warnings` si solo hay warning/info; `valid` sin issues."""
    if any(issue.severity == "error" for issue in issues):
        return "invalid"
    return "warnings" if issues else "valid"


# ---------------------------------------------------------------------------
# Reglas
# ---------------------------------------------------------------------------


def _issue(code: IssueCode, field: str | None, **params: object) -> Issue:
    return Issue(code=code, severity=SEVERITY[code], field=field, params=params)


def _mismatch(
    code: IssueCode, field: str, expected: Decimal, actual: Decimal, **params: object
) -> Iterator[Issue]:
    if abs(expected - actual) > TOLERANCE:
        yield _issue(
            code,
            field,
            **params,
            expected=_fmt(expected.quantize(_CENT, rounding=ROUND_HALF_UP)),
            actual=_fmt(actual),
        )


def _fmt(value: Decimal) -> str:
    return format(value, "f")


def _required_fields(invoice: Invoice) -> Iterator[Issue]:
    required: dict[str, object] = {
        "issue_date": invoice.issue_date,
        "currency": invoice.currency,
        "total": invoice.total,
        "issuer.name": invoice.issuer.name,
    }
    for field, value in required.items():
        if value is None or value == "":
            yield _issue(IssueCode.MISSING_REQUIRED_FIELD, field)


def _currency(invoice: Invoice) -> Iterator[Issue]:
    if invoice.currency and not is_iso_4217(invoice.currency):
        yield _issue(IssueCode.INVALID_CURRENCY, "currency", value=invoice.currency)


def _total(invoice: Invoice) -> Iterator[Issue]:
    if invoice.subtotal is None or invoice.total is None:
        return
    discount = invoice.discount_total or Decimal(0)
    taxes = sum((tax.amount for tax in invoice.taxes), Decimal(0))
    expected = invoice.subtotal - discount + taxes
    yield from _mismatch(IssueCode.TOTAL_MISMATCH, "total", expected, invoice.total)


def _subtotal(invoice: Invoice) -> Iterator[Issue]:
    if invoice.subtotal is None or not invoice.line_items:
        return
    expected = sum((line.amount for line in invoice.line_items), Decimal(0))
    yield from _mismatch(IssueCode.SUBTOTAL_MISMATCH, "subtotal", expected, invoice.subtotal)


def _line_amounts(invoice: Invoice) -> Iterator[Issue]:
    for index, line in enumerate(invoice.line_items):
        if line.quantity is None or line.unit_price is None:
            continue
        expected = line.quantity * line.unit_price - (line.discount or Decimal(0))
        yield from _mismatch(
            IssueCode.LINE_AMOUNT_MISMATCH,
            f"line_items.{index}.amount",
            expected,
            line.amount,
            line=index,
        )


def _taxes(invoice: Invoice) -> Iterator[Issue]:
    for index, tax in enumerate(invoice.taxes):
        if tax.base is None or tax.rate is None:
            continue
        expected = tax.base * tax.rate / 100
        yield from _mismatch(
            IssueCode.TAX_MISMATCH, f"taxes.{index}.amount", expected, tax.amount, index=index
        )


def _dates(invoice: Invoice, today: date) -> Iterator[Issue]:
    if invoice.issue_date is None:
        return
    if invoice.due_date is not None and invoice.due_date < invoice.issue_date:
        yield _issue(IssueCode.DUE_BEFORE_ISSUE, "due_date")
    if invoice.issue_date > today + FUTURE_DATE_GRACE:
        yield _issue(IssueCode.FUTURE_DATE, "issue_date")


def _low_confidence(field_confidence: Mapping[str, float]) -> Iterator[Issue]:
    for field in sorted(field_confidence):
        confidence = field_confidence[field]
        if confidence < LOW_CONFIDENCE_THRESHOLD:
            yield _issue(IssueCode.LOW_CONFIDENCE, field, confidence=confidence)
