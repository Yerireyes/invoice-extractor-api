"""Genera `openapi.json` (versionado en git) o comprueba que está actualizado.

Uso:
    uv run python -m app.scripts.export_openapi          # regenera el archivo
    uv run python -m app.scripts.export_openapi --check  # falla si está desactualizado (CI)

`--check` compara el **contenido** JSON, no el texto: release-please actualiza `info.version` con
`JSON.stringify`, que reordena las claves numéricas (`"413"` antes que `"500"`) y escribe `0.0`
como `0`. El esquema es el mismo y el check no debe fallar en el PR de release.
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
        if not _same_schema(path, content):
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


def _same_schema(path: Path, content: str) -> bool:
    if not path.exists():
        return False
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return bool(current == json.loads(content))


if __name__ == "__main__":
    raise SystemExit(main())
