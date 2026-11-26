import json
from pathlib import Path

import pytest

from app.scripts.export_openapi import OPENAPI_PATH, main, render_openapi


def test_committed_openapi_is_up_to_date() -> None:
    assert OPENAPI_PATH.read_text(encoding="utf-8") == render_openapi(), (
        "openapi.json desactualizado: uv run python -m app.scripts.export_openapi"
    )


def test_writes_openapi(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"

    assert main([], path=target) == 0

    schema = json.loads(target.read_text(encoding="utf-8"))
    assert "/api/v1/meta" in schema["paths"]


def test_check_passes_when_up_to_date(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"
    target.write_text(render_openapi(), encoding="utf-8")

    assert main(["--check"], path=target) == 0


@pytest.mark.parametrize("content", [None, "{}\n"])
def test_check_fails_when_missing_or_stale(
    tmp_path: Path, content: str | None, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "openapi.json"
    if content is not None:
        target.write_text(content, encoding="utf-8")

    assert main(["--check"], path=target) == 1
    assert "desactualizado" in capsys.readouterr().err


def test_problem_details_documented_as_problem_json() -> None:
    schema = json.loads(render_openapi())

    error_response = schema["paths"]["/api/v1/meta"]["get"]["responses"]["500"]

    assert error_response["content"] == {
        "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetail"}}
    }
    assert "ProblemDetail" in schema["components"]["schemas"]
