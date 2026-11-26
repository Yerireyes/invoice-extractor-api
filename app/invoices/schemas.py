"""Modelo de factura genérico (válido para cualquier país) y respuestas de extracción/validación.

Reglas del contrato (secciones 3 y 4 de `docs/api-contract.md`):
- Montos, cantidades y tasas son `Decimal`, y en JSON viajan **solo como strings** (`"1234.50"`).
- Los campos semánticamente obligatorios (`issue_date`, `currency`, `total`, `issuer.name`)
  admiten `null`: si faltan, la validación emite `MISSING_REQUIRED_FIELD` en lugar de un 422,
  para no perder una factura que la IA leyó parcialmente.
- La moneda se normaliza a mayúsculas; si no es ISO 4217 la validación emite `INVALID_CURRENCY`.
- Los campos desconocidos se ignoran (el frontend puede añadir un `id` local).

Este módulo no importa FastAPI.
"""

import re
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    RootModel,
    StringConstraints,
    WithJsonSchema,
)

from app.core.config import LLMProvider

# ---------------------------------------------------------------------------
# Tipos base
# ---------------------------------------------------------------------------

DECIMAL_PATTERN = r"^-?\d{1,15}(\.\d{1,6})?$"
_DECIMAL_RE = re.compile(DECIMAL_PATTERN)


def _parse_decimal(value: object) -> Decimal:
    """Acepta strings decimales (`"-1234.50"`) o `Decimal` finitos; rechaza números JSON."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            msg = "el decimal debe ser finito"
            raise ValueError(msg)
        return value
    if not isinstance(value, str):
        msg = 'debe ser un string decimal, p. ej. "1234.50"'
        raise ValueError(msg)
    text = value.strip()
    if not _DECIMAL_RE.fullmatch(text):
        msg = 'formato decimal inválido, se espera p. ej. "1234.50"'
        raise ValueError(msg)
    return Decimal(text)


DecimalStr = Annotated[
    Decimal,
    BeforeValidator(_parse_decimal),
    PlainSerializer(lambda value: format(value, "f"), return_type=str),
    WithJsonSchema({"type": "string", "pattern": DECIMAL_PATTERN, "examples": ["1234.50"]}),
]
"""Decimal que en JSON es siempre un string (dinero, cantidades, tasas en %)."""


def _normalize_code(case: Literal["upper", "lower"]) -> BeforeValidator:
    """Quita espacios y normaliza mayúsculas/minúsculas antes de comprobar el formato."""

    def normalize(value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value.upper() if case == "upper" else value.lower()
        return value

    return BeforeValidator(normalize)


CurrencyCode = Annotated[
    str,
    _normalize_code("upper"),
    StringConstraints(min_length=1, max_length=10),
    Field(description="ISO 4217 (p. ej. `MXN`). Si no es válido, la validación lo marca."),
]
CountryCode = Annotated[
    str,
    _normalize_code("upper"),
    StringConstraints(pattern=r"^[A-Z]{2}$"),
    Field(description="ISO 3166-1 alfa-2 (p. ej. `MX`)."),
]
LanguageCode = Annotated[
    str,
    _normalize_code("lower"),
    StringConstraints(pattern=r"^[a-z]{2}$"),
    Field(description="ISO 639-1 (p. ej. `es`)."),
]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)]


class Schema(BaseModel):
    """Base de los esquemas del dominio: ignora campos desconocidos."""

    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------
# Factura (sección 3)
# ---------------------------------------------------------------------------

type DocumentType = Literal["invoice", "receipt", "credit_note", "other"]


class Party(Schema):
    """Emisor o receptor. En el emisor, `name` es obligatorio (lo comprueba la validación)."""

    name: ShortText | None = None
    tax_id: ShortText | None = None
    tax_id_type: ShortText | None = Field(
        default=None, description="Texto libre: RFC, NIF, CUIT, VAT, EIN..."
    )
    address: LongText | None = None
    email: ShortText | None = None
    phone: ShortText | None = None


class LineItem(Schema):
    description: LongText = ""
    quantity: DecimalStr | None = None
    unit: ShortText | None = None
    unit_price: DecimalStr | None = None
    discount: DecimalStr | None = None
    tax_rate: DecimalStr | None = Field(default=None, description="Porcentaje (`16` = 16 %).")
    amount: DecimalStr = Field(description="cantidad * precio unitario - descuento")


class Tax(Schema):
    name: ShortText | None = None
    rate: DecimalStr | None = Field(default=None, description="Porcentaje (`16` = 16 %).")
    base: DecimalStr | None = None
    amount: DecimalStr


class Invoice(Schema):
    document_type: DocumentType = "invoice"
    invoice_number: ShortText | None = None
    issue_date: date | None = Field(default=None, description="Obligatorio (validación).")
    due_date: date | None = None
    currency: CurrencyCode | None = Field(default=None, description="Obligatorio (validación).")
    country: CountryCode | None = None
    language: LanguageCode | None = None

    issuer: Party = Field(default_factory=Party)
    recipient: Party | None = None

    line_items: list[LineItem] = Field(default_factory=list)

    subtotal: DecimalStr | None = None
    discount_total: DecimalStr | None = None
    taxes: list[Tax] = Field(default_factory=list)
    total: DecimalStr | None = Field(default=None, description="Obligatorio (validación).")

    payment_method: ShortText | None = None
    notes: LongText | None = None


# ---------------------------------------------------------------------------
# Validación (sección 4)
# ---------------------------------------------------------------------------


class IssueCode(StrEnum):
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    INVALID_CURRENCY = "INVALID_CURRENCY"
    TOTAL_MISMATCH = "TOTAL_MISMATCH"
    SUBTOTAL_MISMATCH = "SUBTOTAL_MISMATCH"
    LINE_AMOUNT_MISMATCH = "LINE_AMOUNT_MISMATCH"
    TAX_MISMATCH = "TAX_MISMATCH"
    DUE_BEFORE_ISSUE = "DUE_BEFORE_ISSUE"
    FUTURE_DATE = "FUTURE_DATE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


type Severity = Literal["error", "warning", "info"]
type ValidationStatus = Literal["valid", "warnings", "invalid"]


class Issue(Schema):
    code: IssueCode
    severity: Severity
    field: str | None = Field(
        default=None, description="Ruta del campo, p. ej. `total` o `line_items.0.amount`."
    )
    params: dict[str, Any] = Field(default_factory=dict)


class Validation(Schema):
    status: ValidationStatus
    issues: list[Issue] = Field(default_factory=list)


class ValidateRequest(Schema):
    invoice: Invoice


class ValidateResponse(Schema):
    validation: Validation


# ---------------------------------------------------------------------------
# Extracción (sección 2.2)
# ---------------------------------------------------------------------------


class FileErrorCode(StrEnum):
    NOT_AN_INVOICE = "NOT_AN_INVOICE"
    UNREADABLE_FILE = "UNREADABLE_FILE"
    UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    PDF_TOO_MANY_PAGES = "PDF_TOO_MANY_PAGES"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"


class FileError(Schema):
    code: FileErrorCode
    params: dict[str, Any] = Field(default_factory=dict)


class ExtractionMeta(Schema):
    provider: LLMProvider
    model: str
    pages: int = Field(ge=1)
    processing_ms: int = Field(ge=0)


Confidence = Annotated[float, Field(ge=0, le=1)]


class ExtractionSuccess(Schema):
    file_name: str
    status: Literal["success"] = "success"
    invoice: Invoice
    validation: Validation
    field_confidence: dict[str, Confidence] = Field(
        default_factory=dict,
        description="Confianza 0..1 por campo, en notación de ruta (`issuer.tax_id`).",
    )
    meta: ExtractionMeta


class ExtractionFailure(Schema):
    file_name: str
    status: Literal["error"] = "error"
    error: FileError


class ExtractionResult(
    RootModel[Annotated[ExtractionSuccess | ExtractionFailure, Field(discriminator="status")]]
):
    """Resultado por archivo: unión discriminada por `status` (`success` | `error`)."""


class ExtractSummary(Schema):
    total: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)


class ExtractResponse(Schema):
    results: list[ExtractionResult]
    summary: ExtractSummary
