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
- Privacidad: los archivos se procesan en memoria (ni siquiera los temporales de subida tocan el
  disco), se reescriben sin metadatos EXIF/GPS y su contenido nunca se registra en los logs.

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
| Rate limit | `limits` |
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

## API

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/v1/health` | Comprobación de salud |
| `GET` | `/api/v1/meta` | Límites, formatos aceptados, proveedores de IA e idiomas de exportación |
| `POST` | `/api/v1/invoices/extract` | Sube 1..10 imágenes o PDF y devuelve las facturas extraídas y validadas (éxito parcial por archivo) |
| `POST` | `/api/v1/invoices/validate` | Revalida una factura editada: cuadre aritmético (±0.02), campos obligatorios, moneda ISO 4217 y fechas |
| `POST` | `/api/v1/exports/excel` | Exporta facturas a `.xlsx`: 5 hojas como Tablas de Excel, totales por moneda con fórmulas, alertas traducidas (es/en) |
| `POST` | `/api/v1/exports/pdf` | Exporta facturas a `.pdf`: una factura por página (conceptos, impuestos, totales y alertas), fuente Unicode incrustada (es/en) |

Los errores siguen [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) (`application/problem+json`)
con un `code` estable y `params`, para que el cliente traduzca los mensajes:

```json
{ "type": "about:blank", "title": "Not Found", "status": 404, "code": "NOT_FOUND", "detail": null, "params": {} }
```

- Contrato (fuente de verdad): [`docs/api-contract.md`](docs/api-contract.md)
- Esquema OpenAPI versionado: [`openapi.json`](openapi.json) — el CI falla si está desactualizado.

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

## Proveedores de IA

| Proveedor | Cuándo | Notas |
|---|---|---|
| `gemini` | `GEMINI_API_KEY` y `GEMINI_MODEL` configurados | Nivel gratuito de Google AI Studio. Todas las páginas de un documento van en una sola llamada con salida estructurada. |
| `ollama` | `OLLAMA_ENABLED=true` | Local y privado (próximamente). |
| `fake` | Desarrollo sin clave | Determinista, sin red: devuelve facturas de ejemplo. Nunca disponible en producción. |

**Gemini**: crea una clave gratuita en [Google AI Studio](https://aistudio.google.com/apikey) y ponla en `.env`
junto con el modelo. A noviembre de 2026 se recomienda `gemini-3.5-flash-lite`: en el nivel gratuito
ofrece 15 peticiones/min y 500/día (frente a 5/min y 20/día de `gemini-3.8-flash`, que además se
satura con frecuencia) y extrae correctamente las facturas de prueba en ~4 s. La lista vigente está en la
[documentación de modelos](https://ai.google.dev/gemini-api/docs/models) y los límites de tu proyecto
en [AI Studio](https://aistudio.google.com/rate-limit). El adaptador usa un esquema
propio para el LLM (solo strings) y lo convierte al dominio con `Decimal`, normalizando formatos como
`1.234,50`. Se reintenta una vez ante errores transitorios (5xx, timeout); una cuota agotada responde
503 `LLM_QUOTA_EXCEEDED` con `Retry-After` cuando Google lo indica.

## Calidad de extracción

`uv run python -m app.scripts.eval` evalúa el proveedor configurado sobre **13 documentos sintéticos**
([`tests/eval/`](tests/eval/)): 8 países, 7 monedas (MXN, EUR, USD, BRL, ARS, COP, GBP, JPY) y 5 idiomas,
con facturas en PDF, una foto de móvil simulada (rotada, desenfocada y con sombra), tickets estrechos,
una factura de 2 páginas, una nota de crédito con importes negativos, una retención de IRPF y una
imagen que **no** es una factura. Compara 18 campos por documento: textos normalizados, importes
exactos, fechas, número e importes de líneas e impuestos; inventar un valor donde se espera `null`
cuenta como error. No corre en el CI porque consume cuota (una petición por documento).

Resultados con `gemini-3.5-flash-lite` (2026-11-26):

| Métrica | Resultado |
|---|---|
| Precisión global | **99.5 %** (216/217 campos) |
| Importes (subtotal, total, impuestos, líneas) | 100 % |
| Fechas (formatos `dd/mm/aaaa`, `mm/dd/aaaa`, `dd.mm.aaaa`, `02 May 2026`...) | 100 % |
| Detección de documentos que no son facturas | 100 % |
| ID fiscal del emisor | 92 % (falló en la foto degradada) |
| Latencia mediana | 4.5 s por documento |

> Son documentos sintéticos y nítidos: el resultado es una cota superior. Fotos reales con poca luz,
> arrugas o escritura a mano darán peores cifras; por eso la API devuelve `field_confidence` y la
> validación aritmética marca lo que no cuadra.

Los documentos se regeneran con `uv run python -m tests.eval.generate` (los importes esperados se
calculan con `Decimal` y un test verifica que todos cuadran).

## Protección de la cuota

La cuota gratuita de Gemini es pequeña (15 peticiones/min y 500/día en `gemini-3.5-flash-lite`), así
que `/invoices/extract` tiene dos límites, ambos en memoria (pensados para una sola instancia):

| Límite | Por defecto | Al superarlo |
|---|---|---|
| Peticiones por IP real (detrás de proxy, vía `X-Forwarded-For`) | 10 por minuto | 429 `RATE_LIMITED` + `Retry-After` |
| Documentos enviados a Gemini al día (global) | 450 (reinicio a medianoche del Pacífico, como Google) | 503 `LLM_QUOTA_EXCEEDED` + `Retry-After` |

Una petición con varios archivos reserva todos sus documentos de golpe: o caben todos o se rechaza
entera. El extractor `fake` no consume cuota.

## Configuración

Todas las variables están documentadas en [`.env.example`](.env.example).

| Variable | Por defecto | Descripción |
|---|---|---|
| `APP_ENV` | `development` | `development` \| `production` |
| `PORT` | `8000` | Puerto HTTP (en Hugging Face Spaces: `7860`) |
| `LLM_PROVIDER` | `gemini` | `gemini` \| `ollama` \| `fake` |
| `GEMINI_API_KEY` | — | Clave de Google AI Studio (secreto) |
| `GEMINI_MODEL` | — | Modelo Flash con visión (recomendado: `gemini-3.5-flash-lite`) |
| `GEMINI_TIMEOUT_S` | `60` | Timeout por llamada a Gemini (s) |
| `OLLAMA_ENABLED` | `false` | Habilita el proveedor local |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | URL de Ollama |
| `OLLAMA_MODEL` | `qwen2.5vl` | Modelo de visión de Ollama |
| `ALLOWED_ORIGINS` | `http://localhost:5173` | Orígenes CORS (separados por comas) |
| `RATE_LIMIT_EXTRACT` | `10/minute` | Límite de extracciones por IP (ventana deslizante) |
| `DAILY_DOCUMENT_LIMIT` | `450` | Documentos/día enviados a Gemini; `0` = sin tope |
| `LOG_LEVEL` | `INFO` | Nivel de logging |

## Desarrollo

```bash
uv run pytest                                  # tests (excluye los marcados como `live`)
uv run pytest -m live                          # tests contra Gemini real (requiere clave en .env)
uv run python -m app.scripts.eval              # evaluación de calidad (consume cuota)
uv run pytest --cov=app --cov-report=term-missing
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run python -m app.scripts.export_openapi   # regenera openapi.json tras cambiar la API
```

## Integración continua

Cada PR a `main` (y cada push a `main`) ejecuta en GitHub Actions:

| Workflow | Qué verifica |
|---|---|
| [`ci.yml`](.github/workflows/ci.yml) | Ruff (lint + formato), mypy estricto, `openapi.json` actualizado, pytest con cobertura ≥ 80 % y `docker build` |
| [`branch-name.yml`](.github/workflows/branch-name.yml) | Ramas con el formato `<tipo>/<modulo>/api-<mod>-<NNNN>` |

Dependabot propone cada mes actualizaciones agrupadas de dependencias, actions e imágenes Docker.

## Licencia

[MIT](LICENSE)

La fuente [DejaVu Sans](https://dejavu-fonts.github.io/) incluida en `app/export/fonts/` tiene su propia licencia libre ([`LICENSE-DejaVu.txt`](app/export/fonts/LICENSE-DejaVu.txt)).
