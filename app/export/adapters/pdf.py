"""Exportador PDF (sección 8 del contrato) con ReportLab. No importa FastAPI.

- Cada factura empieza en una página nueva: encabezado (tipo, número, fechas, Ref y estado),
  emisor/receptor, conceptos (opcional), impuestos, totales, notas y alertas (opcional). Si una
  factura tiene muchos conceptos continúa en la página siguiente (se repite el encabezado).
- Fuente DejaVu Sans incrustada (`app/export/fonts/`): acentos, ñ, €, ₹, ₩, ₫, griego, cirílico...
- Montos con separador de miles y 2 decimales (0 en monedas sin decimales); nunca se suman monedas.
- Seguridad: los textos (que vienen de la IA o del usuario) se escapan antes de pasar a los
  `Paragraph` de ReportLab, que interpretan un mini-HTML.
"""

import io
from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal
from functools import cache, partial
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    CondPageBreak,
    Flowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.export.i18n import ExportLanguage, field_label, issue_message, text
from app.export.ports import ExportDocument
from app.export.schemas import ExportInvoice, ExportOptions
from app.invoices.currencies import has_decimals
from app.invoices.schemas import Party

PDF_MEDIA_TYPE = "application/pdf"

FONT = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"
FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"

PAGE_SIZE = A4
MARGIN = 18 * mm
FRAME_PADDING = 6  # relleno interno por defecto del Frame de `SimpleDocTemplate` (pt)
CONTENT_WIDTH = PAGE_SIZE[0] - 2 * MARGIN - 2 * FRAME_PADDING
EMPTY = "—"
CELL_PADDING = 4  # pt

GRID = colors.HexColor("#C8CDD3")
HEADER_BG = colors.HexColor("#EEF1F4")
MUTED = colors.HexColor("#5F6B76")
STATUS_COLORS = {
    "valid": colors.HexColor("#1E7B34"),
    "warnings": colors.HexColor("#9A6700"),
    "invalid": colors.HexColor("#B42318"),
}
SEVERITY_BG = {
    "error": colors.HexColor("#FDE2E1"),
    "warning": colors.HexColor("#FFF4CC"),
    "info": colors.HexColor("#E3EEFB"),
}


@cache
def register_fonts() -> None:
    """Registra DejaVu Sans (normal y negrita) una sola vez por proceso."""
    pdfmetrics.registerFont(TTFont(FONT, FONTS_DIR / "DejaVuSans.ttf"))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, FONTS_DIR / "DejaVuSans-Bold.ttf"))
    pdfmetrics.registerFontFamily(
        FONT, normal=FONT, bold=FONT_BOLD, italic=FONT, boldItalic=FONT_BOLD
    )


BODY = ParagraphStyle("body", fontName=FONT, fontSize=9, leading=12)
BODY_RIGHT = ParagraphStyle("body_right", parent=BODY, alignment=TA_RIGHT)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=10, textColor=MUTED)
CELL_HEADER = ParagraphStyle(
    "cell_header", parent=BODY, fontName=FONT_BOLD, fontSize=7.5, leading=10
)
CELL_HEADER_RIGHT = ParagraphStyle("cell_header_right", parent=CELL_HEADER, alignment=TA_RIGHT)
TITLE = ParagraphStyle("title", parent=BODY, fontName=FONT_BOLD, fontSize=16, leading=20)
HEADING = ParagraphStyle(
    "heading",
    parent=BODY,
    fontName=FONT_BOLD,
    fontSize=11,
    leading=14,
    spaceBefore=10,
)
HEADING_MIN_SPACE = 25 * mm  # si queda menos espacio, el título salta a la página siguiente
STATUS = ParagraphStyle("status", parent=BODY_RIGHT, fontName=FONT_BOLD, fontSize=10)
TOTAL = ParagraphStyle("total", parent=STATUS)
TOTAL_LABEL = ParagraphStyle("total_label", parent=BODY, fontName=FONT_BOLD, fontSize=10)
STATUS_STYLES = {
    status: ParagraphStyle(f"status_{status}", parent=STATUS, textColor=color)
    for status, color in STATUS_COLORS.items()
}


class PdfExporter:
    """Implementa el puerto `Exporter` para `.pdf`.

    `generated_on` es la fecha del pie de página ("Generado el ..."): se inyecta para que el
    resultado no dependa del reloj en los tests.
    """

    def __init__(self, *, generated_on: date) -> None:
        self._generated_on = generated_on

    @property
    def media_type(self) -> str:
        return PDF_MEDIA_TYPE

    @property
    def extension(self) -> str:
        return "pdf"

    def export(self, documents: Sequence[ExportDocument], options: ExportOptions) -> bytes:
        register_fonts()
        lang = options.language
        output = io.BytesIO()
        template = SimpleDocTemplate(
            output,
            pagesize=PAGE_SIZE,
            leftMargin=MARGIN,
            rightMargin=MARGIN,
            topMargin=MARGIN,
            bottomMargin=MARGIN,
            title=text(lang, "pdf.title"),
            creator="Invoice Extractor API",
        )
        story: list[Flowable] = []
        for index, doc in enumerate(documents):
            if index:
                story.append(PageBreak())
            story.extend(_invoice_story(doc, options))

        canvas_maker = partial(
            _NumberedCanvas,
            footer=text(lang, "pdf.generated", date=self._generated_on.isoformat()),
            page_label=lambda page, pages: text(lang, "pdf.page", page=page, pages=pages),
        )
        template.build(story, canvasmaker=canvas_maker)
        return output.getvalue()


class _NumberedCanvas(Canvas):
    """Canvas que difiere el pie de página hasta conocer el total ("Página 1 de N")."""

    def __init__(
        self,
        *args: Any,
        footer: str,
        page_label: Callable[[int, int], str],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._footer = footer
        self._page_label = page_label
        self._pages: list[dict[str, Any]] = []

    def showPage(self) -> None:  # noqa: N802  # API de ReportLab
        self._pages.append(dict(self.__dict__))
        self._startPage()  # type: ignore[attr-defined]  # privado, sin stub

    def save(self) -> None:
        total = len(self._pages)
        for number, state in enumerate(self._pages, start=1):
            self.__dict__.update(state)
            self._draw_footer(number, total)
            super().showPage()
        super().save()

    def _draw_footer(self, page: int, pages: int) -> None:
        self.saveState()
        self.setFont(FONT, 8)
        self.setFillColor(MUTED)
        y = MARGIN / 2
        self.drawString(MARGIN, y, self._footer)
        self.drawRightString(PAGE_SIZE[0] - MARGIN, y, self._page_label(page, pages))
        self.restoreState()


# ---------------------------------------------------------------------------
# Contenido de una factura
# ---------------------------------------------------------------------------


def _invoice_story(doc: ExportDocument, options: ExportOptions) -> list[Flowable]:
    lang = options.language
    invoice = doc.invoice
    story: list[Flowable] = [
        _header(doc, lang),
        Spacer(1, 3 * mm),
        _paragraph(_meta_line(invoice, lang), SMALL),
        Spacer(1, 5 * mm),
        _parties(invoice, lang),
    ]
    if options.include_line_items and invoice.line_items:
        story += [*_heading(text(lang, "sheet.line_items")), _line_items(invoice, lang)]
    if invoice.taxes:
        story += [*_heading(text(lang, "sheet.taxes")), _taxes(invoice, lang)]
    story += [Spacer(1, 5 * mm), _totals(invoice, lang)]
    if invoice.notes:
        story += [*_heading(text(lang, "pdf.notes")), _paragraph(invoice.notes)]
    if options.include_alerts:
        story += [*_heading(text(lang, "pdf.alerts")), _alerts(doc, lang)]
    return story


def _heading(title: str) -> list[Flowable]:
    """Título de sección que nunca queda solo al pie de la página."""
    return [CondPageBreak(HEADING_MIN_SPACE), _paragraph(title, HEADING)]


def _header(doc: ExportDocument, lang: ExportLanguage) -> Flowable:
    invoice = doc.invoice
    title = text(lang, f"type.{invoice.document_type}")
    if invoice.invoice_number:
        title = f"{title} {invoice.invoice_number}"
    status = doc.validation.status
    table = Table(
        [
            [
                _paragraph(title, TITLE),
                _paragraph(f"{doc.ref} · {text(lang, f'status.{status}')}", STATUS_STYLES[status]),
            ]
        ],
        colWidths=[CONTENT_WIDTH * 0.65, CONTENT_WIDTH * 0.35],
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("LINEBELOW", (0, 0), (-1, 0), 1, GRID),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def _meta_line(invoice: ExportInvoice, lang: ExportLanguage) -> str:
    parts = [
        (text(lang, "col.issue_date"), _date(invoice.issue_date)),
        (text(lang, "col.due_date"), _date(invoice.due_date)),
        (text(lang, "col.currency"), invoice.currency or EMPTY),
    ]
    if invoice.payment_method:
        parts.append((text(lang, "col.payment_method"), invoice.payment_method))
    if invoice.source_file_name:
        parts.append((text(lang, "col.file"), invoice.source_file_name))
    return "   ·   ".join(f"{label}: {value}" for label, value in parts)


def _parties(invoice: ExportInvoice, lang: ExportLanguage) -> Flowable:
    table = Table(
        [
            [
                _paragraph(text(lang, "pdf.issuer"), CELL_HEADER),
                _paragraph(text(lang, "pdf.recipient"), CELL_HEADER),
            ],
            [_party(invoice.issuer, lang), _party(invoice.recipient, lang)],
        ],
        colWidths=[CONTENT_WIDTH / 2] * 2,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                ("BOX", (0, 0), (0, -1), 0.5, GRID),
                ("BOX", (1, 0), (1, -1), 0.5, GRID),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def _party(party: Party | None, lang: ExportLanguage) -> list[Flowable]:
    if party is None or not any(
        (party.name, party.tax_id, party.address, party.email, party.phone)
    ):
        return [_paragraph(EMPTY)]
    lines: list[Flowable] = [_paragraph(party.name or EMPTY, CELL_HEADER)]
    if party.tax_id:
        kind = f" ({party.tax_id_type})" if party.tax_id_type else ""
        lines.append(_paragraph(f"{text(lang, 'pdf.tax_id')}: {party.tax_id}{kind}"))
    lines += [_paragraph(value) for value in (party.address, party.email, party.phone) if value]
    return lines


def _line_items(invoice: ExportInvoice, lang: ExportLanguage) -> Flowable:
    currency = invoice.currency
    headers = (
        ("col.description", False),
        ("col.quantity", True),
        ("col.unit", False),
        ("col.unit_price", True),
        ("col.discount", True),
        ("col.tax_rate", True),
        ("col.amount", True),
    )
    rows = [
        [
            _paragraph(line.description or EMPTY),
            _paragraph(_number(line.quantity), BODY_RIGHT),
            _paragraph(line.unit or ""),
            _paragraph(_money(line.unit_price, currency), BODY_RIGHT),
            _paragraph(_money(line.discount, currency), BODY_RIGHT),
            _paragraph(_number(line.tax_rate), BODY_RIGHT),
            _paragraph(_money(line.amount, currency), BODY_RIGHT),
        ]
        for line in invoice.line_items
    ]
    shares = [31, 10, 9, 13, 12, 11, 14]  # % del ancho útil; caben los encabezados es/en
    return _grid(lang, headers, rows, [CONTENT_WIDTH * share / 100 for share in shares])


def _taxes(invoice: ExportInvoice, lang: ExportLanguage) -> Flowable:
    currency = invoice.currency
    headers = (("col.tax", False), ("col.rate", True), ("col.base", True), ("col.amount", True))
    rows = [
        [
            _paragraph(tax.name or EMPTY),
            _paragraph(_number(tax.rate), BODY_RIGHT),
            _paragraph(_money(tax.base, currency), BODY_RIGHT),
            _paragraph(_money(tax.amount, currency), BODY_RIGHT),
        ]
        for tax in invoice.taxes
    ]
    widths = [CONTENT_WIDTH - 3 * 35 * mm, 35 * mm, 35 * mm, 35 * mm]
    return _grid(lang, headers, rows, widths)


def _totals(invoice: ExportInvoice, lang: ExportLanguage) -> Flowable:
    currency = invoice.currency

    def amount(value: Decimal | None) -> str:
        if value is None:
            return EMPTY
        return f"{_money(value, currency)} {currency}" if currency else _money(value, currency)

    taxes = sum((tax.amount for tax in invoice.taxes), Decimal(0)) if invoice.taxes else None
    rows = [
        (text(lang, "col.subtotal"), invoice.subtotal),
        (text(lang, "col.discounts"), invoice.discount_total),
        (text(lang, "col.taxes"), taxes),
    ]
    data = [[_paragraph(label), _paragraph(amount(value), BODY_RIGHT)] for label, value in rows]
    data.append(
        [
            _paragraph(text(lang, "col.total"), TOTAL_LABEL),
            _paragraph(amount(invoice.total), TOTAL),
        ]
    )
    table = Table(data, colWidths=[40 * mm, 50 * mm], hAlign="RIGHT")
    table.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, -1), (-1, -1), 1, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return table


def _alerts(doc: ExportDocument, lang: ExportLanguage) -> Flowable:
    issues = doc.validation.issues
    if not issues:
        return _paragraph(text(lang, "pdf.no_alerts"), SMALL)
    headers = (("col.severity", False), ("col.field", False), ("col.message", False))
    rows = [
        [
            _paragraph(text(lang, f"severity.{issue.severity}")),
            _paragraph(field_label(lang, issue.field)),
            _paragraph(issue_message(lang, issue)),
        ]
        for issue in issues
    ]
    backgrounds = [
        ("BACKGROUND", (0, row), (-1, row), SEVERITY_BG[issue.severity])
        for row, issue in enumerate(issues, start=1)
    ]
    widths = [22 * mm, 38 * mm, CONTENT_WIDTH - 60 * mm]
    return _grid(lang, headers, rows, widths, extra_style=backgrounds)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _grid(
    lang: ExportLanguage,
    headers: Sequence[tuple[str, bool]],
    rows: Sequence[Sequence[Flowable]],
    widths: Sequence[float],
    *,
    extra_style: Sequence[tuple[Any, ...]] = (),
) -> Flowable:
    """Tabla con encabezado (que se repite si la tabla salta de página) y rejilla fina."""
    header_row: list[Flowable] = [
        _paragraph(text(lang, key), CELL_HEADER_RIGHT if right else CELL_HEADER)
        for key, right in headers
    ]
    table = Table([header_row, *(list(row) for row in rows)], colWidths=list(widths), repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                ("GRID", (0, 0), (-1, -1), 0.5, GRID),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), CELL_PADDING),
                ("RIGHTPADDING", (0, 0), (-1, -1), CELL_PADDING),
                *extra_style,
            ]
        )
    )
    return table


def _paragraph(value: str, style: ParagraphStyle = BODY) -> Paragraph:
    """`Paragraph` con el texto escapado: `<`, `>` y `&` se muestran tal cual."""
    return Paragraph(escape(value), style)


def _date(value: date | None) -> str:
    return value.isoformat() if value else EMPTY


def _money(value: Decimal | None, currency: str | None) -> str:
    if value is None:
        return EMPTY
    return f"{value:,.2f}" if has_decimals(currency) else f"{value:,.0f}"


def _number(value: Decimal | None) -> str:
    """Cantidades y tasas sin ceros sobrantes: `2.000` → `2`, `16.50` → `16.5`."""
    if value is None:
        return ""
    return format(value.normalize(), "f")
