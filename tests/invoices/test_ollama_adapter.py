import asyncio
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

import httpx
import ollama
import pytest

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.invoices.adapters.llm_output import LlmExtraction
from app.invoices.adapters.ollama import (
    CONTEXT_TOKENS,
    LLM_SCHEMA,
    OllamaExtractor,
    OllamaStatus,
    new_client,
)
from app.invoices.adapters.prompts import SYSTEM_PROMPT, USER_PROMPT
from app.invoices.ports import ExtractionFailedError, NotAnInvoiceError
from app.invoices.providers import get_extractor_factory, get_ollama_available
from app.shared.files import PageImage
from tests.invoices.test_llm_output import llm_extraction

MODEL = "qwen3-vl:8b"
PAGES = [PageImage(data=b"page-1", width=10, height=10), PageImage(b"page-2", 10, 10)]


def _response(content: str, done_reason: str | None = "stop") -> ollama.ChatResponse:
    return ollama.ChatResponse(
        model=MODEL,
        message=ollama.Message(role="assistant", content=content),
        done=True,
        done_reason=done_reason,
        prompt_eval_count=1500,
        eval_count=400,
    )


OK = _response(llm_extraction().model_dump_json())


class StubOllama:
    """Simula `ollama.AsyncClient`: devuelve o lanza en orden lo indicado."""

    def __init__(
        self,
        *outcomes: ollama.ChatResponse | Exception,
        models: Sequence[str] = (),
        list_error: Exception | None = None,
    ) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []
        self.models = models
        self.list_error = list_error
        self.list_calls = 0

    async def chat(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        format: Mapping[str, Any],  # noqa: A002
        options: Mapping[str, Any],
    ) -> ollama.ChatResponse:
        self.calls.append(
            {"model": model, "messages": messages, "format": format, "options": options}
        )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def list(self) -> ollama.ListResponse:
        self.list_calls += 1
        if self.list_error:
            raise self.list_error
        return ollama.ListResponse(
            models=[ollama.ListResponse.Model(model=name) for name in self.models]
        )


class SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _extractor(client: StubOllama, sleep: SleepRecorder | None = None) -> OllamaExtractor:
    return OllamaExtractor(client, model=MODEL, sleep=sleep or SleepRecorder())


# --- Extracción --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sends_all_pages_in_a_single_structured_call() -> None:
    client = StubOllama(OK)

    output = await _extractor(client).extract(PAGES)

    assert output.model == MODEL
    assert output.invoice.total == Decimal("1160.00")
    assert output.invoice.currency == "MXN"
    assert output.field_confidence == {"total": 0.97}
    (call,) = client.calls
    assert call["model"] == MODEL
    system, user = call["messages"]
    assert system == {"role": "system", "content": SYSTEM_PROMPT}
    assert user == {"role": "user", "content": USER_PROMPT, "images": [b"page-1", b"page-2"]}
    assert call["format"] == LlmExtraction.model_json_schema() == LLM_SCHEMA
    assert call["options"]["temperature"] == 0
    assert call["options"]["num_ctx"] == CONTEXT_TOKENS


@pytest.mark.anyio
async def test_provider_is_ollama() -> None:
    assert _extractor(StubOllama()).provider == "ollama"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "transient",
    [
        ollama.ResponseError("server busy", 503),
        ollama.ResponseError("boom", 500),
        ConnectionError("Failed to connect to Ollama"),
        httpx.ReadError("reset"),
    ],
    ids=["503", "500", "connection-refused", "network"],
)
async def test_transient_error_is_retried_once(transient: Exception) -> None:
    client, sleep = StubOllama(transient, OK), SleepRecorder()

    output = await _extractor(client, sleep).extract(PAGES)

    assert output.invoice.total == Decimal("1160.00")
    assert len(client.calls) == 2
    assert len(sleep.delays) == 1
    assert 1 <= sleep.delays[0] <= 2


@pytest.mark.anyio
async def test_persistent_transient_error_is_unavailable() -> None:
    error = ConnectionError("Failed to connect to Ollama")
    client = StubOllama(error, error)

    with pytest.raises(AppError) as exc:
        await _extractor(client).extract(PAGES)

    assert exc.value.code is ErrorCode.LLM_UNAVAILABLE
    assert len(client.calls) == 2


@pytest.mark.anyio
async def test_timeout_is_unavailable_without_retry() -> None:
    client = StubOllama(httpx.ReadTimeout("slow"))

    with pytest.raises(AppError) as exc:
        await _extractor(client).extract(PAGES)

    assert exc.value.code is ErrorCode.LLM_UNAVAILABLE
    assert len(client.calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("code", [404, 401])
async def test_model_missing_or_config_errors_are_unavailable(code: int) -> None:
    client = StubOllama(ollama.ResponseError('{"error": "model not found"}', code))

    with pytest.raises(AppError) as exc:
        await _extractor(client).extract(PAGES)

    assert exc.value.code is ErrorCode.LLM_UNAVAILABLE
    assert len(client.calls) == 1


@pytest.mark.anyio
async def test_bad_request_is_extraction_failure() -> None:
    with pytest.raises(ExtractionFailedError):
        await _extractor(StubOllama(ollama.ResponseError("bad image", 400))).extract(PAGES)


@pytest.mark.anyio
async def test_unexpected_exceptions_propagate() -> None:
    with pytest.raises(RuntimeError):
        await _extractor(StubOllama(RuntimeError("bug"))).extract(PAGES)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        _response(llm_extraction().model_dump_json(), done_reason="length"),
        _response(""),
        _response("no es json"),
        _response('{"total": "1"}'),  # falta `is_invoice`
    ],
    ids=["truncated", "empty", "not-json", "schema-mismatch"],
)
async def test_unusable_responses_are_extraction_failures(response: ollama.ChatResponse) -> None:
    with pytest.raises(ExtractionFailedError):
        await _extractor(StubOllama(response)).extract(PAGES)


@pytest.mark.anyio
async def test_not_an_invoice_response() -> None:
    response = _response(LlmExtraction(is_invoice=False).model_dump_json())

    with pytest.raises(NotAnInvoiceError):
        await _extractor(StubOllama(response)).extract(PAGES)


@pytest.mark.anyio
async def test_logs_never_contain_document_data(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")

    await _extractor(StubOllama(OK)).extract(PAGES)

    assert "tokens entrada=1500 salida=400" in caplog.text
    assert "Comercial XYZ" not in caplog.text
    assert "1160" not in caplog.text


# --- Disponibilidad (para /meta) --------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("configured", "installed", "expected"),
    [
        ("qwen3-vl:8b", ["llama3:latest", "qwen3-vl:8b"], True),
        ("qwen2.5vl", ["qwen2.5vl:latest"], True),  # sin etiqueta = `:latest`
        ("qwen3-vl:8b", ["qwen3-vl:4b"], False),  # otra variante del modelo
        ("qwen3-vl:8b", [], False),
    ],
)
async def test_status_requires_the_model_to_be_pulled(
    configured: str, installed: list[str], expected: bool
) -> None:
    status = OllamaStatus(StubOllama(models=installed), model=configured)

    assert await status.available() is expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [ConnectionError("down"), httpx.ConnectTimeout("slow"), ollama.ResponseError("x", 500)],
)
async def test_status_is_unavailable_when_ollama_fails(error: Exception) -> None:
    status = OllamaStatus(StubOllama(list_error=error), model=MODEL)

    assert await status.available() is False


@pytest.mark.anyio
async def test_status_gives_up_after_its_timeout() -> None:
    class Hanging(StubOllama):
        async def list(self) -> ollama.ListResponse:
            await asyncio.sleep(10)
            raise AssertionError  # pragma: no cover

    status = OllamaStatus(Hanging(), model=MODEL, timeout_s=0.01)

    assert await status.available() is False


@pytest.mark.anyio
async def test_status_is_cached() -> None:
    client, clock = StubOllama(models=[MODEL]), Clock()
    status = OllamaStatus(client, model=MODEL, ttl_s=30, clock=clock)

    assert await status.available() is True
    client.models = []
    clock.now += 29
    assert await status.available() is True  # aún en caché
    assert client.list_calls == 1
    clock.now += 2
    assert await status.available() is False
    assert client.list_calls == 2


@pytest.mark.anyio
async def test_disabled_ollama_is_never_probed() -> None:
    settings = Settings(_env_file=None, ollama_enabled=False)

    assert await get_ollama_available(settings) is False


@pytest.mark.anyio
async def test_enabled_ollama_unreachable_is_unavailable() -> None:
    settings = Settings(_env_file=None, ollama_enabled=True, ollama_base_url="http://127.0.0.1:9")

    assert await get_ollama_available(settings) is False


# --- Selección del proveedor ----------------------------------------------------------------


def test_factory_builds_ollama_when_enabled() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        llm_provider="ollama",
        ollama_enabled=True,
        ollama_model=" qwen3-vl:8b ",
    )

    extractor = get_extractor_factory(settings)(None)

    assert isinstance(extractor, OllamaExtractor)
    assert extractor.provider == "ollama"
    assert get_extractor_factory(settings)("ollama") is extractor  # cliente reutilizado


def test_factory_rejects_disabled_ollama() -> None:
    settings = Settings(_env_file=None, ollama_enabled=False)

    with pytest.raises(AppError) as error:
        get_extractor_factory(settings)("ollama")

    assert error.value.code is ErrorCode.PROVIDER_NOT_AVAILABLE
    assert error.value.params == {"provider": "ollama"}


def test_new_client_points_to_configured_host() -> None:
    client = new_client("http://ollama:11434", timeout_s=300)

    assert isinstance(client, ollama.AsyncClient)
