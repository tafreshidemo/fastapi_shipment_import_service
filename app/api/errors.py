from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class ApiError(Exception):
    """Structured application error returned by the API."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = HTTPStatus.BAD_REQUEST,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


def register_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(ApiError)
    async def handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        payload = ErrorResponse(
            error=ErrorBody(code=exc.code, message=exc.message, details=exc.details)
        )
        return JSONResponse(status_code=exc.status_code, content=payload.model_dump())

    @application.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        payload = ErrorResponse(
            error=ErrorBody(
                code="INVALID_REQUEST",
                message="Request validation failed.",
                details=exc.errors(),
            )
        )
        return JSONResponse(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            content=payload.model_dump(),
        )

    @application.exception_handler(StarletteHTTPException)
    async def handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        payload = ErrorResponse(
            error=ErrorBody(
                code="HTTP_ERROR",
                message=str(exc.detail) if exc.detail else HTTPStatus(exc.status_code).phrase,
                details=None,
            )
        )
        return JSONResponse(status_code=exc.status_code, content=payload.model_dump())

    @application.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled API exception for %s %s", request.method, request.url.path)
        payload = ErrorResponse(
            error=ErrorBody(
                code="INTERNAL_ERROR",
                message="An internal server error occurred.",
                details=None,
            )
        )
        return JSONResponse(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            content=payload.model_dump(),
        )
