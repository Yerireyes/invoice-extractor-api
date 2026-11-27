"""Genera el conjunto de evaluación: documentos sintéticos + su JSON esperado.

Todos los datos son ficticios. Cada caso se define aquí una sola vez; los importes se calculan con
`Decimal` para que el JSON esperado siempre cuadre, y el documento se dibuja a partir de él con el
idioma, el formato numérico y de fecha del país y el tipo de soporte (PDF, foto, ticket).

    uv run python -m tests.eval.generate        # regenera tests/eval/cases/
"""

import io
import json
import random
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Literal

import pypdfium2 as pdfium  # type: ignore[import-untyped]
from PIL import Image, ImageDraw, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

CASES_DIR = Path(__file__).parent / "cases"

type Kind = Literal["pdf", "photo", "ticket", "not_invoice"]
type NumberStyle = Literal["en", "eu", "fr"]

LABELS: dict[str, dict[str, str]] = {
    "es": {
        "invoice": "FACTURA",
        "credit_note": "NOTA DE CRÉDITO",
        "receipt": "TICKET DE COMPRA",
        "number": "Número",
        "issue": "Fecha de emisión",
        "due": "Vencimiento",
        "to": "Cliente",
        "qty": "Cant.",
        "desc": "Concepto",
        "price": "Precio",
        "disc": "Desc.",
        "amount": "Importe",
        "subtotal": "Subtotal",
        "discount": "Descuento",
        "total": "TOTAL",
        "payment": "Forma de pago",
        "page": "Página",
    },
    "en": {
        "invoice": "INVOICE",
        "credit_note": "CREDIT NOTE",
        "receipt": "RECEIPT",
        "number": "Invoice No.",
        "issue": "Date",
        "due": "Due date",
        "to": "Bill to",
        "qty": "Qty",
        "desc": "Description",
        "price": "Unit price",
        "disc": "Disc.",
        "amount": "Amount",
        "subtotal": "Subtotal",
        "discount": "Discount",
        "total": "TOTAL",
        "payment": "Payment",
        "page": "Page",
    },
    "de": {
        "invoice": "RECHNUNG",
        "credit_note": "GUTSCHRIFT",
        "receipt": "KASSENBON",
        "number": "Rechnungsnummer",
        "issue": "Rechnungsdatum",
        "due": "Fällig am",
        "to": "Kunde",
        "qty": "Menge",
        "desc": "Beschreibung",
        "price": "Einzelpreis",
        "disc": "Rabatt",
        "amount": "Betrag",
        "subtotal": "Zwischensumme",
        "discount": "Rabatt",
        "total": "GESAMTBETRAG",
        "payment": "Zahlungsart",
        "page": "Seite",
    },
    "fr": {
        "invoice": "FACTURE",
        "credit_note": "AVOIR",
        "receipt": "TICKET",
        "number": "N° de facture",
        "issue": "Date",
        "due": "Échéance",
        "to": "Client",
        "qty": "Qté",
        "desc": "Désignation",
        "price": "Prix unitaire",
        "disc": "Remise",
        "amount": "Montant",
        "subtotal": "Total HT",
        "discount": "Remise",
        "total": "TOTAL TTC",
        "payment": "Règlement",
        "page": "Page",
    },
    "pt": {
        "invoice": "NOTA FISCAL",
        "credit_note": "NOTA DE CRÉDITO",
        "receipt": "CUPOM",
        "number": "Número",
        "issue": "Data de emissão",
        "due": "Vencimento",
        "to": "Destinatário",
        "qty": "Qtd.",
        "desc": "Descrição",
        "price": "Valor unit.",
        "disc": "Desc.",
        "amount": "Valor",
        "subtotal": "Subtotal",
        "discount": "Desconto",
        "total": "TOTAL",
        "payment": "Pagamento",
        "page": "Página",
    },
}

ZERO_DECIMAL_CURRENCIES = {"JPY", "COP", "CLP", "KRW"}


@dataclass(frozen=True)
class Line:
    description: str
    quantity: str
    unit_price: str
    discount: str = "0"
    unit: str | None = None


@dataclass(frozen=True)
class TaxSpec:
    name: str
    rate: str


@dataclass(frozen=True)
class Party:
    name: str
    tax_id: str | None = None
    tax_id_type: str | None = None
    address: str | None = None


@dataclass(frozen=True)
class Case:
    id: str
    kind: Kind
    lang: str
    number_style: NumberStyle
    date_format: str
    currency: str
    symbol: str
    symbol_after: bool
    country: str
    document_type: str
    issuer: Party
    lines: Sequence[Line]
    taxes: Sequence[TaxSpec]
    issue_date: date
    invoice_number: str | None = None
    due_date: date | None = None
    recipient: Party | None = None
    discount_total: str | None = None
    payment_method: str | None = None
    lines_per_page: int = 40
    notes: str | None = None


# ---------------------------------------------------------------------------
# Cálculo del JSON esperado
# ---------------------------------------------------------------------------


def _q(value: Decimal, currency: str) -> Decimal:
    exp = Decimal(1) if currency in ZERO_DECIMAL_CURRENCIES else Decimal("0.01")
    return value.quantize(exp, rounding=ROUND_HALF_UP)


def _s(value: Decimal) -> str:
    return format(value, "f")


def expected_invoice(case: Case) -> dict[str, Any]:
    line_items: list[dict[str, str | None]] = []
    for line in case.lines:
        amount = _q(
            Decimal(line.quantity) * Decimal(line.unit_price) - Decimal(line.discount),
            case.currency,
        )
        line_items.append(
            {
                "description": line.description,
                "quantity": line.quantity,
                "unit": line.unit,
                "unit_price": line.unit_price,
                "discount": line.discount if line.discount != "0" else None,
                "amount": _s(amount),
            }
        )
    subtotal = sum((Decimal(str(item["amount"])) for item in line_items), Decimal(0))
    discount = Decimal(case.discount_total or 0)
    base = subtotal - discount
    taxes = [
        {
            "name": tax.name,
            "rate": tax.rate,
            "base": _s(base),
            "amount": _s(_q(base * Decimal(tax.rate) / 100, case.currency)),
        }
        for tax in case.taxes
    ]
    total = base + sum((Decimal(tax["amount"]) for tax in taxes), Decimal(0))

    def party(p: Party | None) -> dict[str, Any] | None:
        if p is None:
            return None
        return {
            "name": p.name,
            "tax_id": p.tax_id,
            "tax_id_type": p.tax_id_type,
            "address": p.address,
        }

    return {
        "document_type": case.document_type,
        "invoice_number": case.invoice_number,
        "issue_date": case.issue_date.isoformat(),
        "due_date": case.due_date.isoformat() if case.due_date else None,
        "currency": case.currency,
        "country": case.country,
        "language": case.lang,
        "issuer": party(case.issuer),
        "recipient": party(case.recipient),
        "line_items": line_items,
        "subtotal": _s(subtotal),
        "discount_total": case.discount_total,
        "taxes": taxes,
        "total": _s(total),
        "payment_method": case.payment_method,
    }


# ---------------------------------------------------------------------------
# Formatos locales
# ---------------------------------------------------------------------------


def fmt_number(value: str, style: NumberStyle, decimals: int) -> str:
    number = Decimal(value)
    text = f"{abs(number):,.{decimals}f}"  # 1,234.56
    if style == "eu":
        text = text.replace(",", "X").replace(".", ",").replace("X", ".")
    elif style == "fr":
        text = text.replace(",", " ").replace(".", ",")
    return f"-{text}" if number < 0 else text


def fmt_money(value: str, case: Case) -> str:
    decimals = 0 if case.currency in ZERO_DECIMAL_CURRENCIES else 2
    number = fmt_number(value, case.number_style, decimals)
    if case.symbol_after:
        return f"{number} {case.symbol}"
    sign, digits = ("-", number[1:]) if number.startswith("-") else ("", number)
    return f"{sign}{case.symbol}{digits}"


def fmt_rate(value: str, case: Case) -> str:
    rate = Decimal(value)
    decimals = 0 if rate == rate.to_integral_value() else len(str(rate).split(".")[1])
    return fmt_number(value, case.number_style, decimals) + " %"


# ---------------------------------------------------------------------------
# Dibujo
# ---------------------------------------------------------------------------


class _Writer:
    """Escribe líneas de texto de arriba abajo sobre un canvas de ReportLab."""

    def __init__(self, pdf: canvas.Canvas, top: float, left: float, width: float) -> None:
        self.pdf, self.top, self.left, self.width = pdf, top, left, width
        self.y = top

    def text(self, value: str, *, size: float = 10, bold: bool = False, x: float = 0) -> None:
        self.pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        self.pdf.drawString(self.left + x, self.y, value)

    def right(self, value: str, *, x: float, size: float = 10, bold: bool = False) -> None:
        self.pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        self.pdf.drawRightString(self.left + x, self.y, value)

    def down(self, amount: float = 14) -> None:
        self.y -= amount


def _render_pdf(case: Case, invoice: dict[str, Any], *, ticket: bool) -> bytes:
    labels = LABELS[case.lang]
    output = io.BytesIO()
    width, height = (226.0, 620.0) if ticket else A4  # ticket térmico de 80 mm
    # invariant: sin fecha de creación ni ID aleatorio, así cada generación da el mismo PDF
    pdf = canvas.Canvas(output, pagesize=(width, height), invariant=True)
    margin = 12.0 if ticket else 50.0
    usable = width - 2 * margin
    size = 7.5 if ticket else 10.0
    lines = invoice["line_items"]
    pages = [lines[i : i + case.lines_per_page] for i in range(0, len(lines), case.lines_per_page)]

    for page_index, page_lines in enumerate(pages):
        w = _Writer(pdf, height - margin - 10, margin, usable)
        first, last = page_index == 0, page_index == len(pages) - 1
        if first:
            _header(w, case, labels, size=size, ticket=ticket)
        else:
            w.text(
                f"{labels[case.document_type]} {case.invoice_number or ''}", size=size, bold=True
            )
            w.down(20)
        _table(w, case, page_lines, labels, size=size, ticket=ticket)
        if last:
            _totals(w, case, invoice, labels, size=size)
        if len(pages) > 1:
            w.y = margin
            w.text(f"{labels['page']} {page_index + 1} / {len(pages)}", size=size - 1)
        pdf.showPage()
    pdf.save()
    return output.getvalue()


def _header(
    w: _Writer,
    case: Case,
    labels: dict[str, str],
    *,
    size: float,
    ticket: bool,
) -> None:
    issuer = case.issuer
    w.text(issuer.name, size=size + 3, bold=True)
    w.down(size + 4)
    if issuer.address:
        w.text(issuer.address, size=size)
        w.down(size + 3)
    if issuer.tax_id:
        w.text(f"{issuer.tax_id_type or ''}: {issuer.tax_id}".strip(": "), size=size)
        w.down(size + 3)
    w.down(size)
    w.text(labels[case.document_type], size=size + 5, bold=True)
    w.down(size + 8)
    if case.invoice_number:
        w.text(f"{labels['number']}: {case.invoice_number}", size=size)
        w.down(size + 3)
    w.text(f"{labels['issue']}: {case.issue_date.strftime(case.date_format)}", size=size)
    w.down(size + 3)
    if case.due_date:
        w.text(f"{labels['due']}: {case.due_date.strftime(case.date_format)}", size=size)
        w.down(size + 3)
    if case.recipient and not ticket:
        w.down(size)
        w.text(f"{labels['to']}: {case.recipient.name}", size=size, bold=True)
        w.down(size + 3)
        if case.recipient.tax_id:
            w.text(f"{case.recipient.tax_id_type}: {case.recipient.tax_id}", size=size)
            w.down(size + 3)
        if case.recipient.address:
            w.text(case.recipient.address, size=size)
            w.down(size + 3)
    w.down(size + 6)


def _table(
    w: _Writer,
    case: Case,
    lines: Sequence[dict[str, Any]],
    labels: dict[str, str],
    *,
    size: float,
    ticket: bool,
) -> None:
    has_discount = any(line["discount"] for line in lines) and not ticket
    if ticket:
        for line in lines:
            w.text(line["description"], size=size)
            w.down(size + 2)
            qty = fmt_number(line["quantity"], case.number_style, 0)
            price = fmt_money(line["unit_price"], case)
            w.text(f"  {qty} x {price}", size=size)
            w.right(fmt_money(line["amount"], case), x=w.width, size=size)
            w.down(size + 4)
        w.down(4)
        return

    cols = {"qty": 0, "desc": 45, "price": 330, "disc": 400, "amount": w.width}
    w.text(labels["qty"], size=size, bold=True, x=cols["qty"])
    w.text(labels["desc"], size=size, bold=True, x=cols["desc"])
    w.right(labels["price"], x=cols["price"] + 50, size=size, bold=True)
    if has_discount:
        w.right(labels["disc"], x=cols["disc"] + 30, size=size, bold=True)
    w.right(labels["amount"], x=cols["amount"], size=size, bold=True)
    w.down(size + 6)
    for line in lines:
        qty = fmt_number(line["quantity"], case.number_style, 0)
        unit = f" {line['unit']}" if line["unit"] else ""
        w.text(f"{qty}{unit}", size=size, x=cols["qty"])
        w.text(line["description"], size=size, x=cols["desc"])
        w.right(fmt_money(line["unit_price"], case), x=cols["price"] + 50, size=size)
        if has_discount and line["discount"]:
            w.right(fmt_money(line["discount"], case), x=cols["disc"] + 30, size=size)
        w.right(fmt_money(line["amount"], case), x=cols["amount"], size=size)
        w.down(size + 5)
    w.down(size)


def _totals(
    w: _Writer, case: Case, invoice: dict[str, Any], labels: dict[str, str], *, size: float
) -> None:
    label_x = w.width * 0.55
    rows: list[tuple[str, str, bool]] = [
        (labels["subtotal"], fmt_money(invoice["subtotal"], case), False)
    ]
    if invoice["discount_total"]:
        rows.append((labels["discount"], fmt_money(f"-{invoice['discount_total']}", case), False))
    rows += [
        (f"{tax['name']} {fmt_rate(tax['rate'], case)}", fmt_money(tax["amount"], case), False)
        for tax in invoice["taxes"]
    ]
    rows.append((labels["total"], fmt_money(invoice["total"], case), True))
    for label, value, bold in rows:
        w.text(label, size=size + (2 if bold else 0), bold=bold, x=label_x)
        w.right(value, x=w.width, size=size + (2 if bold else 0), bold=bold)
        w.down(size + 6)
    if case.payment_method:
        w.down(size)
        w.text(f"{labels['payment']}: {case.payment_method}", size=size)
        w.down(size + 4)
    if case.notes:
        w.text(case.notes, size=size - 1)


def _rasterize(pdf_bytes: bytes, scale: float) -> list[Image.Image]:
    document = pdfium.PdfDocument(pdf_bytes)
    try:
        return [
            document[i].render(scale=scale).to_pil().convert("RGB") for i in range(len(document))
        ]
    finally:
        document.close()


def _as_photo(image: Image.Image, seed: int) -> Image.Image:
    """Simula una foto de móvil: fondo de mesa, ligera rotación, desenfoque y ruido."""
    rng = random.Random(seed)  # noqa: S311 - reproducible, no criptográfico
    paper = image.filter(ImageFilter.GaussianBlur(0.8))
    pad = 80
    background = Image.new("RGB", (paper.width + 2 * pad, paper.height + 2 * pad), (92, 70, 52))
    background.paste(paper, (pad, pad))
    rotated = background.rotate(rng.uniform(-4, 4), resample=Image.Resampling.BICUBIC, expand=False)
    draw = ImageDraw.Draw(rotated)
    for _ in range(2500):
        x, y = rng.randrange(rotated.width), rng.randrange(rotated.height)
        shade = rng.randrange(60, 200)
        draw.point((x, y), fill=(shade, shade, shade))
    # Sombra suave en una esquina (iluminación desigual)
    shadow = Image.new("L", rotated.size, 0)
    ImageDraw.Draw(shadow).ellipse(
        (-rotated.width // 2, -rotated.height // 2, rotated.width // 2, rotated.height // 2),
        fill=60,
    )
    darker = Image.new("RGB", rotated.size, (0, 0, 0))
    return Image.composite(darker, rotated, shadow.filter(ImageFilter.GaussianBlur(120)))


def _not_invoice_image() -> Image.Image:
    """Un gráfico de barras: contiene números, pero no es un comprobante de pago."""
    image = Image.new("RGB", (900, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 30), "Visitas mensuales al sitio web 2026", fill="black")
    values = [120, 340, 280, 460, 390, 510]
    for index, value in enumerate(values):
        x = 80 + index * 130
        draw.rectangle((x, 540 - value, x + 80, 540), fill=(60, 120, 200))
        draw.text((x + 20, 550), ["Ene", "Feb", "Mar", "Abr", "May", "Jun"][index], fill="black")
        draw.text((x + 20, 520 - value), str(value), fill="black")
    draw.line((60, 540, 860, 540), fill="black", width=2)
    return image


def render(case: Case, invoice: dict[str, Any]) -> tuple[str, bytes]:
    """Devuelve (nombre de archivo, bytes) del documento del caso."""
    if case.kind == "not_invoice":
        output = io.BytesIO()
        _not_invoice_image().save(output, format="PNG")
        return "document.png", output.getvalue()
    pdf_bytes = _render_pdf(case, invoice, ticket=case.kind == "ticket")
    if case.kind == "pdf":
        return "document.pdf", pdf_bytes
    if case.kind == "ticket":
        output = io.BytesIO()
        _rasterize(pdf_bytes, scale=3)[0].save(output, format="PNG", optimize=True)
        return "document.png", output.getvalue()
    output = io.BytesIO()
    photo = _as_photo(_rasterize(pdf_bytes, scale=2)[0], seed=len(case.id))
    photo.save(output, format="JPEG", quality=72)
    return "document.jpg", output.getvalue()


# ---------------------------------------------------------------------------
# Casos
# ---------------------------------------------------------------------------


def _cases() -> list[Case]:
    return [
        Case(
            id="01-mx-factura",
            kind="pdf",
            lang="es",
            number_style="en",
            date_format="%d/%m/%Y",
            currency="MXN",
            symbol="$",
            symbol_after=False,
            country="MX",
            document_type="invoice",
            invoice_number="A-00123",
            issue_date=date(2026, 11, 20),
            due_date=date(2026, 12, 20),
            issuer=Party(
                "Comercial Ejemplo S.A. de C.V.", "CEJ010101AB1", "RFC", "Av. Reforma 100, CDMX"
            ),
            recipient=Party("Cliente Demo S.C.", "CDE020202CD2", "RFC"),
            lines=[
                Line("Servicio de consultoría", "2", "500.00", unit="h"),
                Line("Licencia de software", "1", "1200.00", discount="120.00"),
            ],
            taxes=[TaxSpec("IVA", "16")],
            payment_method="Transferencia",
        ),
        Case(
            id="02-es-factura-irpf",
            kind="pdf",
            lang="es",
            number_style="eu",
            date_format="%d/%m/%Y",
            currency="EUR",
            symbol="€",
            symbol_after=True,
            country="ES",
            document_type="invoice",
            invoice_number="2026/0457",
            issue_date=date(2026, 3, 4),
            due_date=date(2026, 4, 3),
            issuer=Party("Laura Ejemplo Martín", "00000000T", "NIF", "C/ Mayor 1, 28013 Madrid"),
            recipient=Party("Servicios Demo S.L.", "B00000000", "CIF", "Barcelona"),
            lines=[Line("Diseño de identidad corporativa", "1", "1850.00")],
            taxes=[TaxSpec("IVA", "21"), TaxSpec("IRPF", "-15")],
            payment_method="Transferencia bancaria",
        ),
        Case(
            id="03-us-receipt-ticket",
            kind="ticket",
            lang="en",
            number_style="en",
            date_format="%m/%d/%Y",
            currency="USD",
            symbol="$",
            symbol_after=False,
            country="US",
            document_type="receipt",
            issue_date=date(2026, 3, 4),
            issuer=Party("Sample Coffee Co.", address="1 Main St, Springfield, IL"),
            lines=[
                Line("Latte grande", "2", "4.75"),
                Line("Blueberry muffin", "1", "3.25"),
                Line("Bottled water", "3", "1.50"),
            ],
            taxes=[TaxSpec("Sales tax", "8.875")],
            payment_method="Visa",
        ),
        Case(
            id="04-de-rechnung",
            kind="pdf",
            lang="de",
            number_style="eu",
            date_format="%d.%m.%Y",
            currency="EUR",
            symbol="€",
            symbol_after=True,
            country="DE",
            document_type="invoice",
            invoice_number="RE-2026-0815",
            issue_date=date(2026, 10, 15),
            due_date=date(2026, 10, 29),
            issuer=Party(
                "Beispiel Technik GmbH", "DE000000000", "USt-IdNr.", "Hauptstr. 5, Berlin"
            ),
            recipient=Party("Muster AG", address="Ringstr. 9, München"),
            lines=[
                Line("Wartungsvertrag Oktober", "1", "450.00"),
                Line("Ersatzteil Filter", "4", "37.50", unit="Stk"),
            ],
            taxes=[TaxSpec("MwSt.", "19")],
            payment_method="Überweisung",
        ),
        Case(
            id="05-fr-facture",
            kind="pdf",
            lang="fr",
            number_style="fr",
            date_format="%d/%m/%Y",
            currency="EUR",
            symbol="€",
            symbol_after=True,
            country="FR",
            document_type="invoice",
            invoice_number="F-2026-112",
            issue_date=date(2026, 9, 12),
            due_date=date(2026, 10, 12),
            issuer=Party("Exemple Conseil SARL", "FR00000000000", "TVA", "12 rue de Rivoli, Paris"),
            recipient=Party("Client Démo SAS", address="Lyon"),
            lines=[
                Line("Formation équipe (journée)", "3", "980.00"),
                Line("Support pédagogique", "12", "15.00"),
            ],
            taxes=[TaxSpec("TVA", "20")],
            payment_method="Virement",
        ),
        Case(
            id="06-br-nota",
            kind="pdf",
            lang="pt",
            number_style="eu",
            date_format="%d/%m/%Y",
            currency="BRL",
            symbol="R$ ",
            symbol_after=False,
            country="BR",
            document_type="invoice",
            invoice_number="000.451",
            issue_date=date(2026, 8, 5),
            issuer=Party(
                "Exemplo Comércio Ltda.",
                "00.000.000/0001-00",
                "CNPJ",
                "Av. Paulista 1000, São Paulo",
            ),
            recipient=Party("Cliente Demonstração ME", "11.111.111/0001-11", "CNPJ"),
            lines=[
                Line("Cadeira de escritório", "5", "389.90"),
                Line("Mesa de reunião", "1", "1450.00"),
            ],
            taxes=[TaxSpec("ICMS", "18")],
            payment_method="Boleto",
        ),
        Case(
            id="07-ar-factura-foto",
            kind="photo",
            lang="es",
            number_style="eu",
            date_format="%d/%m/%Y",
            currency="ARS",
            symbol="$ ",
            symbol_after=False,
            country="AR",
            document_type="invoice",
            invoice_number="0003-00001234",
            issue_date=date(2026, 7, 21),
            issuer=Party(
                "Ejemplo Insumos S.R.L.", "30-00000000-0", "CUIT", "Av. Corrientes 1234, CABA"
            ),
            recipient=Party("Consumidor Final"),
            lines=[
                Line("Resma papel A4", "10", "8500.00"),
                Line("Tóner impresora", "2", "45990.00"),
            ],
            taxes=[TaxSpec("IVA", "21")],
            payment_method="Efectivo",
        ),
        Case(
            id="08-co-factura",
            kind="pdf",
            lang="es",
            number_style="eu",
            date_format="%Y-%m-%d",
            currency="COP",
            symbol="$",
            symbol_after=False,
            country="CO",
            document_type="invoice",
            invoice_number="FE-9087",
            issue_date=date(2026, 6, 30),
            due_date=date(2026, 7, 30),
            issuer=Party(
                "Ejemplo Digital S.A.S.", "900.000.000-1", "NIT", "Cra. 7 # 71-21, Bogotá"
            ),
            recipient=Party("Cliente Demo Ltda.", "800.000.000-2", "NIT"),
            lines=[
                Line("Desarrollo página web", "1", "3500000"),
                Line("Hosting anual", "1", "480000"),
            ],
            taxes=[TaxSpec("IVA", "19")],
            payment_method="Transferencia",
        ),
        Case(
            id="09-uk-invoice",
            kind="pdf",
            lang="en",
            number_style="en",
            date_format="%d %B %Y",
            currency="GBP",
            symbol="£",
            symbol_after=False,
            country="GB",
            document_type="invoice",
            invoice_number="INV-3021",
            issue_date=date(2026, 5, 2),
            due_date=date(2026, 6, 1),
            issuer=Party(
                "Example Studio Ltd", "GB000000000", "VAT Reg No", "10 Downing Lane, London"
            ),
            recipient=Party("Demo Retail plc", address="Manchester"),
            lines=[
                Line("Photography session", "1", "650.00"),
                Line("Edited images", "25", "8.00"),
            ],
            taxes=[TaxSpec("VAT", "20")],
            discount_total="50.00",
            payment_method="Bank transfer",
        ),
        Case(
            id="10-jp-receipt-ticket",
            kind="ticket",
            lang="en",
            number_style="en",
            date_format="%Y/%m/%d",
            currency="JPY",
            symbol="¥",
            symbol_after=False,
            country="JP",
            document_type="receipt",
            issue_date=date(2026, 4, 18),
            issuer=Party("Sample Mart Shibuya", address="Shibuya, Tokyo"),
            lines=[
                Line("Green tea 500ml", "2", "160"),
                Line("Onigiri salmon", "3", "180"),
                Line("Sandwich", "1", "420"),
            ],
            taxes=[TaxSpec("Consumption tax", "8")],
            payment_method="Cash",
        ),
        Case(
            id="11-us-invoice-multipagina",
            kind="pdf",
            lang="en",
            number_style="en",
            date_format="%m/%d/%Y",
            currency="USD",
            symbol="$",
            symbol_after=False,
            country="US",
            document_type="invoice",
            invoice_number="10045",
            issue_date=date(2026, 2, 3),
            due_date=date(2026, 3, 5),
            issuer=Party("Example Supplies Inc.", "00-0000000", "EIN", "500 Market St, Austin, TX"),
            recipient=Party("Demo Builders LLC", address="Dallas, TX"),
            lines=[
                Line(
                    f"Item {chr(65 + n)} - parts kit #{100 + n}", str(n % 4 + 1), f"{12 + n * 3}.50"
                )
                for n in range(18)
            ],
            taxes=[TaxSpec("Sales tax", "8.25")],
            lines_per_page=10,
            payment_method="Net 30",
        ),
        Case(
            id="12-es-nota-credito",
            kind="pdf",
            lang="es",
            number_style="eu",
            date_format="%d/%m/%Y",
            currency="EUR",
            symbol="€",
            symbol_after=True,
            country="ES",
            document_type="credit_note",
            invoice_number="R-2026/012",
            issue_date=date(2026, 11, 2),
            issuer=Party("Electro Ejemplo S.A.", "A00000000", "CIF", "Valencia"),
            recipient=Party("Juan Demo Pérez", "11111111H", "NIF"),
            lines=[Line("Devolución auriculares inalámbricos", "-1", "82.64")],
            taxes=[TaxSpec("IVA", "21")],
            notes="Rectifica la factura 2026/0988",
        ),
        Case(
            id="13-no-factura",
            kind="not_invoice",
            lang="es",
            number_style="en",
            date_format="%d/%m/%Y",
            currency="EUR",
            symbol="€",
            symbol_after=True,
            country="ES",
            document_type="other",
            issue_date=date(2026, 1, 1),
            issuer=Party("-"),
            lines=[],
            taxes=[],
        ),
    ]


def generate(cases_dir: Path = CASES_DIR) -> int:
    if cases_dir.exists():
        shutil.rmtree(cases_dir)
    for case in _cases():
        folder = cases_dir / case.id
        folder.mkdir(parents=True)
        is_invoice = case.kind != "not_invoice"
        invoice = expected_invoice(case) if is_invoice else None
        file_name, data = render(case, invoice or {})
        (folder / file_name).write_bytes(data)
        expected = {"file": file_name, "is_invoice": is_invoice, "invoice": invoice}
        (folder / "expected.json").write_text(
            json.dumps(expected, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return len(_cases())


if __name__ == "__main__":
    count = generate()
    print(f"{count} casos generados en {CASES_DIR}")
