from decimal import Decimal
from typing import Any

import httpx
import pytest
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.invoices.adapters.gemini import (
    GeminiExtractor,
    LlmExtraction,
    LlmFieldConfidence,
    LlmLineItem,
    LlmParty,
    LlmTax,
    to_extraction_output,
)
from app.invoices.adapters.prompts import SYSTEM_PROMPT
from app.invoices.ports import ExtractionFailedError, NotAnInvoiceError
from app.invoices.providers import get_extractor_factory
from app.shared.files import PageImage

MODEL = "gemini-test"


def _llm(**overrides: Any) -> LlmExtraction:
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
    output = to_extraction_output(_llm(), model=MODEL)

    invoice = output.invoice
    assert output.model == MODEL
    assert invoice.currency == "MXN"
    assert invoice.total == Decimal("1160.00")
    assert invoice.line_items[0].unit_price == Decimal(500)
    assert invoice.issuer.tax_id_type == "RFC"
    assert output.field_confidence == {"total": 0.97}


def test_not_an_invoice() -> None:
    with pytest.raises(NotAnInvoiceError):
        to_extraction_output(_llm(is_invoice=False), model=MODEL)


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
    output = to_extraction_output(_llm(total=raw), model=MODEL)

    assert output.invoice.total == (Decimal(expected) if expected else None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2026-11-20", "2026-11-20"), ("2026-11-20T10:00:00", "2026-11-20"), ("20/11/2026", None)],
)
def test_dates_must_be_iso(raw: str, expected: str | None) -> None:
    invoice = to_extraction_output(_llm(issue_date=raw), model=MODEL).invoice

    assert (invoice.issue_date.isoformat() if invoice.issue_date else None) == expected


def test_invalid_codes_become_null_but_currency_is_kept_for_validation() -> None:
    invoice = to_extraction_output(
        _llm(country="Mexico", language="spanish", currency="pesos"), model=MODEL
    ).invoice

    assert invoice.country is None
    assert invoice.language is None
    assert invoice.currency == "PESOS"


def test_texts_are_collapsed_and_truncated() -> None:
    invoice = to_extraction_output(
        _llm(invoice_number="  A \n 1  ", notes="x" * 6000), model=MODEL
    ).invoice

    assert invoice.invoice_number == "A 1"
    assert invoice.notes is not None
    assert len(invoice.notes) == 5000


def test_empty_parties_become_defaults() -> None:
    invoice = to_extraction_output(_llm(issuer=LlmParty(), recipient=LlmParty()), model=MODEL)

    assert invoice.invoice.issuer.name is None
    assert invoice.invoice.recipient is None


def test_line_amount_is_computed_or_line_dropped() -> None:
    lines = [
        LlmLineItem(description="Sin importe", quantity="3", unit_price="10", discount="5"),
        LlmLineItem(description="Ilegible"),
        LlmLineItem(description="Normal", amount="7"),
    ]

    items = to_extraction_output(_llm(line_items=lines), model=MODEL).invoice.line_items

    assert [(item.description, item.amount) for item in items] == [
        ("Sin importe", Decimal(25)),
        ("Normal", Decimal(7)),
    ]


def test_tax_amount_is_computed_or_tax_dropped() -> None:
    taxes = [LlmTax(name="IVA", rate="16", base="99.99"), LlmTax(name="Ilegible")]

    result = to_extraction_output(_llm(taxes=taxes), model=MODEL).invoice.taxes

    assert [(tax.name, tax.amount) for tax in result] == [("IVA", Decimal("16.00"))]


def test_confidence_is_clamped_and_paths_filtered() -> None:
    confidence = [
        LlmFieldConfidence(field="issuer.tax_id", confidence=1.4),
        LlmFieldConfidence(field="line_items.0.amount", confidence=-0.2),
        LlmFieldConfidence(field="DROP TABLE", confidence=0.5),
    ]

    output = to_extraction_output(_llm(field_confidence=confidence), model=MODEL)

    assert output.field_confidence == {"issuer.tax_id": 1.0, "line_items.0.amount": 0.0}


# --- Cliente con SDK simulado -------------------------------------------------------------


def _response(
    text: str | None, finish: types.FinishReason = types.FinishReason.STOP
) -> types.GenerateContentResponse:
    parts = [types.Part(text=text)] if text is not None else []
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(content=types.Content(parts=parts, role="model"), finish_reason=finish)
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=1200, candidates_token_count=300
        ),
    )


class StubModels:
    """Simula `client.aio.models`: devuelve o lanza en orden lo indicado."""

    def __init__(self, *outcomes: types.GenerateContentResponse | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def generate_content(
        self, *, model: str, contents: Any, config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        self.calls.append({"model": model, "contents": contents, "config": config})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


PAGES = [PageImage(data=b"page-1", width=10, height=10), PageImage(b"page-2", 10, 10)]
OK = _response(_llm().model_dump_json())


def _extractor(models: StubModels, sleep: SleepRecorder | None = None) -> GeminiExtractor:
    return GeminiExtractor(models, model=MODEL, sleep=sleep or SleepRecorder())


def _quota_error(retry_delay: str | None = "31s") -> genai_errors.ClientError:
    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}]
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": details}}
    return genai_errors.ClientError(429, body)


def _server_error(code: int = 503) -> genai_errors.ServerError:
    return genai_errors.ServerError(code, {"error": {"code": code, "status": "UNAVAILABLE"}})


@pytest.mark.anyio
async def test_sends_all_pages_in_a_single_structured_call() -> None:
    models = StubModels(OK)

    output = await _extractor(models).extract(PAGES)

    assert output.invoice.total == Decimal("1160.00")
    assert len(models.calls) == 1
    call = models.calls[0]
    assert call["model"] == MODEL
    image_parts = [part for part in call["contents"] if isinstance(part, types.Part)]
    assert [part.inline_data.data for part in image_parts if part.inline_data] == [
        b"page-1",
        b"page-2",
    ]
    config = call["config"]
    assert config.system_instruction == SYSTEM_PROMPT
    assert config.response_mime_type == "application/json"
    assert config.response_schema is LlmExtraction
    assert config.temperature == 0


@pytest.mark.anyio
async def test_quota_error_is_not_retried_and_reports_retry_after() -> None:
    models = StubModels(_quota_error("30.5s"))

    with pytest.raises(AppError) as error:
        await _extractor(models).extract(PAGES)

    assert error.value.code is ErrorCode.LLM_QUOTA_EXCEEDED
    assert error.value.status == 503
    assert error.value.params == {"retry_after_s": 31}
    assert error.value.headers == {"Retry-After": "31"}
    assert len(models.calls) == 1


@pytest.mark.anyio
async def test_quota_error_without_retry_info() -> None:
    error_body = genai_errors.ClientError(429, {"error": {"code": 429}})

    with pytest.raises(AppError) as error:
        await _extractor(StubModels(error_body)).extract(PAGES)

    assert error.value.params == {}
    assert error.value.headers == {}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "transient",
    [_server_error(503), _server_error(500), httpx.ReadTimeout("t"), httpx.ConnectError("c")],
)
async def test_transient_error_is_retried_once(transient: Exception) -> None:
    models = StubModels(transient, OK)
    sleep = SleepRecorder()

    output = await _extractor(models, sleep).extract(PAGES)

    assert output.invoice.total == Decimal("1160.00")
    assert len(models.calls) == 2
    assert len(sleep.delays) == 1
    assert 1 <= sleep.delays[0] <= 2


@pytest.mark.anyio
@pytest.mark.parametrize(
    "transient", [_server_error(503), httpx.ReadTimeout("t")], ids=["server", "timeout"]
)
async def test_persistent_transient_error_is_unavailable(transient: Exception) -> None:
    models = StubModels(transient, transient)

    with pytest.raises(AppError) as error:
        await _extractor(models).extract(PAGES)

    assert error.value.code is ErrorCode.LLM_UNAVAILABLE
    assert len(models.calls) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("code", [401, 403, 404])
async def test_configuration_errors_are_unavailable_without_retry(code: int) -> None:
    models = StubModels(genai_errors.ClientError(code, {"error": {"code": code}}))

    with pytest.raises(AppError) as error:
        await _extractor(models).extract(PAGES)

    assert error.value.code is ErrorCode.LLM_UNAVAILABLE
    assert len(models.calls) == 1


@pytest.mark.anyio
async def test_bad_request_is_extraction_failure() -> None:
    models = StubModels(genai_errors.ClientError(400, {"error": {"code": 400}}))

    with pytest.raises(ExtractionFailedError):
        await _extractor(models).extract(PAGES)


@pytest.mark.anyio
async def test_unexpected_exceptions_propagate() -> None:
    with pytest.raises(RuntimeError):
        await _extractor(StubModels(RuntimeError("bug"))).extract(PAGES)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        _response(None),
        _response("{no es json"),
        _response('{"total": "1"}'),  # falta is_invoice
        _response(_llm().model_dump_json(), types.FinishReason.MAX_TOKENS),
        _response(_llm().model_dump_json(), types.FinishReason.SAFETY),
        types.GenerateContentResponse(candidates=[]),
    ],
    ids=["empty", "invalid-json", "schema", "max-tokens", "safety", "no-candidates"],
)
async def test_unusable_responses_are_extraction_failures(
    response: types.GenerateContentResponse,
) -> None:
    with pytest.raises(ExtractionFailedError):
        await _extractor(StubModels(response)).extract(PAGES)


@pytest.mark.anyio
async def test_not_an_invoice_response() -> None:
    response = _response(LlmExtraction(is_invoice=False).model_dump_json())

    with pytest.raises(NotAnInvoiceError):
        await _extractor(StubModels(response)).extract(PAGES)


@pytest.mark.anyio
async def test_logs_never_contain_document_data(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")

    await _extractor(StubModels(OK)).extract(PAGES)

    assert "tokens entrada=1200 salida=300" in caplog.text
    assert "Comercial XYZ" not in caplog.text
    assert "1160" not in caplog.text


# --- Selección del proveedor ----------------------------------------------------------------


def test_factory_builds_gemini_when_configured() -> None:
    settings = Settings(
        _env_file=None, llm_provider="gemini", gemini_api_key=SecretStr("clave"), gemini_model=" m "
    )

    extractor = get_extractor_factory(settings)(None)

    assert isinstance(extractor, GeminiExtractor)
    assert extractor.provider == "gemini"
    assert get_extractor_factory(settings)("gemini") is extractor  # cliente reutilizado


def test_factory_rejects_gemini_without_model() -> None:
    settings = Settings(_env_file=None, app_env="production", gemini_api_key=SecretStr("clave"))

    with pytest.raises(AppError) as error:
        get_extractor_factory(settings)("gemini")

    assert error.value.code is ErrorCode.PROVIDER_NOT_AVAILABLE


def test_missing_codes_are_null() -> None:
    invoice = to_extraction_output(_llm(country=None, language="", currency=""), model=MODEL)

    assert invoice.invoice.country is None
    assert invoice.invoice.language is None
    assert invoice.invoice.currency is None


@pytest.mark.anyio
async def test_retry_info_is_found_among_other_details() -> None:
    details = [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": []},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"},
    ]
    error_body = genai_errors.ClientError(429, {"error": {"code": 429, "details": details}})

    with pytest.raises(AppError) as error:
        await _extractor(StubModels(error_body)).extract(PAGES)

    assert error.value.params == {"retry_after_s": 7}
