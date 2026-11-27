"""Textos de exportación (hojas, encabezados, estados y mensajes de issues) en `es` y `en`.

Lo usan los exportadores (Excel y PDF). No importa FastAPI.
"""

import re
from collections.abc import Mapping
from typing import Any, Literal

from app.invoices.schemas import Issue, IssueCode

type ExportLanguage = Literal["es", "en"]

EXPORT_LANGUAGES: tuple[ExportLanguage, ...] = ("es", "en")

TEXTS: Mapping[ExportLanguage, Mapping[str, str]] = {
    "es": {
        # Hojas
        "sheet.summary": "Resumen",
        "sheet.line_items": "Conceptos",
        "sheet.taxes": "Impuestos",
        "sheet.totals": "Totales por moneda",
        "sheet.alerts": "Alertas",
        # Columnas
        "col.ref": "Ref",
        "col.file": "Archivo",
        "col.type": "Tipo",
        "col.number": "Número",
        "col.invoice_number": "Número factura",
        "col.issue_date": "Fecha emisión",
        "col.due_date": "Vencimiento",
        "col.issuer": "Emisor",
        "col.issuer_tax_id": "ID fiscal emisor",
        "col.recipient": "Receptor",
        "col.recipient_tax_id": "ID fiscal receptor",
        "col.currency": "Moneda",
        "col.subtotal": "Subtotal",
        "col.discounts": "Descuentos",
        "col.taxes": "Impuestos",
        "col.total": "Total",
        "col.payment_method": "Método de pago",
        "col.validation": "Estado validación",
        "col.alert_count": "Nº alertas",
        "col.line": "# Línea",
        "col.description": "Descripción",
        "col.quantity": "Cantidad",
        "col.unit": "Unidad",
        "col.unit_price": "Precio unitario",
        "col.discount": "Descuento",
        "col.tax_rate": "% Impuesto",
        "col.amount": "Importe",
        "col.tax": "Impuesto",
        "col.rate": "Tasa %",
        "col.base": "Base",
        "col.invoice_count": "Nº facturas",
        "col.severity": "Severidad",
        "col.code": "Código",
        "col.field": "Campo",
        "col.message": "Mensaje",
        # Valores
        "status.valid": "Válida",
        "status.warnings": "Con avisos",
        "status.invalid": "Inválida",
        "severity.error": "Error",
        "severity.warning": "Aviso",
        "severity.info": "Info",
        "type.invoice": "Factura",
        "type.receipt": "Ticket",
        "type.credit_note": "Nota de crédito",
        "type.other": "Otro",
        # Campos (columna "Campo" de alertas)
        "field.issue_date": "Fecha de emisión",
        "field.due_date": "Vencimiento",
        "field.currency": "Moneda",
        "field.total": "Total",
        "field.subtotal": "Subtotal",
        "field.issuer.name": "Nombre del emisor",
        "field.line": "Línea {n}",
        "field.tax": "Impuesto {n}",
        "field.amount": "importe",
        # Mensajes
        "msg.MISSING_REQUIRED_FIELD": "Falta un dato obligatorio: {field}.",
        "msg.INVALID_CURRENCY": "La moneda «{value}» no es un código ISO 4217 válido.",
        "msg.TOTAL_MISMATCH": (
            "El total no cuadra: subtotal − descuentos + impuestos = {expected}, "
            "pero la factura indica {actual}."
        ),
        "msg.SUBTOTAL_MISMATCH": (
            "El subtotal no cuadra: la suma de los conceptos es {expected}, "
            "pero la factura indica {actual}."
        ),
        "msg.LINE_AMOUNT_MISMATCH": (
            "El importe de la línea {line} no cuadra: cantidad × precio − descuento = "
            "{expected}, pero indica {actual}."
        ),
        "msg.TAX_MISMATCH": (
            "El impuesto {index} no cuadra: base × tasa = {expected}, pero indica {actual}."
        ),
        "msg.DUE_BEFORE_ISSUE": "La fecha de vencimiento es anterior a la de emisión.",
        "msg.FUTURE_DATE": "La fecha de emisión es posterior a hoy.",
        "msg.LOW_CONFIDENCE": "Dato leído con poca seguridad ({confidence}): revísalo.",
        # PDF
        "pdf.title": "Facturas",
        "pdf.issuer": "Emisor",
        "pdf.recipient": "Receptor",
        "pdf.alerts": "Alertas",
        "pdf.no_alerts": "Sin alertas",
        "pdf.page": "Página {page} de {pages}",
        "pdf.generated": "Generado el {date}",
    },
    "en": {
        "sheet.summary": "Summary",
        "sheet.line_items": "Line items",
        "sheet.taxes": "Taxes",
        "sheet.totals": "Totals by currency",
        "sheet.alerts": "Alerts",
        "col.ref": "Ref",
        "col.file": "File",
        "col.type": "Type",
        "col.number": "Number",
        "col.invoice_number": "Invoice number",
        "col.issue_date": "Issue date",
        "col.due_date": "Due date",
        "col.issuer": "Issuer",
        "col.issuer_tax_id": "Issuer tax ID",
        "col.recipient": "Recipient",
        "col.recipient_tax_id": "Recipient tax ID",
        "col.currency": "Currency",
        "col.subtotal": "Subtotal",
        "col.discounts": "Discounts",
        "col.taxes": "Taxes",
        "col.total": "Total",
        "col.payment_method": "Payment method",
        "col.validation": "Validation status",
        "col.alert_count": "Alerts",
        "col.line": "Line #",
        "col.description": "Description",
        "col.quantity": "Quantity",
        "col.unit": "Unit",
        "col.unit_price": "Unit price",
        "col.discount": "Discount",
        "col.tax_rate": "Tax %",
        "col.amount": "Amount",
        "col.tax": "Tax",
        "col.rate": "Rate %",
        "col.base": "Base",
        "col.invoice_count": "Invoices",
        "col.severity": "Severity",
        "col.code": "Code",
        "col.field": "Field",
        "col.message": "Message",
        "status.valid": "Valid",
        "status.warnings": "Warnings",
        "status.invalid": "Invalid",
        "severity.error": "Error",
        "severity.warning": "Warning",
        "severity.info": "Info",
        "type.invoice": "Invoice",
        "type.receipt": "Receipt",
        "type.credit_note": "Credit note",
        "type.other": "Other",
        "field.issue_date": "Issue date",
        "field.due_date": "Due date",
        "field.currency": "Currency",
        "field.total": "Total",
        "field.subtotal": "Subtotal",
        "field.issuer.name": "Issuer name",
        "field.line": "Line {n}",
        "field.tax": "Tax {n}",
        "field.amount": "amount",
        "msg.MISSING_REQUIRED_FIELD": "Missing required data: {field}.",
        "msg.INVALID_CURRENCY": "Currency “{value}” is not a valid ISO 4217 code.",
        "msg.TOTAL_MISMATCH": (
            "Total does not add up: subtotal − discounts + taxes = {expected}, "
            "but the invoice says {actual}."
        ),
        "msg.SUBTOTAL_MISMATCH": (
            "Subtotal does not add up: line items sum {expected}, but the invoice says {actual}."
        ),
        "msg.LINE_AMOUNT_MISMATCH": (
            "Line {line} amount does not add up: quantity × price − discount = {expected}, "
            "but it says {actual}."
        ),
        "msg.TAX_MISMATCH": (
            "Tax {index} does not add up: base × rate = {expected}, but it says {actual}."
        ),
        "msg.DUE_BEFORE_ISSUE": "The due date is earlier than the issue date.",
        "msg.FUTURE_DATE": "The issue date is in the future.",
        "msg.LOW_CONFIDENCE": "Value read with low confidence ({confidence}): please check it.",
        "pdf.title": "Invoices",
        "pdf.issuer": "Issuer",
        "pdf.recipient": "Recipient",
        "pdf.alerts": "Alerts",
        "pdf.no_alerts": "No alerts",
        "pdf.page": "Page {page} of {pages}",
        "pdf.generated": "Generated on {date}",
    },
}

_LIST_FIELD_RE = re.compile(r"^(line_items|taxes)\.(\d+)(?:\.(\w+))?$")


def text(language: ExportLanguage, key: str, **params: Any) -> str:
    template = TEXTS[language][key]
    return template.format(**params) if params else template


def field_label(language: ExportLanguage, path: str | None) -> str:
    """Nombre legible de un campo: `line_items.1.amount` → "Línea 2 · importe"."""
    if not path:
        return ""
    if match := _LIST_FIELD_RE.fullmatch(path):
        kind, index, attribute = match.groups()
        label = text(
            language, "field.line" if kind == "line_items" else "field.tax", n=int(index) + 1
        )
        return f"{label} · {text(language, 'field.amount')}" if attribute == "amount" else label
    return TEXTS[language].get(f"field.{path}", path)


def issue_message(language: ExportLanguage, issue: Issue) -> str:
    """Mensaje traducido de un issue. Las posiciones 0-based se muestran 1-based."""
    params = dict(issue.params)
    for key in ("line", "index"):
        if isinstance(params.get(key), int):
            params[key] += 1
    if issue.code is IssueCode.MISSING_REQUIRED_FIELD:
        params["field"] = field_label(language, issue.field)
    if issue.code is IssueCode.LOW_CONFIDENCE and isinstance(params.get("confidence"), float):
        params["confidence"] = f"{params['confidence']:.0%}"
    return text(language, f"msg.{issue.code.value}", **params)
