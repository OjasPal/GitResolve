"""Shared error type and the `{"error": {"code", "message"}}` response shape."""

import logging
import uuid
import re
from dataclasses import dataclass
from app.core.redaction import redact_value


@dataclass(frozen=True)
class PublicError:
    category: str
    message: str
    incident_id: str


class PersistenceError(OSError):
    """A local transaction could not be durably committed."""


def public_error(error: Exception) -> PublicError:
    incident = uuid.uuid4().hex
    category, message = "internal_error", "An unexpected error interrupted this operation."
    if isinstance(error, TimeoutError):
        category, message = "timeout", "The operation took too long."
    elif isinstance(error, (PermissionError, PersistenceError, OSError)):
        category, message = "storage_unavailable", "Local storage is temporarily unavailable."

    logging.getLogger("gitresolve.errors").error(
        "incident=%s category=%s", incident, category,
        exc_info=(type(error), error, error.__traceback__),
    )
    return PublicError(category, f"{message} Reference: {incident}.", incident)


def safe_message(value: str, max_chars: int | None = None) -> str:
    """Scrub text; infrastructure exception renderings become incident references.

    Assertion failures and repository-relative test diagnostics are retained.
    """
    from app.core.redaction import redact
    pattern = r"(?m)^.*(?:(?:PermissionError|FileNotFoundError|ConnectionError|PersistenceError|OSError)\s*:|WinError \d+|Traceback \(most recent call last\)).*$"
    value = re.sub(pattern, lambda match: public_error(RuntimeError(match.group())).message, value)
    return redact(value, max_chars)


from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


# Canonical constructors keep codes consistent across routes and services.
def not_found() -> ApiError:
    # Same response for missing and cross-user resources (AFD section 3).
    return ApiError(404, "not_found", "This item doesn't exist or you don't have access.")


def invalid_state(message: str) -> ApiError:
    return ApiError(409, "invalid_state", message)


def unauthorized(message: str = "Authentication required.") -> ApiError:
    return ApiError(401, "unauthorized", message)


def _body(code: str, message: str, details: dict | None = None) -> dict:
    body: dict = {"code": code, "message": safe_message(message)}
    if details:
        body["details"] = details
    return redact_value({"error": body})


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            _body(exc.code, exc.message, exc.details),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Backend Schema section 6: shared 400 format instead of FastAPI's 422.
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        detail = first.get("msg", "Invalid request.")
        message = f"{loc}: {detail}" if loc else detail
        return JSONResponse(_body("validation_error", message), status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, __: Exception) -> JSONResponse:
        # Never leak stack traces or internals to clients.
        return JSONResponse(
            _body("internal_error", public_error(__).message), status_code=500
        )
