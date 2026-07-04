"""Problem responses: every non-2xx body this API emits.

Clients switch on `code`, never on prose; codes are stable, and the sets are
open for addition. The handlers normalize FastAPI's own error shapes
(HTTPException, validation errors) so nothing but problem+json ever leaves
the app - infrastructure-level errors (a proxy answering for a dead backend)
are the client's one non-problem branch.
"""

from __future__ import annotations

from enum import StrEnum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_MEDIA = "application/problem+json"


class ProblemCode(StrEnum):
    """Machine-readable error codes; add members freely, never repurpose one."""

    NOT_AUTHENTICATED = "not_authenticated"
    NOT_FOUND = "not_found"
    ALREADY_MEMBER = "already_member"
    NOT_A_MEMBER = "not_a_member"
    MEMBER_NOT_FOUND = "member_not_found"
    UNKNOWN_PERSONA = "unknown_persona"
    NOT_CHAPTER_LEADER = "not_chapter_leader"
    AUTH_NOT_IMPLEMENTED = "auth_not_implemented"
    INVALID_REQUEST = "invalid_request"
    FORBIDDEN_ORIGIN = "forbidden_origin"
    INTERNAL = "internal"


class AppProblem(Exception):
    """A typed, client-visible failure; the handler renders it as problem+json."""

    def __init__(
        self, status: int, code: ProblemCode, title: str, detail: str | None = None
    ) -> None:
        super().__init__(title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail


def _render(
    status: int, code: ProblemCode, title: str, detail: str | None
) -> JSONResponse:
    body: dict[str, object] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "code": str(code),
    }
    if detail is not None:
        body["detail"] = detail
    return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA)


def install_handlers(app: FastAPI) -> None:
    """Route every error shape FastAPI produces through one problem renderer."""

    @app.exception_handler(AppProblem)
    async def _app_problem(request: Request, exc: AppProblem) -> JSONResponse:
        return _render(exc.status, exc.code, exc.title, exc.detail)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = ProblemCode.NOT_FOUND if exc.status_code == 404 else ProblemCode.INTERNAL
        return _render(exc.status_code, code, str(exc.detail), None)

    @app.exception_handler(RequestValidationError)
    async def _validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _render(
            422, ProblemCode.INVALID_REQUEST, "request body failed validation", str(exc)
        )
