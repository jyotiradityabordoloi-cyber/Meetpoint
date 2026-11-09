"""One error shape for the whole API: {"detail": {"code": ..., "message": ...}}."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def group_not_found() -> ApiError:
    return ApiError(404, "GROUP_NOT_FOUND", "This group doesn't exist or was deleted.")


def member_not_found() -> ApiError:
    return ApiError(404, "MEMBER_NOT_FOUND", "That person isn't in this group any more.")


def _readable(err: dict) -> str:
    loc = [str(x) for x in err.get("loc", []) if x not in ("body", "query", "path")]
    field = ".".join(loc)
    msg = err.get("msg", "Invalid value")
    msg = msg.removeprefix("Value error, ")
    return f"{field}: {msg}" if field else msg


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content={"detail": {"code": exc.code, "message": exc.message}})

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        errors = exc.errors()
        message = _readable(errors[0]) if errors else "Invalid request"
        return JSONResponse(
            status_code=422,
            content={"detail": {"code": "VALIDATION_ERROR", "message": message}},
        )
