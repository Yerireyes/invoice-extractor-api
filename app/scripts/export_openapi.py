"""Genera `openapi.json` (versionado en git) o comprueba que está actualizado.

Uso:
    uv run python -m app.scripts.export_openapi          # regenera el archivo
    uv run python -m app.scripts.export_openapi --check  # falla si está desactualizado (CI)
"""

import argparse
import json
import sys
from pathlib import Path

from app.core.config import Settings
from app.main import create_app

OPENAPI_PATH = Path(__file__).resolve().parents[2] / "openapi.json"


def render_openapi() -> str:
    """Esquema OpenAPI serializado de forma determinista."""
    schema = create_app(Settings(_env_file=None)).openapi()
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None, path: Path = OPENAPI_PATH) -> int:
    parser = argparse.ArgumentParser(description="Genera o verifica openapi.json")
    parser.add_argument("--check", action="store_true", help="no escribe; falla si hay cambios")
    args = parser.parse_args(argv)

    content = render_openapi()
    if args.check:
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current != content:
            print(
                f"{path.name} está desactualizado. "
                "Ejecuta: uv run python -m app.scripts.export_openapi",
                file=sys.stderr,
            )
            return 1
        print(f"{path.name} está actualizado.")
        return 0

    path.write_text(content, encoding="utf-8", newline="\n")
    print(f"{path.name} generado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
