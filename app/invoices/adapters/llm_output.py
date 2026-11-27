"""Esquema de salida que se pide a los LLM y su conversión al dominio (común a Gemini y Ollama).

- **Esquema propio para el LLM** (`LlmExtraction`): solo strings sencillos, sin `Decimal` ni
  restricciones que los proveedores no soportan en su salida estructurada.
- `to_extraction_output` lo normaliza y lo convierte al dominio (`Invoice` con `Decimal`),
  descartando valores ilegibles en lugar de fallar.
"""

import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.invoices.ports import ExtractionFailedError, ExtractionOutput, NotAnInvoiceError
from app.invoices.schemas import DECIMAL_PATTERN, Invoice

# ---------------------------------------------------------------------------
# Esquema para el LLM (solo strings simples)
# ---------------------------------------------------------------------------


class LlmParty(BaseModel):
    name: str | None = None
    tax_id: str | None = None
    tax_id_type: str | None = None
    address: str | None = None
    email: str | None = None
    phone: str | None = None


class LlmLineItem(BaseModel):
    description: str | None = None
    quantity: str | None = None
    unit: str | None = None
    unit_price: str | None = None
    discount: str | None = None
    tax_rate: str | None = None
    amount: str | None = None


class LlmTax(BaseModel):
    name: str | None = None
    rate: str | None = None
    base: str | None = None
    amount: str | None = None


class LlmFieldConfidence(BaseModel):
    field: str
    confidence: float


class LlmExtraction(BaseModel):
    is_invoice: bool
    document_type: Literal["invoice", "receipt", "credit_note", "other"] | None = None
    invoice_number: str | None = None
    issue_date: str | None = None
    due_date: str | None = None
    currency: str | None = None
    country: str | None = None
    language: str | None = None
    issuer: LlmParty | None = None
    recipient: LlmParty | None = None
    line_items: list[LlmLineItem] = Field(default_factory=list)
    subtotal: str | None = None
    discount_total: str | None = None
    taxes: list[LlmTax] = Field(default_factory=list)
    total: str | None = None
    payment_method: str | None = None
    notes: str | None = None
    field_confidence: list[LlmFieldConfidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Mapeo LLM → dominio
# ---------------------------------------------------------------------------

_DECIMAL_RE = re.compile(DECIMAL_PATTERN)
_NUMBER_CHARS_RE = re.compile(r"[^\d,.\-]")
_FIELD_PATH_RE = re.compile(r"^[a-z_]+(\.(\d+|[a-z_]+))*$")
_SHORT, _LONG = 300, 5000


def to_extraction_output(llm: LlmExtraction, *, model: str) -> ExtractionOutput:
    """Normaliza la respuesta del LLM y la convierte al dominio. Lanza `NotAnInvoiceError`."""
    if not llm.is_invoice:
        raise NotAnInvoiceError

    line_items = [item for item in map(_line_item, llm.line_items) if item is not None]
    taxes = [tax for tax in map(_tax, llm.taxes) if tax is not None]
    data: dict[str, Any] = {
        "document_type": llm.document_type or "invoice",
        "invoice_number": _text(llm.invoice_number),
        "issue_date": _date(llm.issue_date),
        "due_date": _date(llm.due_date),
        "currency": _code(llm.currency, length=3, upper=True, max_length=10),
        "country": _code(llm.country, length=2, upper=True),
        "language": _code(llm.language, length=2, upper=False),
        "issuer": _party(llm.issuer) or {},
        "recipient": _party(llm.recipient),
        "line_items": line_items,
        "subtotal": _decimal(llm.subtotal),
        "discount_total": _decimal(llm.discount_total),
        "taxes": taxes,
        "total": _decimal(llm.total),
        "payment_method": _text(llm.payment_method),
        "notes": _text(llm.notes, _LONG),
    }
    try:
        invoice = Invoice.model_validate(data)
    except ValidationError as exc:  # pragma: no cover - el mapeo ya garantiza datos válidos
        raise ExtractionFailedError from exc
    return ExtractionOutput(
        invoice=invoice, model=model, field_confidence=_confidence(llm.field_confidence)
    )


def _text(value: str | None, max_length: int = _SHORT) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    return text[:max_length] or None


def _decimal(value: str | None) -> str | None:
    """Acepta `"1,234.50"`, `"1.234,50"`, `"$ 99"`... y devuelve `"1234.50"` o `None`."""
    if value is None:
        return None
    text = _NUMBER_CHARS_RE.sub("", value)
    negative = text.startswith("-") or value.strip().startswith("(")
    text = text.replace("-", "")
    if "," in text and "." in text:
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        text = text.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif "," in text:
        head, _, tail = text.rpartition(",")
        is_decimal = text.count(",") == 1 and len(tail) != 3  # noqa: PLR2004 - "1,234" son miles
        text = f"{head}.{tail}" if is_decimal else text.replace(",", "")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    candidate = f"-{text}" if negative and text else text
    if not _DECIMAL_RE.fullmatch(candidate):
        return None
    try:
        return format(Decimal(candidate), "f")
    except InvalidOperation:  # pragma: no cover - la regex ya lo garantiza
        return None


def _date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10]).isoformat()
    except ValueError:
        return None


def _code(value: str | None, *, length: int, upper: bool, max_length: int = 0) -> str | None:
    if not value:
        return None
    code = value.strip()
    code = code.upper() if upper else code.lower()
    if max_length:  # moneda: se conserva aunque no sea ISO (la validación la marca)
        return code[:max_length] or None
    return code if len(code) == length and code.isalpha() else None


def _party(llm: LlmParty | None) -> dict[str, Any] | None:
    if llm is None:
        return None
    party = {
        "name": _text(llm.name),
        "tax_id": _text(llm.tax_id),
        "tax_id_type": _text(llm.tax_id_type),
        "address": _text(llm.address, _LONG),
        "email": _text(llm.email),
        "phone": _text(llm.phone),
    }
    return party if any(party.values()) else None


def _line_item(llm: LlmLineItem) -> dict[str, Any] | None:
    quantity, unit_price = _decimal(llm.quantity), _decimal(llm.unit_price)
    discount = _decimal(llm.discount)
    amount = _decimal(llm.amount)
    if amount is None and quantity is not None and unit_price is not None:
        amount = format(Decimal(quantity) * Decimal(unit_price) - Decimal(discount or 0), "f")
    if amount is None or not _DECIMAL_RE.fullmatch(amount):
        return None  # una línea sin importe no aporta nada verificable
    return {
        "description": _text(llm.description, _LONG) or "",
        "quantity": quantity,
        "unit": _text(llm.unit),
        "unit_price": unit_price,
        "discount": discount,
        "tax_rate": _decimal(llm.tax_rate),
        "amount": amount,
    }


def _tax(llm: LlmTax) -> dict[str, Any] | None:
    rate, base, amount = _decimal(llm.rate), _decimal(llm.base), _decimal(llm.amount)
    if amount is None and rate is not None and base is not None:
        amount = format((Decimal(base) * Decimal(rate) / 100).quantize(Decimal("0.01")), "f")
    if amount is None:
        return None
    return {"name": _text(llm.name), "rate": rate, "base": base, "amount": amount}


def _confidence(items: Sequence[LlmFieldConfidence]) -> dict[str, float]:
    return {
        item.field: min(max(item.confidence, 0.0), 1.0)
        for item in items
        if _FIELD_PATH_RE.fullmatch(item.field)
        and item.field.split(".", 1)[0] in Invoice.model_fields
    }
