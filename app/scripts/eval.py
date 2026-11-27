"""Mide la calidad de extracción del proveedor configurado sobre facturas sintéticas.

Cada caso de `tests/eval/cases/<id>/` tiene un documento y su `expected.json`. El script ejecuta el
extractor real (gasta cuota: una petición por caso), compara campo a campo y muestra la precisión
por campo y por caso. No corre en el CI.

    uv run python -m app.scripts.eval                     # proveedor por defecto de .env
    uv run python -m app.scripts.eval --delay 5 --markdown resultados.md

Criterios de comparación:
- Textos: sin distinguir mayúsculas, espacios ni puntuación ("S.A. de C.V." == "SA de CV").
- Importes: igualdad numérica exacta ("1160" == "1160.00").
- `null` esperado: el valor extraído también debe ser `null` (inventar datos cuenta como error).
- Líneas e impuestos: número de elementos y multiconjunto de importes.
"""

import argparse
import asyncio
import json
import re
import sys
import time
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.core.config import LLMProvider, Settings
from app.core.errors import AppError
from app.invoices.ports import ExtractionFailedError, InvoiceExtractor, NotAnInvoiceError
from app.invoices.providers import get_extractor_factory
from app.invoices.schemas import Invoice
from app.shared.files import FileRejectedError, prepare_file

CASES_DIR = Path(__file__).resolve().parents[2] / "tests" / "eval" / "cases"

TEXT_FIELDS = (
    "document_type",
    "invoice_number",
    "currency",
    "country",
    "language",
    "issuer.name",
    "issuer.tax_id",
    "recipient.name",
    "recipient.tax_id",
)
DATE_FIELDS = ("issue_date", "due_date")
AMOUNT_FIELDS = ("subtotal", "discount_total", "total")
LIST_FIELDS = ("line_items.count", "line_items.amounts", "taxes.amounts")
FIELDS = ("is_invoice", *TEXT_FIELDS, *DATE_FIELDS, *AMOUNT_FIELDS, *LIST_FIELDS)

type Sleep = Callable[[float], Any]


# ---------------------------------------------------------------------------
# Comparación
# ---------------------------------------------------------------------------


def _get(invoice: Invoice | None, path: str) -> Any:
    value: Any = invoice
    for part in path.split("."):
        value = getattr(value, part, None) if value is not None else None
    return value


def _normalize_text(value: object) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower()) or None


def _amounts(items: Iterable[Any]) -> list[Decimal]:
    return sorted(Decimal(item.amount).normalize() for item in items)


def compare(expected: Invoice | None, actual: Invoice | None) -> dict[str, bool]:
    """Acierto (`True`) o fallo por campo. Si no hay factura esperada, solo cuenta `is_invoice`."""
    results = {"is_invoice": (expected is None) == (actual is None)}
    if expected is None or actual is None:
        return results
    for path in TEXT_FIELDS:
        results[path] = _normalize_text(_get(expected, path)) == _normalize_text(_get(actual, path))
    for path in (*DATE_FIELDS, *AMOUNT_FIELDS):
        results[path] = _get(expected, path) == _get(actual, path)
    results["line_items.count"] = len(expected.line_items) == len(actual.line_items)
    results["line_items.amounts"] = _amounts(expected.line_items) == _amounts(actual.line_items)
    results["taxes.amounts"] = _amounts(expected.taxes) == _amounts(actual.taxes)
    return results


# ---------------------------------------------------------------------------
# Ejecución
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    case_id: str
    fields: dict[str, bool] = field(default_factory=dict)
    error: str | None = None
    elapsed_ms: int = 0

    @property
    def score(self) -> float:
        return sum(self.fields.values()) / len(self.fields) if self.fields else 0.0


@dataclass
class EvalCase:
    case_id: str
    document: bytes
    expected: Invoice | None


def load_cases(cases_dir: Path = CASES_DIR, only: Sequence[str] = ()) -> list[EvalCase]:
    cases = []
    for folder in sorted(path for path in cases_dir.iterdir() if path.is_dir()):
        if only and not any(name in folder.name for name in only):
            continue
        spec = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
        expected = Invoice.model_validate(spec["invoice"]) if spec["is_invoice"] else None
        cases.append(EvalCase(folder.name, (folder / spec["file"]).read_bytes(), expected))
    return cases


async def evaluate_case(
    extractor: InvoiceExtractor, case: EvalCase, *, max_pdf_pages: int
) -> CaseResult:
    started = time.perf_counter()
    actual: Invoice | None = None
    error: str | None = None
    try:
        pages = prepare_file(case.document, max_pdf_pages=max_pdf_pages).pages
        actual = (await extractor.extract(pages)).invoice
    except NotAnInvoiceError:
        actual = None
    except ExtractionFailedError:
        error = "EXTRACTION_FAILED"
    except FileRejectedError as exc:
        error = exc.code.value
    elapsed = round((time.perf_counter() - started) * 1000)
    if error:
        fields = dict.fromkeys(FIELDS if case.expected else ("is_invoice",), False)
        return CaseResult(case.case_id, fields, error, elapsed)
    return CaseResult(case.case_id, compare(case.expected, actual), None, elapsed)


async def run(
    extractor: InvoiceExtractor,
    cases: Sequence[EvalCase],
    *,
    max_pdf_pages: int,
    delay_s: float = 0,
    sleep: Sleep = asyncio.sleep,
    progress: Callable[[CaseResult], None] | None = None,
) -> list[CaseResult]:
    """Evalúa los casos en secuencia (con pausa opcional para respetar el límite por minuto).

    Un `AppError` (cuota agotada, proveedor caído) detiene la evaluación y se propaga.
    """
    results = []
    for index, case in enumerate(cases):
        if index and delay_s:
            await sleep(delay_s)
        result = await evaluate_case(extractor, case, max_pdf_pages=max_pdf_pages)
        results.append(result)
        if progress:
            progress(result)
    return results


# ---------------------------------------------------------------------------
# Informe
# ---------------------------------------------------------------------------


def field_accuracy(results: Sequence[CaseResult]) -> dict[str, tuple[int, int]]:
    """Aciertos y total por campo (solo en los casos donde el campo se evaluó)."""
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for result in results:
        for name, ok in result.fields.items():
            totals[name][0] += ok
            totals[name][1] += 1
    return {name: (totals[name][0], totals[name][1]) for name in FIELDS if name in totals}


def to_markdown(results: Sequence[CaseResult], *, provider: str, model: str) -> str:
    accuracy = field_accuracy(results)
    hits = sum(ok for ok, _ in accuracy.values())
    checks = sum(total for _, total in accuracy.values())
    latencies = sorted(result.elapsed_ms for result in results)
    lines = [
        f"**Proveedor:** `{provider}` · **modelo:** `{model}` · **casos:** {len(results)} · "
        f"**precisión global:** {hits / checks:.1%} ({hits}/{checks} campos) · "
        f"**latencia mediana:** {latencies[len(latencies) // 2] / 1000:.1f} s",
        "",
        "| Campo | Aciertos | Precisión |",
        "|---|---|---|",
        *(
            f"| `{name}` | {ok}/{total} | {ok / total:.0%} |"
            for name, (ok, total) in accuracy.items()
        ),
        "",
        "| Caso | Precisión | Fallos | Tiempo |",
        "|---|---|---|---|",
    ]
    for result in results:
        failed = [name for name, ok in result.fields.items() if not ok]
        detail = result.error or (", ".join(f"`{name}`" for name in failed) if failed else "—")
        lines.append(
            f"| {result.case_id} | {result.score:.0%} | {detail} | "
            f"{result.elapsed_ms / 1000:.1f} s |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evalúa la calidad de extracción")
    parser.add_argument("--provider", choices=["gemini", "ollama", "fake"], default=None)
    parser.add_argument("--cases", type=Path, default=CASES_DIR)
    parser.add_argument("--only", nargs="*", default=(), help="filtra casos por nombre")
    parser.add_argument("--delay", type=float, default=5.0, help="segundos entre peticiones")
    parser.add_argument("--markdown", type=Path, help="guarda el informe en un archivo")
    args = parser.parse_args(argv)

    settings = Settings()
    provider: LLMProvider | None = args.provider
    try:
        extractor = get_extractor_factory(settings)(provider)
    except AppError as exc:
        print(f"Proveedor no disponible: {exc.params}", file=sys.stderr)
        return 2

    cases = load_cases(args.cases, args.only)
    model = settings.gemini_model if extractor.provider == "gemini" else extractor.provider
    print(f"Evaluando {len(cases)} casos con {extractor.provider} ({model})...")

    def progress(result: CaseResult) -> None:
        status = result.error or f"{result.score:.0%}"
        print(f"  {result.case_id:<32} {status:>8}  {result.elapsed_ms / 1000:5.1f} s")

    try:
        results = asyncio.run(
            run(
                extractor,
                cases,
                max_pdf_pages=settings.max_pdf_pages,
                delay_s=args.delay,
                progress=progress,
            )
        )
    except AppError as exc:
        print(f"Evaluación interrumpida: {exc.code} {exc.params}", file=sys.stderr)
        return 1

    report = to_markdown(results, provider=extractor.provider, model=model)
    print()
    print(report)
    if args.markdown:
        args.markdown.write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
