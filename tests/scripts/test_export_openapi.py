import json
from pathlib import Path

import pytest

from app.scripts.export_openapi import OPENAPI_PATH, main, render_openapi


def test_committed_openapi_is_up_to_date() -> None:
    committed = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    assert committed == json.loads(render_openapi()), (
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


@pytest.mark.parametrize("content", [None, "{}\n", "no es json"])
def test_check_fails_when_missing_or_stale(
    tmp_path: Path, content: str | None, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "openapi.json"
    if content is not None:
        target.write_text(content, encoding="utf-8")

    assert main(["--check"], path=target) == 1
    assert "desactualizado" in capsys.readouterr().err


def test_check_ignores_release_please_reformatting(tmp_path: Path) -> None:
    """Mismo esquema serializado como lo hace `JSON.stringify` (orden de claves, `0.0` → `0`)."""

    def js_like(value: object) -> object:
        if isinstance(value, dict):
            numeric = sorted((key for key in value if key.isdigit()), key=int)
            ordered = numeric + [key for key in value if not key.isdigit()]
            return {key: js_like(value[key]) for key in ordered}
        if isinstance(value, list):
            return [js_like(item) for item in value]
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    reformatted = json.dumps(js_like(json.loads(render_openapi())), indent=2)
    assert reformatted != render_openapi()
    target = tmp_path / "openapi.json"
    target.write_text(reformatted, encoding="utf-8")

    assert main(["--check"], path=target) == 0


def test_check_fails_when_version_differs(tmp_path: Path) -> None:
    schema = json.loads(render_openapi())
    schema["info"]["version"] = "99.0.0"
    target = tmp_path / "openapi.json"
    target.write_text(json.dumps(schema), encoding="utf-8")

    assert main(["--check"], path=target) == 1


def test_problem_details_documented_as_problem_json() -> None:
    schema = json.loads(render_openapi())

    error_response = schema["paths"]["/api/v1/meta"]["get"]["responses"]["500"]

    assert error_response["content"] == {
        "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
    }
    assert "ProblemDetails" in schema["components"]["schemas"]
