# Contrato de la API — v1

> Fuente de verdad: `invoice-extractor-api/docs/api-contract.md`. Este archivo es una copia publicada por el backend.
> El `openapi.json` generado por FastAPI debe coincidir con este documento (se publica junto a esta copia).

## 1. Reglas generales

| Regla | Decisión |
|---|---|
| Prefijo | `/api/v1/...` |
| Formato | JSON, claves en `snake_case` |
| Dinero, cantidades, tasas | **Strings decimales** (`"1234.50"`, `"-50"`): regex `^-?\d{1,15}(\.\d{1,6})?$`, sin exponentes ni separadores de miles. Los números JSON se **rechazan** (422). Nunca `float`. |
| Fechas | ISO 8601 (`"2026-11-25"`) |
| Moneda | ISO 4217 (`"MXN"`, `"EUR"`, `"USD"`) |
| País | ISO 3166-1 alfa-2 (`"MX"`, `"ES"`), opcional |
| Errores HTTP | RFC 9457 (Problem Details), `Content-Type: application/problem+json` |
| Mensajes | La API devuelve **códigos + `params`**; el frontend traduce |
| Estado | Sin almacenamiento. El cliente reenvía la factura completa para validar/exportar |
| Campos desconocidos en el body | Se **ignoran** (el frontend puede añadir un `id` local sin romper nada) |

## 2. Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/v1/health` | `{"status": "ok"}` |
| `GET` | `/api/v1/meta` | Límites, formatos, proveedores, idiomas de exportación |
| `POST` | `/api/v1/invoices/extract` | Sube 1..N archivos → facturas extraídas + validación |
| `POST` | `/api/v1/invoices/validate` | Revalida una factura editada (no llama a la IA) |
| `POST` | `/api/v1/exports/excel` | Facturas → `.xlsx` |
| `POST` | `/api/v1/exports/pdf` | Facturas → `.pdf` (una factura por página) |

### 2.1 `GET /api/v1/meta` → 200

```json
{
  "version": "1.0.0",
  "limits": {
    "max_files_per_request": 10,
    "max_file_size_mb": 10,
    "max_pdf_pages": 5,
    "max_invoices_per_export": 100
  },
  "accepted_mime_types": ["image/jpeg", "image/png", "image/webp", "application/pdf"],
  "providers": [
    { "id": "gemini", "available": true, "default": true },
    { "id": "ollama", "available": false, "default": false }
  ],
  "export_languages": ["es", "en"]
}
```

- `version`: versión semver del servicio desplegado (no la del contrato).
- `limits`: valores configurados en el servidor; el frontend debe usarlos en lugar de constantes propias.
- `providers[].id`: `gemini` | `ollama` | `fake`. Exactamente un proveedor tiene `default: true`.
  - `fake` es un extractor **determinista de desarrollo** que devuelve datos de ejemplo. Solo aparece (primero y como `default`) cuando el servidor corre sin clave de Gemini en desarrollo; **nunca en producción**. El frontend debería indicar que los datos no son reales.

### 2.2 `POST /api/v1/invoices/extract`

Petición `multipart/form-data`:

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `files` | archivo[] | sí | 1..`max_files_per_request` |
| `provider` | string | no | `gemini` (por defecto) \| `ollama` |

Respuesta `200` (éxito parcial permitido; cada resultado tiene su `status`):

```json
{
  "results": [
    {
      "file_name": "factura_luz.jpg",
      "status": "success",
      "invoice": { "...": "Invoice (sección 3)" },
      "validation": {
        "status": "invalid",
        "issues": [
          {
            "code": "TOTAL_MISMATCH",
            "severity": "error",
            "field": "total",
            "params": { "expected": "1160.00", "actual": "1150.00" }
          }
        ]
      },
      "field_confidence": { "issuer.tax_id": 0.62, "total": 0.98 },
      "meta": { "provider": "gemini", "model": "<modelo>", "pages": 1, "processing_ms": 2840 }
    },
    {
      "file_name": "foto_perro.png",
      "status": "error",
      "error": { "code": "NOT_AN_INVOICE", "params": {} }
    }
  ],
  "summary": { "total": 2, "succeeded": 1, "failed": 1 }
}
```

- Cada elemento de `results` es un `ExtractionResult`: **unión discriminada por `status`**.
  - `"success"` → `ExtractionSuccess`: `file_name`, `invoice`, `validation`, `field_confidence`, `meta`.
  - `"error"` → `ExtractionFailure`: `file_name`, `error` (`FileError`: `{ code, params }`).
- `field_confidence`: claves en notación de ruta (`issuer.tax_id`, `line_items.0.amount`), valores 0..1.
- `meta.provider`: `gemini` | `ollama` | `fake`.
- Errores por archivo: `NOT_AN_INVOICE`, `UNREADABLE_FILE`, `UNSUPPORTED_FILE_TYPE`, `FILE_TOO_LARGE`, `PDF_TOO_MANY_PAGES`, `EXTRACTION_FAILED`.
- Si **toda** la petición es inválida (p. ej. demasiados archivos) → error HTTP (sección 5).

### 2.3 `POST /api/v1/invoices/validate`

Petición: `{ "invoice": Invoice }` → Respuesta 200: `{ "validation": Validation }`

### 2.4 `POST /api/v1/exports/excel` y `POST /api/v1/exports/pdf`

Petición:

```json
{
  "invoices": [ "Invoice", "Invoice" ],
  "options": {
    "language": "es",
    "include_line_items": true,
    "include_alerts": true
  }
}
```

Respuesta 200: archivo binario.
- Excel: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
- PDF: `application/pdf`
- `Content-Disposition: attachment; filename="facturas_AAAAMMDD_HHMM.xlsx"` (o `.pdf`)
- El header `Content-Disposition` se expone vía CORS (`Access-Control-Expose-Headers`).

## 3. Esquema `Invoice`

```jsonc
{
  "document_type": "invoice",        // invoice | receipt | credit_note | other
  "invoice_number": "A-00123",       // nullable
  "issue_date": "2026-11-20",        // OBLIGATORIO
  "due_date": "2026-12-20",          // nullable
  "currency": "MXN",                 // OBLIGATORIO, ISO 4217
  "country": "MX",                   // nullable
  "language": "es",                  // nullable, idioma detectado (ISO 639-1)

  "issuer": {                        // Party — name OBLIGATORIO
    "name": "Comercial XYZ S.A.",
    "tax_id": "XYZ010101ABC",        // nullable
    "tax_id_type": "RFC",            // nullable, texto libre (RFC, NIF, CUIT, VAT, EIN...)
    "address": "Av. Siempre Viva 123, CDMX", // nullable
    "email": null,
    "phone": null
  },
  "recipient": null,                 // Party nullable (todos sus campos nullable)

  "line_items": [                    // puede ser []
    {
      "description": "Servicio de consultoría",
      "quantity": "2",               // nullable
      "unit": "h",                   // nullable
      "unit_price": "500.00",        // nullable
      "discount": "0.00",            // nullable
      "tax_rate": "16",              // nullable, porcentaje
      "amount": "1000.00"            // cantidad × precio − descuento
    }
  ],

  "subtotal": "1000.00",             // nullable
  "discount_total": "0.00",          // nullable
  "taxes": [                         // puede ser []
    { "name": "IVA", "rate": "16", "base": "1000.00", "amount": "160.00" }
  ],
  "total": "1160.00",                // OBLIGATORIO

  "payment_method": "Transferencia", // nullable
  "notes": null
}
```

**Notas sobre el esquema**

- **"OBLIGATORIO" significa obligatorio para que la factura sea válida, no para que la petición se acepte.** `issue_date`, `currency`, `total` e `issuer.name` admiten `null` en el JSON: si faltan, la validación devuelve `MISSING_REQUIRED_FIELD` (status `invalid`) en lugar de un 422. Así una factura que la IA leyó parcialmente no se pierde y el usuario puede completarla.
- `currency` se normaliza a mayúsculas y admite cualquier texto corto (≤ 10). Si no es ISO 4217, la validación devuelve `INVALID_CURRENCY`.
- `country` (2 letras) y `language` (2 letras) se normalizan (`mx` → `MX`, `ES` → `es`). Si tienen otro formato → 422.
- Valores por defecto si se omiten: `document_type` = `"invoice"`, `issuer` = Party vacío, `line_items` y `taxes` = `[]`, el resto `null`.
- Obligatorios en el JSON (422 si faltan): `line_items[].amount` y `taxes[].amount`.
- Límites de longitud: textos cortos ≤ 300 caracteres; `description`, `address` y `notes` ≤ 5000.

## 4. Validación

`Validation = { "status": "valid" | "warnings" | "invalid", "issues": Issue[] }`
`Issue = { "code": string, "severity": "error" | "warning" | "info", "field": string | null, "params": object }`

- `valid`: sin issues. `warnings`: solo warning/info. `invalid`: al menos un error.
- Tolerancia de redondeo: **±0.02** en la moneda de la factura.
- Las reglas aritméticas solo se aplican si los campos implicados no son `null`.

| Código | Severidad | Regla | `params` |
|---|---|---|---|
| `MISSING_REQUIRED_FIELD` | error | Falta un campo obligatorio | `{}` (usa `field`) |
| `INVALID_CURRENCY` | error | No es ISO 4217 | `{ "value" }` |
| `TOTAL_MISMATCH` | error | `subtotal − discount_total + Σ taxes.amount ≠ total` | `{ "expected", "actual" }` |
| `SUBTOTAL_MISMATCH` | error | `Σ line_items.amount ≠ subtotal` | `{ "expected", "actual" }` |
| `LINE_AMOUNT_MISMATCH` | warning | `quantity × unit_price − discount ≠ amount` | `{ "line", "expected", "actual" }` |
| `TAX_MISMATCH` | warning | `base × rate / 100 ≠ amount` | `{ "index", "expected", "actual" }` |
| `DUE_BEFORE_ISSUE` | warning | `due_date < issue_date` | `{}` |
| `FUTURE_DATE` | warning | `issue_date` posterior a hoy | `{}` |
| `LOW_CONFIDENCE` | info | Confianza de un campo < 0.7 (solo en `/extract`) | `{ "confidence" }` |

## 5. Errores HTTP (RFC 9457)

```json
{
  "type": "about:blank",
  "title": "Unprocessable Content",
  "status": 422,
  "code": "TOO_MANY_FILES",
  "detail": null,
  "params": { "max_files": 10 }
}
```

- `title`: frase estándar del status HTTP (en inglés). **No traducir ni mostrar**: el frontend traduce a partir de `code` + `params`.
- `detail`: texto técnico opcional (`null` casi siempre). Tampoco es para el usuario final.
- `params`: siempre presente (puede ser `{}`).
- Los errores **por archivo** de `/extract` (`FILE_TOO_LARGE`, `NOT_AN_INVOICE`…) no son errores HTTP: van dentro de `results[]` con status 200 (sección 2.2).

| `code` | HTTP | Cuándo |
|---|---|---|
| `TOO_MANY_FILES` | 422 | Más archivos que el límite |
| `NO_FILES` | 422 | Ningún archivo enviado |
| `TOO_MANY_INVOICES` | 422 | Exportación con más facturas que el límite |
| `VALIDATION_ERROR` | 422 | Body inválido o ilegible. `params.errors`: lista de `{ "loc": [..], "msg", "type" }` (nunca incluye el valor enviado). Otros 4xx del framework (p. ej. 400 multipart corrupto) usan este código con su propio status |
| `PROVIDER_NOT_AVAILABLE` | 422 | Se pidió un proveedor deshabilitado |
| `NOT_FOUND` | 404 | La ruta no existe |
| `METHOD_NOT_ALLOWED` | 405 | Método HTTP no soportado en esa ruta (header `Allow` con los válidos) |
| `RATE_LIMITED` | 429 | Demasiadas peticiones por IP (`params.retry_after_s`) |
| `LLM_QUOTA_EXCEEDED` | 503 | Cuota del proveedor agotada |
| `LLM_UNAVAILABLE` | 503 | El proveedor no responde |
| `INTERNAL_ERROR` | 500 | Error inesperado (sin detalles internos) |

Esquema OpenAPI: `ProblemDetails`. Todas las respuestas 4xx/5xx se declaran con `application/problem+json`.

## 6. Límites v1

| Límite | Valor |
|---|---|
| Archivos por petición | 10 |
| Tamaño por archivo | 10 MB |
| Páginas por PDF | 5 |
| Extracciones por IP | 10 / minuto |
| Facturas por exportación | 100 |

## 7. Excel exportado

Encabezados en el idioma de `options.language`. Cada factura recibe una **Ref** (`F-001`, `F-002`…) según su orden en la petición; la Ref une todas las hojas.

| Hoja | Una fila por | Columnas |
|---|---|---|
| **Resumen** | factura | Ref, Archivo*, Tipo, Número, Fecha emisión, Vencimiento, Emisor, ID fiscal emisor, Receptor, ID fiscal receptor, Moneda, Subtotal, Descuentos, Impuestos, Total, Método de pago, Estado validación, Nº alertas |
| **Conceptos** (si `include_line_items`) | línea | Ref, Número factura, Emisor, # Línea, Descripción, Cantidad, Unidad, Precio unitario, Descuento, % Impuesto, Importe, Moneda |
| **Impuestos** | impuesto | Ref, Número factura, Emisor, Impuesto, Tasa %, Base, Importe, Moneda |
| **Totales por moneda** | moneda | Moneda, Nº facturas, Subtotal, Descuentos, Impuestos, Total — **fórmulas** `COUNTIF`/`SUMIF` sobre *Resumen* |
| **Alertas** (si `include_alerts`) | issue | Ref, Número factura, Severidad, Código, Campo, Mensaje (traducido) |

\* `Archivo`: el body de exportación no trae el nombre de archivo; la columna queda vacía salvo que en el futuro se añada `source_file_name` opcional a la petición.

Formato: cada hoja es una Tabla de Excel con filtros, encabezado en negrita y congelado, fechas como fechas reales, montos como números `#,##0.00`, anchos ajustados, y formato condicional en *Resumen* (rojo claro = `invalid`, amarillo claro = `warnings`). **Nunca se suman monedas distintas.**

## 8. PDF exportado

Un único PDF, una factura por página: encabezado (tipo, número, fechas), emisor/receptor, tabla de conceptos, impuestos, totales, y un bloque de alertas si las hay. Textos fijos en el idioma de `options.language`.

## 9. Nombres de esquemas (`components.schemas` de `openapi.json`)

Estables: el frontend genera sus tipos a partir de ellos. Un esquema aparece en `openapi.json` cuando existe el endpoint que lo usa.

| Esquema | Uso |
|---|---|
| `Invoice`, `Party`, `LineItem`, `Tax` | Factura (sección 3) |
| `Validation`, `Issue` | Validación (sección 4) |
| `ExtractResponse`, `ExtractionResult` (`ExtractionSuccess` \| `ExtractionFailure`), `FileError`, `ExtractionMeta`, `ExtractSummary` | `/invoices/extract` |
| `ValidateRequest`, `ValidateResponse` | `/invoices/validate` |
| `ExportRequest`, `ExportOptions` | `/exports/*` |
| `MetaResponse`, `Limits`, `ProviderInfo` | `/meta` |
| `HealthResponse` | `/health` |
| `ProblemDetails` | Errores RFC 9457 |

## 10. Historial de cambios

| Fecha | Historia | Cambio | ¿Compatible? |
|---|---|---|---|
| 2026-11-25 | API-COR-0003 | Nuevos códigos HTTP `NOT_FOUND` (404) y `METHOD_NOT_ALLOWED` (405). Proveedor `fake` en `/meta` (solo desarrollo). Aclaraciones sobre `title`, `detail`, `params` y `params.errors`. Ejemplo de la sección 5 corregido. Primera publicación de `openapi.json`. | Sí (solo añade) |
| 2026-11-25 | API-INV-0004 | Esquema de error renombrado `ProblemDetail` → `ProblemDetails` (solo afecta a nombres de tipos generados). `ExtractionResult` como unión discriminada por `status`. Aclarado que los campos "OBLIGATORIO" admiten `null` y generan `MISSING_REQUIRED_FIELD`. Formato decimal exacto (solo strings). Nueva sección 9 con los nombres de esquemas. | Sí (el renombrado no cambia el JSON) |
