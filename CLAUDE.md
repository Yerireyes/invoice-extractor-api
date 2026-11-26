# invoice-extractor-api — Backend

API REST que recibe imágenes o PDFs de facturas de **cualquier país**, extrae sus datos con un LLM de visión (gratis), los valida y los exporta a **Excel** y **PDF**. Es un proyecto de portafolio: el código, los tests, el CI/CD y la documentación deben verse profesionales.

## Ecosistema

```
facturas-ia/
├── buzon/                     ← comunicación con el frontend (léelo, ver "Buzón")
│   ├── contrato/              ← TÚ publicas aquí el contrato y openapi.json
│   ├── para-backend/          ← mensajes PARA ti
│   └── para-frontend/         ← mensajes que TÚ envías
├── invoice-extractor-api/     ← ESTE repo
└── invoice-extractor-web/     ← frontend (React). NUNCA edites archivos de ese repo.
```

Si no tienes acceso a `../buzon`, pide al usuario que lo añada a la sesión (`/add-dir ../buzon`).

## Al iniciar cada sesión

1. Lee `../buzon/para-backend/` (ignora `procesados/`). Los mensajes `prioridad: alta` van primero.
2. Revisa la tabla **Backlog** (abajo) y continúa con la primera historia `pendiente`, salvo que el usuario indique otra.
3. Trabaja **una historia a la vez**.

## Stack

| Propósito | Herramienta |
|---|---|
| Lenguaje | Python 3.14 |
| Paquetes / entorno | **uv** (`pyproject.toml` + `uv.lock`) |
| API | FastAPI + Uvicorn |
| Esquemas / config | Pydantic v2 + pydantic-settings |
| IA | `google-genai` (Gemini), `ollama` (opcional, local) |
| Excel / PDF | openpyxl / ReportLab |
| Archivos de entrada | Pillow, pypdfium2 (se descartó PyMuPDF 2.x: arrastra onnxruntime/numpy y duplica el tamaño de la imagen) |
| Rate limit | slowapi |
| Calidad | Ruff (lint + format), mypy (`strict`) |
| Tests | pytest, pytest-cov, httpx (`TestClient`) |

Usa las versiones estables más recientes al crear el proyecto. El **nombre del modelo de Gemini** no se hardcodea: va en `GEMINI_MODEL`; al implementar el adaptador, verifica en la documentación oficial qué modelo Flash con visión está disponible en el nivel gratuito.

## Arquitectura: monolito modular + puertos y adaptadores (ligero)

```
app/
├── main.py                  # create_app(): routers, CORS, handlers de error, rate limit
├── core/
│   ├── config.py            # Settings (pydantic-settings)
│   ├── errors.py            # AppError(code, status, params) + handler RFC 9457
│   └── logging.py
├── invoices/
│   ├── api.py               # endpoints /invoices/* (delgados)
│   ├── schemas.py           # Invoice, Party, LineItem, Tax, Validation, Issue, ExtractionResult
│   ├── service.py           # caso de uso: archivos → páginas → extractor → validación
│   ├── validation.py        # reglas puras (sin FastAPI, sin IA)
│   ├── ports.py             # class InvoiceExtractor(Protocol)
│   └── adapters/
│       ├── fake.py          # extractor determinista para tests y desarrollo sin API key
│       ├── gemini.py
│       └── ollama.py
├── export/
│   ├── api.py               # endpoints /exports/*
│   ├── i18n.py              # textos es/en de encabezados y mensajes de issues
│   ├── ports.py             # class Exporter(Protocol)
│   └── adapters/
│       ├── excel.py
│       └── pdf.py
└── shared/
    └── files.py             # validación de tipo/tamaño, PDF → imágenes, normalización
tests/                       # espejo de app/: tests/invoices/test_validation.py, etc.
docs/api-contract.md         # FUENTE DE VERDAD del contrato
openapi.json                 # generado, versionado en git
```

Reglas:
- **`api.py` es delgado**: parsea la petición, llama al servicio, devuelve. Sin lógica de negocio.
- **`validation.py`, `schemas.py` y los adaptadores de exportación no importan FastAPI.**
- Los servicios dependen de los **puertos** (`Protocol`), nunca de un adaptador concreto. La elección del adaptador se hace con dependencias de FastAPI (`Depends`) a partir de `Settings`; en tests se sobrescribe con `app.dependency_overrides`.
- **Dinero = `Decimal`** siempre. Serializado como string. Prohibido `float` para montos.
- Gemini tiene limitaciones con JSON Schema: usa un **esquema propio para el LLM** (strings simples) en el adaptador y mapea a los modelos de dominio con `Decimal`. El dominio no se adapta al LLM.
- Errores de negocio = `AppError` con `code` del contrato. Nunca devuelvas trazas ni mensajes internos al cliente.
- **Privacidad**: no se guardan archivos en disco (todo en memoria) y **no se loguea el contenido de las facturas** (solo metadatos: tamaño, páginas, proveedor, tiempo).
- El prompt de extracción vive en un archivo propio (`adapters/prompts.py`) e indica: no inventar datos (usar `null`), fechas a ISO 8601, moneda a ISO 4217 (inferir del símbolo/país), detectar si no es una factura, y devolver confianza por campo.

## Contrato

- La fuente de verdad es `docs/api-contract.md`. La copia inicial está en `../buzon/contrato/api-contract.md` (la historia api-cor-0001 la trae al repo).
- `openapi.json` se genera con `uv run python -m app.scripts.export_openapi` y **se versiona**. El CI falla si está desactualizado.
- **Cada vez que cambie el contrato** (en la misma rama que lo cambia):
  1. Actualiza `docs/api-contract.md` y regenera `openapi.json`.
  2. Copia ambos a `../buzon/contrato/`.
  3. Escribe un mensaje `aviso-cambio` en `../buzon/para-frontend/`.
- No hagas cambios incompatibles (quitar o renombrar campos, cambiar tipos) sin aviso al usuario.

## Flujo de trabajo Git

### Ramas: una por historia, siempre desde `main` actualizado

Formato: `<tipo>/<modulo>/api-<mod>-<NNNN>`

- `tipo`: `feat` | `fix` | `chore` | `refactor`
- `modulo` y su código de 3 letras:

| Módulo | Código | Cubre |
|---|---|---|
| `core` | `cor` | config, errores, app, CI, Docker, herramientas |
| `invoices` | `inv` | extracción, esquemas, validación |
| `export` | `exp` | Excel, PDF |
| `shared` | `shr` | manejo de archivos |
| `deploy` | `dep` | despliegue y releases |

- `NNNN`: numeración **global y correlativa en este repo** (no por módulo). Cada rama nueva —incluidos `fix` y `refactor` no planificados— toma el siguiente número libre y se registra en el Backlog.
- Regex (lo valida el CI): `^(feat|fix|chore|refactor)/[a-z0-9-]+/api-[a-z]{3}-[0-9]{4}$`
- Ejemplos: `feat/invoices/api-inv-0005`, `fix/export/api-exp-0015`

### Commits

Formato: `<tipo>(<modulo>): <descripción en español, minúscula, imperativo o descriptivo, sin punto final>`

- Ejemplos: `feat(invoices): endpoint para subir archivos`, `fix(export): totales por moneda con divisas mezcladas`
- Commits pequeños y atómicos. Los tests van en el mismo commit que el código que prueban.
- Para cambios de CI, tests o documentación usa `chore`.
- Nunca commitees `.env`, claves ni facturas reales.

### Pull requests

- Al terminar una historia: `git push -u origin <rama>` y abre un PR con `gh pr create`.
- Título: `[API-INV-0005] feat(invoices): validación aritmética`
- Cuerpo: qué se hizo, criterios de aceptación marcados, cómo probarlo, cambios de contrato (si los hay).
- **No hagas merge sin la aprobación del usuario.** Tras el merge: `git checkout main && git pull`, borra la rama local y marca la historia como `hecha` en el Backlog.
- Merge con **squash** (el título del PR queda como commit en `main`).

## Buzón

Protocolo completo en `../buzon/README.md`. Resumen:
- Escribe en `../buzon/para-frontend/AAAA-MM-DD-NN-tema.md` cuando: cambie el contrato, detectes una incoherencia en lo que pide el front, o necesites aclarar algo.
- Al resolver un mensaje de `para-backend/`: completa `## Respuesta`, cambia `estado`, muévelo a `procesados/`.
- Si un mensaje pide algo fuera del contrato, evalúalo; si tiene sentido, crea una historia nueva en el Backlog. Si no, recházalo con una razón.

## Comandos

```bash
uv sync                                   # instalar dependencias
uv run uvicorn app.main:app --reload      # servidor dev en :8000 (docs en /docs)
uv run pytest                             # tests
uv run pytest --cov=app --cov-report=term-missing
uv run ruff check . && uv run ruff format --check .
uv run mypy app
uv run python -m app.scripts.export_openapi   # regenera openapi.json
docker compose up --build                 # API en contenedor
docker compose --profile ollama up        # API + Ollama local
```

## Variables de entorno (`.env.example` siempre actualizado)

| Variable | Ejemplo | Descripción |
|---|---|---|
| `APP_ENV` | `development` | `development` \| `production` |
| `PORT` | `8000` | En Hugging Face Spaces: `7860` |
| `LLM_PROVIDER` | `gemini` | `gemini` \| `ollama` \| `fake` |
| `GEMINI_API_KEY` | — | Secreto. Nunca en el repo |
| `GEMINI_MODEL` | — | Modelo Flash con visión vigente |
| `OLLAMA_ENABLED` | `false` | |
| `OLLAMA_BASE_URL` | `http://ollama:11434` | |
| `OLLAMA_MODEL` | `qwen2.5vl` | Modelo de visión |
| `ALLOWED_ORIGINS` | `http://localhost:5173` | CSV, para CORS |
| `RATE_LIMIT_EXTRACT` | `10/minute` | |
| `LOG_LEVEL` | `INFO` | |

Si `GEMINI_API_KEY` está vacío en desarrollo, el proveedor por defecto es `fake` para que todo funcione sin clave.

## Tests

- **El CI nunca llama a APIs reales de IA.** Usa `adapters/fake.py` o mocks.
- Tests reales contra Gemini se marcan con `@pytest.mark.live` y se excluyen por defecto (`-m "not live"`); solo corren en local con clave.
- `validation.py` y los exportadores se prueban a fondo con casos unitarios (redondeos, `null`, varias monedas, varios impuestos, cero impuestos).
- El Excel se prueba **abriéndolo con openpyxl** y verificando hojas, encabezados, tipos de celda y fórmulas.
- Fixtures: imágenes de facturas **sintéticas** (generadas o de dominio público) en `tests/fixtures/`. Nunca facturas reales con datos personales.
- Cobertura mínima: 80 % (el CI falla por debajo).

## Docker

- `Dockerfile` multi-stage: etapa de build con uv, imagen final `python:3.14-slim`, **usuario no root**, `HEALTHCHECK` contra `/api/v1/health`, puerto desde `PORT`.
- Uvicorn con `--proxy-headers --forwarded-allow-ips="*"` (detrás del proxy de Hugging Face, para que el rate limit vea la IP real).
- `docker-compose.yml`: servicio `api` (lee `.env`) y servicio `ollama` bajo el profile `ollama`.
- `.dockerignore` completo (sin `.venv`, `.git`, tests, fixtures).

## CI/CD

Solo lo que aporta valor:

| Workflow | Disparador | Qué hace |
|---|---|---|
| `ci.yml` | PR a `main` y push a `main` | Ruff (lint + format), mypy, pytest + cobertura ≥ 80 %, verificar `openapi.json` actualizado, `docker build` (sin push) |
| `branch-name.yml` | PR | Valida el nombre de la rama con el regex |
| `dependabot.yml` | mensual | Actualizaciones agrupadas de dependencias (ecosistema `uv`, actualiza `uv.lock`), GitHub Actions e imágenes Docker (sin subir de versión de Python) |
| `release.yml` | push a `main` (api-dep-0014) | release-please: mantiene un PR de release con versión semver + CHANGELOG |
| `deploy.yml` | **release publicado** (api-dep-0014) | Despliega a Hugging Face Spaces + smoke test a `/health` |

**Estrategia de despliegue:** CI en cada PR y en `main`; **producción solo al publicar un release** (no en cada push a `main`). release-please genera la versión a partir de los commits (`feat` → minor, `fix` → patch).

## Definition of Done (toda historia)

- [ ] Criterios de aceptación cumplidos
- [ ] Tests nuevos y existentes pasando; cobertura ≥ 80 %
- [ ] `ruff`, `mypy` sin errores
- [ ] `openapi.json` regenerado si cambió la API; contrato copiado al buzón + `aviso-cambio`
- [ ] `.env.example` y `README.md` actualizados si aplica
- [ ] PR abierto con el formato indicado; Backlog actualizado

## Backlog

Estados: `pendiente` | `en curso` | `hecha`. Añade al final las historias nuevas (fix, refactor o surgidas del buzón) con el siguiente número.

| ID | Rama | Estado |
|---|---|---|
| API-COR-0001 | `chore/core/api-cor-0001` | hecha |
| API-COR-0002 | `chore/core/api-cor-0002` | hecha |
| API-COR-0003 | `feat/core/api-cor-0003` | hecha |
| API-INV-0004 | `feat/invoices/api-inv-0004` | hecha |
| API-INV-0005 | `feat/invoices/api-inv-0005` | en curso |
| API-SHR-0006 | `feat/shared/api-shr-0006` | pendiente |
| API-INV-0007 | `feat/invoices/api-inv-0007` | pendiente |
| API-INV-0008 | `feat/invoices/api-inv-0008` | pendiente |
| API-INV-0009 | `feat/invoices/api-inv-0009` | pendiente |
| API-COR-0010 | `feat/core/api-cor-0010` | pendiente |
| API-EXP-0011 | `feat/export/api-exp-0011` | pendiente |
| API-EXP-0012 | `feat/export/api-exp-0012` | pendiente |
| API-INV-0013 | `feat/invoices/api-inv-0013` | pendiente |
| API-DEP-0014 | `chore/deploy/api-dep-0014` | pendiente |

### API-COR-0001 — Estructura base del proyecto
*Como desarrollador quiero un proyecto base funcional para construir sobre él.*
- `git init` si hace falta, `pyproject.toml` con uv, estructura de carpetas de la sección Arquitectura (módulos vacíos con `__init__.py`).
- `core/config.py` con `Settings`; `.env.example`; `.gitignore`.
- `GET /api/v1/health` → `{"status": "ok"}` con test.
- Ruff, mypy (strict) y pytest configurados en `pyproject.toml`.
- `Dockerfile`, `.dockerignore`, `docker-compose.yml` (sin Ollama aún). `docker compose up` responde en `/api/v1/health`.
- Copiar `../buzon/contrato/api-contract.md` a `docs/api-contract.md`.
- `README.md` inicial: qué es, stack, cómo correrlo (local y Docker).

### API-COR-0002 — Integración continua
*Como desarrollador quiero que cada PR se verifique automáticamente.*
- `.github/workflows/ci.yml`: uv con caché, ruff, mypy, pytest con cobertura (umbral 80 %), `docker build`.
- `.github/workflows/branch-name.yml` con el regex de ramas.
- `.github/dependabot.yml` (uv + github-actions + docker, mensual, agrupado).
- Badge de CI en el README.

### API-COR-0003 — Manejo de errores y metadatos
*Como cliente de la API quiero errores consistentes y saber los límites del servicio.*
- `AppError` + handlers: errores de negocio, `RequestValidationError` → `VALIDATION_ERROR`, excepciones no controladas → `INTERNAL_ERROR`. Todo como `application/problem+json` (sección 5 del contrato).
- `GET /api/v1/meta` según contrato, con valores tomados de `Settings`.
- CORS con `ALLOWED_ORIGINS` y `expose_headers=["Content-Disposition"]`.
- Script `app/scripts/export_openapi.py` + `openapi.json` versionado + paso del CI que falla si está desactualizado.
- Publicar `openapi.json` en `../buzon/contrato/` y avisar al frontend.

### API-INV-0004 — Esquemas de factura
*Como sistema necesito un modelo de factura genérico, válido para cualquier país.*
- `Invoice`, `Party`, `LineItem`, `Tax`, `Validation`, `Issue`, `ExtractionResult`, `ExtractResponse` según contrato.
- Montos `Decimal` serializados como string; moneda validada contra ISO 4217; `extra="ignore"`.
- Tests de serialización/deserialización (incluido que `0.1 + 0.2` no genere errores de float).

### API-INV-0005 — Reglas de validación
*Como usuario quiero saber si los números de mi factura cuadran.*
- `validation.py` con todas las reglas de la sección 4 del contrato, tolerancia ±0.02, omitiendo reglas cuyos campos son `null`.
- Cálculo de `status` (`valid` / `warnings` / `invalid`).
- `POST /api/v1/invoices/validate`.
- Tests unitarios por regla, incluidos bordes de tolerancia.

### API-SHR-0006 — Manejo de archivos de entrada
*Como usuario quiero subir fotos o PDFs y recibir errores claros si no son válidos.*
- Validar tipo **por contenido** (magic bytes), no solo por extensión o MIME declarado.
- Validar tamaño **leyendo por bloques** (no confiar en `Content-Length`).
- PDF → imágenes por página con pypdfium2 (máx. `max_pdf_pages`); imágenes: corregir orientación EXIF y redimensionar a un máximo razonable.
- Todo en memoria. Tests con fixtures válidos, corruptos y en límite.

### API-INV-0007 — Endpoint de extracción con extractor falso
*Como usuario quiero subir varias facturas y recibir sus datos extraídos.*
- Puerto `InvoiceExtractor` y adaptador `fake` determinista.
- `POST /api/v1/invoices/extract` (multipart, 1..10 archivos, campo `provider`) según contrato: éxito parcial, `summary`, errores por archivo, `LOW_CONFIDENCE`.
- Procesamiento concurrente de archivos con límite (`asyncio.Semaphore`).
- Tests de integración del endpoint con el adaptador falso.

### API-INV-0008 — Adaptador Gemini
*Como usuario quiero que la IA lea mis facturas reales.*
- Adaptador `gemini.py` con el SDK `google-genai`, salida estructurada (esquema propio del LLM → mapeo a dominio).
- Prompt en `prompts.py` (ver reglas de Arquitectura). Facturas multipágina: todas las páginas en una sola llamada.
- Mapear errores del proveedor: cuota → `LLM_QUOTA_EXCEEDED`, timeout/caída → `LLM_UNAVAILABLE`; respuesta que no es factura → `NOT_AN_INVOICE`.
- Timeout y un reintento con backoff solo en errores transitorios.
- Tests con el cliente mockeado + un test `live` opcional.

### API-INV-0009 — Calidad de extracción medible
*Como desarrollador quiero medir qué tan bien extrae la IA.*
- `tests/eval/`: 10–15 facturas sintéticas variadas (países, monedas, idiomas, tickets, multipágina) con su JSON esperado.
- Script `uv run python -m app.scripts.eval` que ejecuta el extractor configurado y reporta precisión por campo.
- No corre en CI (gasta cuota). Resultados resumidos en el README.

### API-COR-0010 — Límite de peticiones
*Como dueño del servicio quiero proteger la cuota gratuita de abuso.*
- slowapi por IP real (con proxy headers) en `/invoices/extract` (`RATE_LIMIT_EXTRACT`).
- Respuesta 429 `RATE_LIMITED` con `retry_after_s` y header `Retry-After`.
- Tests.

### API-EXP-0011 — Exportación a Excel
*Como usuario quiero descargar mis facturas en un Excel listo para usar.*
- Puerto `Exporter` y adaptador `excel.py` según la sección 7 del contrato (5 hojas, Ref, Tablas de Excel, fórmulas por moneda, formato condicional, fechas y números reales).
- `i18n.py` con encabezados y mensajes de issues en `es` y `en`.
- `POST /api/v1/exports/excel` con `Content-Disposition` y límite `TOO_MANY_INVOICES`.
- Tests abriendo el archivo con openpyxl (hojas, encabezados, tipos, fórmulas, monedas mezcladas).

### API-EXP-0012 — Exportación a PDF
*Como usuario quiero un PDF limpio de mis facturas.*
- Adaptador `pdf.py` con ReportLab según la sección 8 del contrato: una factura por página, conceptos en tabla, bloque de alertas.
- Fuente con soporte Unicode completo (acentos, ñ, €, símbolos de otras monedas) incluida en el repo.
- `POST /api/v1/exports/pdf`. Tests: número de páginas y texto extraído con pypdfium2.

### API-INV-0013 — Adaptador Ollama (modo privado)
*Como usuario preocupado por la privacidad quiero procesar facturas sin enviarlas a la nube.*
- Adaptador `ollama.py` con el mismo puerto y prompt. Habilitado solo si `OLLAMA_ENABLED=true`; si no, pedirlo devuelve `PROVIDER_NOT_AVAILABLE`.
- Servicio `ollama` en docker-compose (profile `ollama`) y README con instrucciones.
- `/meta` refleja la disponibilidad real. Tests con cliente mockeado.

### API-DEP-0014 — Releases y despliegue a producción
*Como dueño del proyecto quiero publicar versiones estables en la nube de forma controlada.*
- `release.yml` con **release-please** (tipo `python`): PR de release automático con versión semver y `CHANGELOG.md`. La versión de `/meta` se lee del paquete.
- `deploy.yml` disparado en **`release: published`**: sube la app a un **Hugging Face Space (SDK Docker)** usando el secreto `HF_TOKEN`. El `README.md` del Space (con frontmatter `sdk: docker`, `app_port: 7860`) se genera en el paso de deploy; no reemplaza el README de GitHub.
- Smoke test tras el deploy: reintentar `GET /api/v1/health` hasta 200 o fallar el job.
- `openapi.json` incluye `info.version` (leída del paquete): cuando release-please suba la versión en `pyproject.toml`, el check `export_openapi --check` fallará en su PR. Resolverlo (p. ej. un paso del workflow de release que regenere y commitee `openapi.json` en la rama de release, o actualizar `uv.lock`/`openapi.json` vía `extra-files`).
- Documentar en el README los pasos manuales del usuario: crear el Space, poner `GEMINI_API_KEY`, `GEMINI_MODEL` y `ALLOWED_ORIGINS` (URL de Vercel) como *secrets* del Space, y `HF_TOKEN` en GitHub.
- Avisar al frontend por el buzón la URL pública de la API.
