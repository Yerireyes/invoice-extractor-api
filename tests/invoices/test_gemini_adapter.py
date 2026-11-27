from decimal import Decimal
from typing import Any

import httpx
import httpx2
import pytest
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.invoices.adapters.gemini import GeminiExtractor, http_options
from app.invoices.adapters.llm_output import LlmExtraction
from app.invoices.adapters.prompts import SYSTEM_PROMPT
from app.invoices.ports import ExtractionFailedError, NotAnInvoiceError
from app.invoices.providers import get_extractor_factory
from app.shared.files import PageImage
from tests.invoices.test_llm_output import llm_extraction

MODEL = "gemini-test"


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
OK = _response(llm_extraction().model_dump_json())


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
    [
        _server_error(503),
        _server_error(500),
        httpx.ReadTimeout("t"),
        httpx.ConnectError("c"),
        httpx2.ReadTimeout("t"),
        httpx2.ConnectError("c"),
    ],
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
    "transient",
    [_server_error(503), httpx.ReadTimeout("t"), httpx2.ReadTimeout("t")],
    ids=["server", "httpx-timeout", "httpx2-timeout"],
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
        _response(llm_extraction().model_dump_json(), types.FinishReason.MAX_TOKENS),
        _response(llm_extraction().model_dump_json(), types.FinishReason.SAFETY),
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


def test_sdk_retries_are_disabled_and_timeout_is_set() -> None:
    options = http_options(timeout_s=45)

    assert options.timeout == 45_000
    assert options.retry_options is not None
    assert options.retry_options.attempts == 1
