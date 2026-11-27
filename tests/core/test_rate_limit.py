from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import LLMProvider, Settings
from app.core.errors import PROBLEM_JSON, AppError, ErrorCode
from app.core.rate_limit import DailyQuota, IpRateLimiter
from app.invoices.ports import ExtractionOutput
from app.invoices.providers import get_extractor_factory
from app.invoices.schemas import Invoice
from app.main import create_app
from app.shared.files import PageImage
from tests.factories import image_bytes, invoice_payload

EXTRACT = "/api/v1/invoices/extract"


# --- Límite por IP --------------------------------------------------------------------------


def test_ip_limiter_allows_up_to_the_limit_per_key() -> None:
    limiter = IpRateLimiter("2/minute")

    limiter.hit("1.1.1.1")
    limiter.hit("1.1.1.1")
    limiter.hit("2.2.2.2")  # otra IP tiene su propio contador

    with pytest.raises(AppError) as error:
        limiter.hit("1.1.1.1")

    assert error.value.code is ErrorCode.RATE_LIMITED
    assert error.value.status == 429
    retry_after = error.value.params["retry_after_s"]
    assert 1 <= retry_after <= 60
    assert error.value.headers == {"Retry-After": str(retry_after)}


def test_retry_after_is_at_least_one_second() -> None:
    limiter = IpRateLimiter("1/second", clock=lambda: 10**12)  # reloj "en el futuro"
    limiter.hit("k")

    with pytest.raises(AppError) as error:
        limiter.hit("k")

    assert error.value.params == {"retry_after_s": 1}


@pytest.mark.parametrize("value", ["10/minute", "100 per hour", "5/second"])
def test_valid_rate_limit_settings(value: str) -> None:
    assert Settings(_env_file=None, rate_limit_extract=value).rate_limit_extract == value


@pytest.mark.parametrize("value", ["", "diez por minuto", "10/fortnight"])
def test_invalid_rate_limit_settings(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rate_limit_extract=value)


# --- Cuota diaria -------------------------------------------------------------------------------


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


NOON_PACIFIC = datetime(2026, 11, 26, 20, 0, tzinfo=UTC)  # 12:00 PST (UTC-8)


def test_quota_is_consumed_atomically() -> None:
    quota = DailyQuota(5, now=Clock(NOON_PACIFIC))

    quota.consume(3)
    with pytest.raises(AppError) as error:
        quota.consume(3)  # no caben: no se reserva ninguno
    quota.consume(2)

    assert quota.used == 5
    assert error.value.code is ErrorCode.LLM_QUOTA_EXCEEDED
    assert error.value.status == 503
    assert error.value.params == {"retry_after_s": 12 * 3600}
    assert error.value.headers == {"Retry-After": str(12 * 3600)}


def test_quota_resets_at_pacific_midnight() -> None:
    clock = Clock(NOON_PACIFIC)
    quota = DailyQuota(1, now=clock)
    quota.consume(1)

    clock.now = datetime(2026, 11, 27, 7, 59, tzinfo=UTC)  # 23:59 PST, mismo día
    with pytest.raises(AppError):
        quota.consume(1)

    clock.now = datetime(2026, 11, 27, 8, 0, tzinfo=UTC)  # 00:00 PST
    quota.consume(1)
    assert quota.used == 1


def test_retry_after_respects_daylight_saving_change() -> None:
    # 2026-03-08: en el Pacífico se adelanta el reloj (el día dura 23 h).
    clock = Clock(datetime(2026, 3, 8, 8, 0, tzinfo=UTC))  # 00:00 PST
    quota = DailyQuota(1, now=clock)
    quota.consume(1)

    with pytest.raises(AppError) as error:
        quota.consume(1)

    assert error.value.params["retry_after_s"] == 23 * 3600


def test_zero_limit_disables_the_quota() -> None:
    quota = DailyQuota(0)

    quota.consume(10_000)

    assert quota.used == 0


def test_default_daily_limit_leaves_margin_below_google_quota() -> None:
    assert Settings(_env_file=None).daily_document_limit == 450


# --- Integración con /extract ---------------------------------------------------------------------


class GeminiLikeExtractor:
    """Extractor "medido" (como Gemini) que no llama a ninguna red."""

    @property
    def provider(self) -> LLMProvider:
        return "gemini"

    async def extract(self, pages: Sequence[PageImage]) -> ExtractionOutput:
        assert pages
        return ExtractionOutput(invoice=Invoice.model_validate(invoice_payload()), model="m")


def _files(count: int) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (f"f{n}.png", image_bytes("PNG"), "image/png")) for n in range(count)]


def test_extract_is_rate_limited_per_ip() -> None:
    app = create_app(Settings(_env_file=None, rate_limit_extract="2/minute"))

    with (
        TestClient(app, client=("10.0.0.1", 1)) as first,
        TestClient(app, client=("10.0.0.2", 1)) as second,
    ):
        statuses = [first.post(EXTRACT, files=_files(1)).status_code for _ in range(3)]
        other_ip = second.post(EXTRACT, files=_files(1))
        validate = first.post("/api/v1/invoices/validate", json={"invoice": invoice_payload()})
        limited = first.post(EXTRACT, files=_files(1))

    assert statuses == [200, 200, 429]
    assert other_ip.status_code == 200
    assert validate.status_code == 200  # el límite solo aplica a /extract
    assert limited.headers["content-type"] == PROBLEM_JSON
    body = limited.json()
    assert body["code"] == "RATE_LIMITED"
    assert body["params"]["retry_after_s"] >= 1
    assert limited.headers["retry-after"] == str(body["params"]["retry_after_s"])


def test_daily_quota_counts_documents_for_metered_providers() -> None:
    app = create_app(Settings(_env_file=None, daily_document_limit=3))
    app.dependency_overrides[get_extractor_factory] = lambda: lambda _: GeminiLikeExtractor()

    with TestClient(app) as client:
        first = client.post(EXTRACT, files=_files(2))
        too_many = client.post(EXTRACT, files=_files(2))
        last = client.post(EXTRACT, files=_files(1))
        exhausted = client.post(EXTRACT, files=_files(1))

    assert first.status_code == 200
    assert too_many.status_code == 503
    assert too_many.json()["code"] == "LLM_QUOTA_EXCEEDED"
    assert too_many.json()["params"]["retry_after_s"] > 0
    assert "retry-after" in too_many.headers
    assert last.status_code == 200
    assert exhausted.status_code == 503
    assert app.state.daily_quota.used == 3


def test_fake_provider_does_not_consume_the_daily_quota() -> None:
    app = create_app(Settings(_env_file=None, daily_document_limit=1))

    with TestClient(app) as client:
        responses = [client.post(EXTRACT, files=_files(2)) for _ in range(2)]

    assert [response.status_code for response in responses] == [200, 200]
    assert app.state.daily_quota.used == 0


def test_rate_limit_resets_with_each_app() -> None:
    """Cada `create_app` tiene su propio estado (los tests no se contaminan entre sí)."""
    settings = Settings(_env_file=None, rate_limit_extract="1/minute")
    for _ in range(2):
        with TestClient(create_app(settings)) as client:
            assert client.post(EXTRACT, files=_files(1)).status_code == 200


def test_quota_window_is_tracked_in_pacific_time() -> None:
    clock = Clock(datetime(2026, 11, 27, 7, 0, tzinfo=UTC))  # 23:00 PST del día 26
    quota = DailyQuota(1, now=clock)
    quota.consume(1)
    clock.now += timedelta(hours=1)  # 00:00 PST del 27

    quota.consume(1)

    assert quota.used == 1
