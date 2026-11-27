# Changelog

## [1.0.0](https://github.com/Yerireyes/invoice-extractor-api/compare/v0.1.0...v1.0.0) (2026-11-27)


### Nuevas funcionalidades

* **core:** api base con fastapi, configuración, docker e integración continua ([e614277](https://github.com/Yerireyes/invoice-extractor-api/commit/e61427771acd8cb3808c3eda045a41ef167fb4a3))
* **core:** errores rfc 9457, endpoint /meta y openapi.json versionado ([e614277](https://github.com/Yerireyes/invoice-extractor-api/commit/e61427771acd8cb3808c3eda045a41ef167fb4a3))
* **core:** límite de peticiones por ip y tope diario de documentos ([e614277](https://github.com/Yerireyes/invoice-extractor-api/commit/e61427771acd8cb3808c3eda045a41ef167fb4a3))
* **deploy:** versiones automáticas y despliegue a hugging face spaces ([e614277](https://github.com/Yerireyes/invoice-extractor-api/commit/e61427771acd8cb3808c3eda045a41ef167fb4a3))
* **export:** exportación a excel con tablas, fórmulas por moneda y formato condicional ([e614277](https://github.com/Yerireyes/invoice-extractor-api/commit/e61427771acd8cb3808c3eda045a41ef167fb4a3))
* **export:** exportación a pdf con fuente unicode incrustada ([e614277](https://github.com/Yerireyes/invoice-extractor-api/commit/e61427771acd8cb3808c3eda045a41ef167fb4a3))
* **invoices:** adaptador gemini con salida estructurada ([e614277](https://github.com/Yerireyes/invoice-extractor-api/commit/e61427771acd8cb3808c3eda045a41ef167fb4a3))
* **invoices:** esquema de factura genérico para cualquier país con montos decimal ([e614277](https://github.com/Yerireyes/invoice-extractor-api/commit/e61427771acd8cb3808c3eda045a41ef167fb4a3))
* **invoices:** evaluación medible de la calidad de extracción ([e614277](https://github.com/Yerireyes/invoice-extractor-api/commit/e61427771acd8cb3808c3eda045a41ef167fb4a3))
* **invoices:** extracción de varias facturas por petición con post /invoices/extract ([e614277](https://github.com/Yerireyes/invoice-extractor-api/commit/e61427771acd8cb3808c3eda045a41ef167fb4a3))
* **invoices:** proveedor ollama para procesar facturas en local ([e614277](https://github.com/Yerireyes/invoice-extractor-api/commit/e61427771acd8cb3808c3eda045a41ef167fb4a3))
* **invoices:** validación aritmética de facturas con post /invoices/validate ([e614277](https://github.com/Yerireyes/invoice-extractor-api/commit/e61427771acd8cb3808c3eda045a41ef167fb4a3))
* **shared:** validación de archivos por contenido y pdf a imágenes en memoria ([e614277](https://github.com/Yerireyes/invoice-extractor-api/commit/e61427771acd8cb3808c3eda045a41ef167fb4a3))


### Correcciones

* **deploy:** despliegue en render con imagen en ghcr [API-DEP-0015] ([#17](https://github.com/Yerireyes/invoice-extractor-api/issues/17)) ([49ff59f](https://github.com/Yerireyes/invoice-extractor-api/commit/49ff59f90e1ecc08e80d5410117a5d90e47b8768))
