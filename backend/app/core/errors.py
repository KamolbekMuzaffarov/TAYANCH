from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

log = logging.getLogger("perinatal.errors")


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, details: list | None = None):
        self.status, self.code, self.message, self.details = status, code, message, details or []


def forbidden(message: str = "Ruxsat yo‘q / Access denied") -> AppError:
    return AppError(403, "forbidden", message)


def not_found(what: str = "resource") -> AppError:
    # Same response for "missing" and "not allowed" where existence itself is sensitive (anti-enumeration).
    return AppError(404, "not_found", f"{what} not found")


def _envelope(request: Request, status: int, code: str, message: str, details: list | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {
        "code": code, "message": message, "details": details or [],
        "request_id": getattr(request.state, "request_id", None)}})


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return _envelope(request, exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
                413: "payload_too_large", 429: "rate_limited"}.get(exc.status_code, "http_error")
        return _envelope(request, exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        # Report field locations and error types only - never echo the submitted input.
        details = [{"loc": list(e.get("loc", [])), "type": e.get("type"), "msg": e.get("msg")} for e in exc.errors()]
        return _envelope(request, 422, "validation_error", "Request validation failed", details)

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError):
        log.warning("integrity error request_id=%s", getattr(request.state, "request_id", None))
        return _envelope(request, 409, "conflict", "Conflicting or duplicate record")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.error("unhandled %s request_id=%s", type(exc).__name__, getattr(request.state, "request_id", None))
        return _envelope(request, 500, "internal_error", "Internal server error")
