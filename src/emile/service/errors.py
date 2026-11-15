"""Map domain errors to HTTP responses with a stable ``error`` code, so clients can branch without parsing text."""

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import JSONResponse

from emile.core import InvalidInput, ItemNotFound, LimitExceeded, NoCurrentList, StaleList
from emile.service.accounts import AccountError
from emile.service.repository import Busy

# (exception, HTTP status, error code)
DOMAIN_ERRORS: list[tuple[type[Exception], int, str]] = [
    (StaleList, 409, "stale_list"),
    (NoCurrentList, 404, "no_current_list"),
    (ItemNotFound, 404, "not_found"),
    (LimitExceeded, 422, "limit_exceeded"),
    (InvalidInput, 422, "invalid_input"),
    (AccountError, 400, "account_error"),
    (Busy, 503, "busy"),
]

_HTTP_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict"}


def _body(error: str, detail: object) -> dict[str, object]:
    return {"error": error, "detail": detail}


def install(app: FastAPI) -> None:
    for exc_type, status_code, code in DOMAIN_ERRORS:

        def handler(request: Request, exc: Exception, status_code: int = status_code, code: str = code) -> JSONResponse:
            return JSONResponse(_body(code, str(exc)), status_code=status_code)

        app.add_exception_handler(exc_type, handler)

    def http_handler(request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, HTTPException):  # registered for HTTPException only
            raise exc
        code = _HTTP_CODES.get(exc.status_code, "error")
        return JSONResponse(_body(code, exc.detail), status_code=exc.status_code, headers=exc.headers)

    def validation_handler(request: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, RequestValidationError):
            raise exc
        return JSONResponse(_body("validation_error", jsonable_encoder(exc.errors())), status_code=422)

    app.add_exception_handler(HTTPException, http_handler)
    app.add_exception_handler(RequestValidationError, validation_handler)
