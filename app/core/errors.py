"""Errores de la API en formato RFC 9457 (Problem Details).

Los errores de negocio se lanzan como `AppError` con un `code` del contrato. La API nunca devuelve
trazas ni mensajes internos: el cliente recibe solo `code`, `params` y textos genéricos.
"""

import logging
from collections.abc import Mapping, Sequence
from enum import StrEnum
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

PROBLEM_JSON = "application/problem+json"


class ErrorCode(StrEnum):
    """Códigos de error HTTP del contrato (sección 5)."""

    TOO_MANY_FILES = "TOO_MANY_FILES"
    NO_FILES = "NO_FILES"
    TOO_MANY_INVOICES = "TOO_MANY_INVOICES"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PROVIDER_NOT_AVAILABLE = "PROVIDER_NOT_AVAILABLE"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    RATE_LIMITED = "RATE_LIMITED"
    LLM_QUOTA_EXCEEDED = "LLM_QUOTA_EXCEEDED"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_DEFAULT_STATUS: Mapping[ErrorCode, HTTPStatus] = {
    ErrorCode.TOO_MANY_FILES: HTTPStatus.UNPROCESSABLE_CONTENT,
    ErrorCode.NO_FILES: HTTPStatus.UNPROCESSABLE_CONTENT,
    ErrorCode.TOO_MANY_INVOICES: HTTPStatus.UNPROCESSABLE_CONTENT,
    ErrorCode.VALIDATION_ERROR: HTTPStatus.UNPROCESSABLE_CONTENT,
    ErrorCode.PROVIDER_NOT_AVAILABLE: HTTPStatus.UNPROCESSABLE_CONTENT,
    ErrorCode.NOT_FOUND: HTTPStatus.NOT_FOUND,
    ErrorCode.METHOD_NOT_ALLOWED: HTTPStatus.METHOD_NOT_ALLOWED,
    ErrorCode.REQUEST_TOO_LARGE: HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
    ErrorCode.RATE_LIMITED: HTTPStatus.TOO_MANY_REQUESTS,
    ErrorCode.LLM_QUOTA_EXCEEDED: HTTPStatus.SERVICE_UNAVAILABLE,
    ErrorCode.LLM_UNAVAILABLE: HTTPStatus.SERVICE_UNAVAILABLE,
    ErrorCode.INTERNAL_ERROR: HTTPStatus.INTERNAL_SERVER_ERROR,
}


class ProblemDetails(BaseModel):
    """Cuerpo de error RFC 9457 extendido con `code` y `params`."""

    type: str = Field(default="about:blank", examples=["about:blank"])
    title: str = Field(examples=["Unprocessable Content"])
    status: int = Field(examples=[422])
    code: ErrorCode
    detail: str | None = Field(default=None, examples=["Max 10 files per request"])
    params: dict[str, Any] = Field(default_factory=dict, examples=[{"max_files": 10}])


class AppError(Exception):
    """Error de negocio con un código del contrato. El status por defecto sale del contrato."""

    def __init__(
        self,
        code: ErrorCode,
        status: int | None = None,
        *,
        params: Mapping[str, Any] | None = None,
        detail: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.code = code
        self.status = status or _DEFAULT_STATUS[code]
        self.params = dict(params or {})
        self.detail = detail
        self.headers = dict(headers or {})
        super().__init__(code)


def problem_response(
    code: ErrorCode,
    status: int,
    *,
    params: Mapping[str, Any] | None = None,
    detail: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Construye una respuesta `application/problem+json`."""
    problem = ProblemDetails(
        title=HTTPStatus(status).phrase,
        status=status,
        code=code,
        detail=detail,
        params=dict(params or {}),
    )
    return JSONResponse(
        content=problem.model_dump(mode="json"),
        status_code=status,
        media_type=PROBLEM_JSON,
        headers=dict(headers or {}),
    )


def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """Documenta en OpenAPI las respuestas de error indicadas como `ProblemDetails`.

    FastAPI publica el modelo bajo `application/json`; `use_problem_json_in_openapi` lo mueve a
    `application/problem+json`, que es lo que la API devuelve realmente.
    """
    return {
        status: {"model": ProblemDetails, "description": HTTPStatus(status).phrase}
        for status in statuses
    }


_PROBLEM_REF = "#/components/schemas/ProblemDetails"


def use_problem_json_in_openapi(app: FastAPI) -> None:
    """Publica las respuestas `ProblemDetails` de OpenAPI como `application/problem+json`."""
    build_openapi = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = build_openapi()
            for path_item in schema.get("paths", {}).values():
                for operation in path_item.values():
                    for response in operation.get("responses", {}).values():
                        content = response.get("content", {})
                        json_content = content.get("application/json", {})
                        if json_content.get("schema", {}).get("$ref") == _PROBLEM_REF:
                            content[PROBLEM_JSON] = content.pop("application/json")
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]


def _validation_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """Detalle de Pydantic sin `input` ni `ctx`: nunca se devuelve contenido del cliente."""
    return [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]} for error in errors
    ]


_HTTP_STATUS_CODES: Mapping[int, ErrorCode] = {
    HTTPStatus.NOT_FOUND: ErrorCode.NOT_FOUND,
    HTTPStatus.METHOD_NOT_ALLOWED: ErrorCode.METHOD_NOT_ALLOWED,
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE: ErrorCode.REQUEST_TOO_LARGE,
}


def _http_error_code(status: int) -> ErrorCode:
    if status in _HTTP_STATUS_CODES:
        return _HTTP_STATUS_CODES[status]
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return ErrorCode.INTERNAL_ERROR
    return ErrorCode.VALIDATION_ERROR


def register_error_handlers(app: FastAPI) -> None:
    """Registra los handlers que convierten cualquier error en Problem Details."""
    use_problem_json_in_openapi(app)

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return problem_response(
            exc.code, exc.status, params=exc.params, detail=exc.detail, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return problem_response(
            ErrorCode.VALIDATION_ERROR,
            HTTPStatus.UNPROCESSABLE_CONTENT,
            params={"errors": _validation_errors(exc.errors())},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Errores HTTP del framework (ruta inexistente, método no permitido...)."""
        return problem_response(
            _http_error_code(exc.status_code), exc.status_code, headers=exc.headers
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Error no controlado en %s %s", request.method, request.url.path, exc_info=exc)
        return problem_response(ErrorCode.INTERNAL_ERROR, HTTPStatus.INTERNAL_SERVER_ERROR)


class RequestTooLargeProblemMiddleware:
    """Convierte el 413 en texto plano de Starlette en Problem Details (`REQUEST_TOO_LARGE`).

    El límite de body de FastAPI/Starlette responde por su cuenta, sin pasar por los handlers,
    cuando `Content-Length` ya supera el límite. Debe registrarse *dentro* de CORS para que la
    respuesta reescrita conserve las cabeceras CORS.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        replaced = False

        async def send_problem(message: Message) -> None:
            nonlocal replaced
            if message["type"] == "http.response.start" and _is_plain_413(message):
                replaced = True
                response = problem_response(
                    ErrorCode.REQUEST_TOO_LARGE, HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                )
                await response(scope, receive, send)
                return
            if replaced and message["type"] == "http.response.body":
                return
            await send(message)

        await self.app(scope, receive, send_problem)


def _is_plain_413(message: Message) -> bool:
    if message["status"] != HTTPStatus.REQUEST_ENTITY_TOO_LARGE:
        return False
    headers = dict(message.get("headers", []))
    content_type: bytes = headers.get(b"content-type", b"")
    return content_type.startswith(b"text/plain")
