# Invoice Extractor API

[![CI](https://github.com/Yerireyes/invoice-extractor-api/actions/workflows/ci.yml/badge.svg)](https://github.com/Yerireyes/invoice-extractor-api/actions/workflows/ci.yml)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

API REST que recibe **fotos o PDFs de facturas de cualquier país**, extrae sus datos con un
LLM de visión, valida que los números cuadren y los exporta a **Excel** y **PDF**.

> Proyecto de portafolio. El frontend vive en un repositorio aparte (`invoice-extractor-web`).

## Características

- Extracción de facturas, tickets y notas de crédito de cualquier país, idioma y moneda.
- Modelo de factura genérico: montos como `Decimal` (nunca `float`), fechas ISO 8601,
  monedas ISO 4217.
- Validación aritmética (subtotales, impuestos, totales) con tolerancia de redondeo.
- Exportación a Excel (tablas, fórmulas por moneda, formato condicional) y a PDF.
- Proveedores de IA intercambiables: **Gemini** (nube, nivel gratuito), **Ollama** (local,
  privado) y un extractor **falso** determinista para desarrollo sin clave.
- Privacidad: los archivos se procesan en memoria y nunca se guardan ni se registran.

## Stack

| Propósito | Herramienta |
|---|---|
| Lenguaje | Python 3.14 |
| Paquetes / entorno | [uv](https://docs.astral.sh/uv/) |
| API | FastAPI + Uvicorn |
| Esquemas / config | Pydantic v2 + pydantic-settings |
| IA | `google-genai` (Gemini), `ollama` |
| Excel / PDF | openpyxl / ReportLab |
| Archivos de entrada | Pillow, pypdfium2 |
| Rate limit | slowapi |
| Calidad | Ruff, mypy (`strict`) |
| Tests | pytest, pytest-cov, httpx |

## Arquitectura

Monolito modular con puertos y adaptadores (ligero):

```
app/
├── main.py          # create_app(): routers, middlewares, handlers de error
├── core/            # configuración, errores, logging, health
├── invoices/        # esquemas, validación, extracción (puerto + adaptadores de IA)
├── export/          # exportadores Excel y PDF
└── shared/          # validación y normalización de archivos de entrada
```

Los servicios dependen de **puertos** (`Protocol`); el adaptador concreto se elige con
dependencias de FastAPI a partir de la configuración.

El contrato de la API está en [`docs/api-contract.md`](docs/api-contract.md).

## Puesta en marcha

### Requisitos

- [uv](https://docs.astral.sh/uv/getting-started/installation/) (instala Python 3.14 por ti)
- Docker (opcional)

### Local

```bash
cp .env.example .env          # sin GEMINI_API_KEY se usa el extractor falso
uv sync                       # crea .venv e instala dependencias
uv run uvicorn app.main:app --reload
```

- Health: <http://localhost:8000/api/v1/health>
- Documentación interactiva: <http://localhost:8000/docs>

### Docker

```bash
cp .env.example .env
docker compose up --build
```

La API queda en <http://localhost:8000> (el puerto se toma de `PORT`). La imagen es
multi-stage, corre con un usuario sin privilegios e incluye un `HEALTHCHECK`.

## Configuración

Todas las variables están documentadas en [`.env.example`](.env.example).

| Variable | Por defecto | Descripción |
|---|---|---|
| `APP_ENV` | `development` | `development` \| `production` |
| `PORT` | `8000` | Puerto HTTP (en Hugging Face Spaces: `7860`) |
| `LLM_PROVIDER` | `gemini` | `gemini` \| `ollama` \| `fake` |
| `GEMINI_API_KEY` | — | Clave de Google AI Studio (secreto) |
| `GEMINI_MODEL` | — | Modelo Flash con visión |
| `OLLAMA_ENABLED` | `false` | Habilita el proveedor local |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | URL de Ollama |
| `OLLAMA_MODEL` | `qwen2.5vl` | Modelo de visión de Ollama |
| `ALLOWED_ORIGINS` | `http://localhost:5173` | Orígenes CORS (separados por comas) |
| `RATE_LIMIT_EXTRACT` | `10/minute` | Límite de extracciones por IP |
| `LOG_LEVEL` | `INFO` | Nivel de logging |

## Desarrollo

```bash
uv run pytest                                  # tests (excluye los marcados como `live`)
uv run pytest --cov=app --cov-report=term-missing
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

## Integración continua

Cada PR a `main` (y cada push a `main`) ejecuta en GitHub Actions:

| Workflow | Qué verifica |
|---|---|
| [`ci.yml`](.github/workflows/ci.yml) | Ruff (lint + formato), mypy estricto, pytest con cobertura ≥ 80 % y `docker build` |
| [`branch-name.yml`](.github/workflows/branch-name.yml) | Ramas con el formato `<tipo>/<modulo>/api-<mod>-<NNNN>` |

Dependabot propone cada mes actualizaciones agrupadas de dependencias, actions e imágenes Docker.

## Licencia

[MIT](LICENSE)
