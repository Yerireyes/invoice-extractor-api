from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest

from app.core.config import LLMProvider
from app.core.errors import AppError, ErrorCode
from app.invoices.ports import ExtractionFailedError, ExtractionOutput, NotAnInvoiceError
from app.invoices.schemas import Invoice
from app.invoices.validation import validate_invoice
from app.scripts import eval as evaluation
from app.scripts.eval import (
    FIELDS,
    CaseResult,
    EvalCase,
    compare,
    field_accuracy,
    load_cases,
    run,
    to_markdown,
)
from app.shared.files import PageImage, prepare_file
from tests.factories import image_bytes, invoice_payload

pytestmark = pytest.mark.anyio

EXPECTED = Invoice.model_validate(invoice_payload())


def _invoice(**overrides: object) -> Invoice:
    return Invoice.model_validate(invoice_payload(**overrides))


# --- Conjunto de evaluación versionado ---------------------------------------------------------


def test_committed_dataset_is_consistent() -> None:
    cases = load_cases()

    assert len(cases) >= 10
    assert any(case.expected is None for case in cases), "falta un caso que no sea factura"
    for case in cases:
        prepared = prepare_file(case.document, max_pdf_pages=5)
        assert prepared.pages, case.case_id
        if case.expected is not None:
            validation = validate_invoice(case.expected, today=date(2026, 12, 31))
            assert validation.status == "valid", (case.case_id, validation.issues)


def test_dataset_includes_variety() -> None:
    invoices = [case.expected for case in load_cases() if case.expected]

    assert len({invoice.currency for invoice in invoices}) >= 6
    assert len({invoice.language for invoice in invoices}) >= 4
    assert {invoice.document_type for invoice in invoices} >= {"invoice", "receipt", "credit_note"}
    assert any(len(case.document) and case.case_id.endswith("multipagina") for case in load_cases())


def test_load_cases_filters_by_name() -> None:
    assert [case.case_id for case in load_cases(only=["mx"])] == ["01-mx-factura"]


# --- Comparación -------------------------------------------------------------------------------


def test_identical_invoices_score_everything() -> None:
    result = compare(EXPECTED, EXPECTED)

    assert set(result) == set(FIELDS)
    assert all(result.values())


def test_text_comparison_ignores_case_punctuation_and_accents() -> None:
    actual = _invoice(issuer={"name": "comercial xyz sa", "tax_id": "xyz-010101-abc"})
    expected = _invoice(issuer={"name": "Comercial XYZ S.A.", "tax_id": "XYZ010101ABC"})

    result = compare(expected, actual)

    assert result["issuer.name"]
    assert result["issuer.tax_id"]


def test_amounts_compare_numerically() -> None:
    assert compare(EXPECTED, _invoice(total="1160"))["total"]
    assert not compare(EXPECTED, _invoice(total="1160.01"))["total"]


def test_hallucinated_value_counts_as_error() -> None:
    expected = _invoice(due_date=None)

    assert not compare(expected, _invoice(due_date="2026-12-31"))["due_date"]


def test_lists_compare_count_and_amounts() -> None:
    lines = [*invoice_payload()["line_items"], {"description": "Extra", "amount": "5.00"}]

    result = compare(EXPECTED, _invoice(line_items=lines))

    assert not result["line_items.count"]
    assert not result["line_items.amounts"]
    assert result["taxes.amounts"]


@pytest.mark.parametrize(
    ("expected", "actual", "ok"),
    [(None, None, True), (None, EXPECTED, False), (EXPECTED, None, False)],
)
def test_not_invoice_cases_only_score_detection(
    expected: Invoice | None, actual: Invoice | None, ok: bool
) -> None:
    assert compare(expected, actual) == {"is_invoice": ok}


# --- Ejecución ---------------------------------------------------------------------------------


class ScriptedExtractor:
    """Devuelve (o lanza) lo indicado para cada llamada, en orden."""

    def __init__(self, *outcomes: Invoice | Exception) -> None:
        self.outcomes = list(outcomes)

    @property
    def provider(self) -> LLMProvider:
        return "fake"

    async def extract(self, pages: Sequence[PageImage]) -> ExtractionOutput:
        assert pages
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return ExtractionOutput(invoice=outcome, model="scripted")


class SleepRecorder:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.calls.append(delay)


def _case(case_id: str, expected: Invoice | None, document: bytes | None = None) -> EvalCase:
    return EvalCase(case_id, document or image_bytes("PNG"), expected)


async def test_run_scores_each_case_and_waits_between_requests() -> None:
    cases = [
        _case("perfecto", EXPECTED),
        _case("total-mal", EXPECTED),
        _case("no-factura", None),
        _case("falla", EXPECTED),
        _case("roto", EXPECTED, b"no es un archivo"),
    ]
    extractor = ScriptedExtractor(
        EXPECTED, _invoice(total="1.00"), NotAnInvoiceError(), ExtractionFailedError()
    )
    sleep = SleepRecorder()
    seen: list[str] = []

    results = await run(
        extractor,
        cases,
        max_pdf_pages=5,
        delay_s=2,
        sleep=sleep,
        progress=lambda result: seen.append(result.case_id),
    )

    assert [result.score for result in results][:3] == [1.0, (len(FIELDS) - 1) / len(FIELDS), 1.0]
    assert results[3].error == "EXTRACTION_FAILED"
    assert results[3].score == 0
    assert results[4].error == "UNSUPPORTED_FILE_TYPE"
    assert sleep.calls == [2, 2, 2, 2]
    assert seen == [case.case_id for case in cases]


async def test_provider_errors_stop_the_evaluation() -> None:
    extractor = ScriptedExtractor(AppError(ErrorCode.LLM_QUOTA_EXCEEDED))

    with pytest.raises(AppError):
        await run(extractor, [_case("a", EXPECTED), _case("b", EXPECTED)], max_pdf_pages=5)


def test_report() -> None:
    results = [
        CaseResult("a", {"is_invoice": True, "total": True}, elapsed_ms=1000),
        CaseResult("b", {"is_invoice": True, "total": False}, elapsed_ms=3000),
        CaseResult("c", {"is_invoice": False}, error="EXTRACTION_FAILED", elapsed_ms=500),
    ]

    assert field_accuracy(results) == {"is_invoice": (2, 3), "total": (1, 2)}
    report = to_markdown(results, provider="gemini", model="m")
    assert "**precisión global:** 60.0% (3/5 campos)" in report
    assert "**latencia mediana:** 1.0 s" in report
    assert "| `total` | 1/2 | 50% |" in report
    assert "| b | 50% | `total` | 3.0 s |" in report
    assert "| c | 0% | EXTRACTION_FAILED | 0.5 s |" in report
    assert "| a | 100% | — | 1.0 s |" in report


# --- CLI ---------------------------------------------------------------------------------------


@pytest.fixture
def no_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ignora la clave del `.env` local: el CLI usará el extractor falso."""
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("APP_ENV", "development")


@pytest.mark.usefixtures("no_gemini")
def test_cli_runs_with_fake_provider(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report = tmp_path / "eval.md"

    code = evaluation.main(["--only", "01", "13", "--delay", "0", "--markdown", str(report)])

    assert code == 0
    output = capsys.readouterr().out
    assert "Evaluando 2 casos con fake" in output
    assert "01-mx-factura" in report.read_text(encoding="utf-8")


def test_cli_reports_unavailable_provider(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("APP_ENV", "production")

    assert evaluation.main(["--provider", "gemini"]) == 2
    assert "no disponible" in capsys.readouterr().err


@pytest.mark.usefixtures("no_gemini")
def test_cli_stops_on_provider_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    failing = ScriptedExtractor(AppError(ErrorCode.LLM_UNAVAILABLE))
    monkeypatch.setattr(evaluation, "get_extractor_factory", lambda _: lambda _: failing)

    assert evaluation.main(["--only", "01", "--delay", "0"]) == 1
    assert "LLM_UNAVAILABLE" in capsys.readouterr().err
