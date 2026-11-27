from collections.abc import Iterator, Sequence
from datetime import date
from typing import Any

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from starlette.formparsers import MultiPartParser

from app.core.config import LLMProvider, Settings
from app.core.errors import PROBLEM_JSON, AppError, ErrorCode
from app.invoices.api import extract_body_limit, get_today
from app.invoices.ports import ExtractionOutput
from app.invoices.providers import get_extractor_factory
from app.main import create_app
from app.shared.files import PageImage
from tests.factories import image_bytes, pdf_bytes

URL = "/api/v1/invoices/extract"
MB = 1024 * 1024

type Upload = tuple[str, tuple[str, bytes, str]]


def _client(settings: Settings) -> TestClient:
    app = create_app(settings)
    app.dependency_overrides[get_today] = lambda: date(2026, 11, 25)
    return TestClient(app)


@pytest.fixture
def api(settings: Settings) -> Iterator[TestClient]:
    with _client(settings) as client:
        yield client


def _upload(data: bytes, name: str = "factura.png", mime: str = "image/png") -> Upload:
    return ("files", (name, data, mime))


def _post(client: TestClient, files: list[Upload], **data: str) -> Any:
    return client.post(URL, files=files, data=data)


# --- Éxito ---------------------------------------------------------------------------------


def test_extracts_image_with_fake_provider(api: TestClient) -> None:
    response = _post(api, [_upload(image_bytes("PNG"))])

    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {"total": 1, "succeeded": 1, "failed": 0}
    result = body["results"][0]
    assert result["status"] == "success"
    assert result["file_name"] == "factura.png"
    assert result["meta"]["provider"] == "fake"
    assert result["meta"]["pages"] == 1
    assert isinstance(result["invoice"]["total"], str)
    assert result["validation"]["status"] in {"valid", "warnings", "invalid"}


def test_multipage_pdf(api: TestClient) -> None:
    response = _post(api, [_upload(pdf_bytes(pages=2), "f.pdf", "application/pdf")])

    assert response.json()["results"][0]["meta"]["pages"] == 2


def test_low_confidence_fields_are_reported_as_info(api: TestClient) -> None:
    results = [
        _post(api, [_upload(image_bytes("PNG", size=(10 + n, 10)))]).json()["results"][0]
        for n in range(15)
    ]

    issues = [issue for result in results for issue in result["validation"]["issues"]]
    low = [issue for issue in issues if issue["code"] == "LOW_CONFIDENCE"]
    assert low
    assert all(issue["severity"] == "info" for issue in low)
    assert all(issue["params"]["confidence"] < 0.7 for issue in low)


def test_declared_mime_type_is_ignored(api: TestClient) -> None:
    gif_disguised = _upload(image_bytes("GIF"), "foto.png", "image/png")

    result = _post(api, [gif_disguised]).json()["results"][0]

    assert result["status"] == "error"
    assert result["error"] == {
        "code": "UNSUPPORTED_FILE_TYPE",
        "params": {"mime_type": "image/gif"},
    }


def test_partial_success(api: TestClient) -> None:
    files = [
        _upload(image_bytes("JPEG"), "ok.jpg", "image/jpeg"),
        _upload(b"no soy una imagen", "roto.png"),
        _upload(pdf_bytes(pages=6), "largo.pdf", "application/pdf"),
    ]

    body = _post(api, files).json()

    assert body["summary"] == {"total": 3, "succeeded": 1, "failed": 2}
    assert [result["status"] for result in body["results"]] == ["success", "error", "error"]
    assert body["results"][2]["error"] == {
        "code": "PDF_TOO_MANY_PAGES",
        "params": {"max_pages": 5, "pages": 6},
    }


def test_explicit_fake_provider_in_development(api: TestClient) -> None:
    response = _post(api, [_upload(image_bytes("PNG"))], provider="fake")

    assert response.status_code == 200


# --- Errores de toda la petición -----------------------------------------------------------------


def test_no_files(api: TestClient) -> None:
    response = api.post(URL, data={"provider": "fake"})

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "NO_FILES"


def test_too_many_files(api: TestClient) -> None:
    files = [_upload(image_bytes("PNG"), f"f{n}.png") for n in range(11)]

    response = _post(api, files)

    assert response.status_code == 422
    assert response.json()["code"] == "TOO_MANY_FILES"
    assert response.json()["params"] == {"max_files": 10}


def test_unavailable_provider(api: TestClient) -> None:
    response = _post(api, [_upload(image_bytes("PNG"))], provider="gemini")

    assert response.status_code == 422
    assert response.json()["code"] == "PROVIDER_NOT_AVAILABLE"
    assert response.json()["params"] == {"provider": "gemini"}


def test_fake_provider_is_never_available_in_production() -> None:
    settings = Settings(_env_file=None, app_env="production")

    with _client(settings) as client:
        response = _post(client, [_upload(image_bytes("PNG"))], provider="fake")

    assert response.json()["code"] == "PROVIDER_NOT_AVAILABLE"


def test_unknown_provider_is_validation_error(api: TestClient) -> None:
    response = _post(api, [_upload(image_bytes("PNG"))], provider="openai")

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


# --- Tamaños y privacidad ------------------------------------------------------------------------


def test_file_over_size_limit_is_per_file_error() -> None:
    settings = Settings(_env_file=None, max_file_size_mb=1)

    with _client(settings) as client:
        files = [_upload(b"x" * (MB + 1), "grande.png"), _upload(image_bytes("PNG"))]
        body = _post(client, files).json()

    assert body["results"][0]["error"] == {"code": "FILE_TOO_LARGE", "params": {"max_mb": 1}}
    assert body["results"][1]["status"] == "success"


def test_body_over_limit_is_request_too_large() -> None:
    settings = Settings(_env_file=None, max_files_per_request=1, max_file_size_mb=1)
    assert extract_body_limit(settings) == 2 * MB

    with _client(settings) as client:
        response = _post(client, [_upload(b"x" * (3 * MB))])

    assert response.status_code == 413
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "REQUEST_TOO_LARGE"


def test_json_body_over_limit_on_validate(api: TestClient) -> None:
    response = api.post(
        "/api/v1/invoices/validate",
        content=b'{"invoice": {"notes": "' + b"x" * (11 * MB) + b'"}}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["code"] == "REQUEST_TOO_LARGE"


def test_uploads_are_kept_in_memory(settings: Settings) -> None:
    """Un archivo de 5 MB (> 1 MB, el umbral por defecto de Starlette) no se vuelca a disco."""
    app = create_app(settings)
    assert MultiPartParser.spool_max_size >= extract_body_limit(settings)

    @app.post("/spool-probe")
    async def probe(file: UploadFile) -> dict[str, bool]:
        return {"on_disk": bool(getattr(file.file, "_rolled", True))}

    with TestClient(app) as client:
        response = client.post("/spool-probe", files={"file": ("f.bin", b"x" * (5 * MB))})

    assert response.json() == {"on_disk": False}


def test_request_too_large_keeps_cors_headers() -> None:
    settings = Settings(_env_file=None, max_files_per_request=1, max_file_size_mb=1)
    origin = "http://localhost:5173"

    with _client(settings) as client:
        response = client.post(URL, files=[_upload(b"x" * (3 * MB))], headers={"Origin": origin})

    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == origin


def test_streamed_body_without_content_length_is_limited(api: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'{"invoice": {"notes": "'
        for _ in range(11):
            yield b"x" * MB
        yield b'"}}'

    response = api.post(
        "/api/v1/invoices/validate",
        content=chunks(),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == "REQUEST_TOO_LARGE"


def test_other_responses_pass_through_middleware(api: TestClient) -> None:
    response = api.get("/api/v1/health")

    assert response.json() == {"status": "ok"}


class _FailingExtractor:
    def __init__(self, error: AppError) -> None:
        self.error = error

    @property
    def provider(self) -> LLMProvider:
        return "gemini"

    async def extract(self, pages: Sequence[PageImage]) -> ExtractionOutput:  # noqa: ARG002
        raise self.error


@pytest.mark.parametrize(
    ("error", "params", "retry_after"),
    [
        (AppError(ErrorCode.LLM_UNAVAILABLE), {}, None),
        (
            AppError(
                ErrorCode.LLM_QUOTA_EXCEEDED,
                params={"retry_after_s": 30},
                headers={"Retry-After": "30"},
            ),
            {"retry_after_s": 30},
            "30",
        ),
    ],
)
def test_provider_errors_abort_with_503(
    settings: Settings, error: AppError, params: dict[str, int], retry_after: str | None
) -> None:
    app = create_app(settings)
    app.dependency_overrides[get_extractor_factory] = lambda: lambda _: _FailingExtractor(error)

    with TestClient(app) as client:
        response = _post(client, [_upload(image_bytes("PNG")), _upload(image_bytes("JPEG"))])

    assert response.status_code == 503
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["code"] == error.code
    assert response.json()["params"] == params
    assert response.headers.get("retry-after") == retry_after
