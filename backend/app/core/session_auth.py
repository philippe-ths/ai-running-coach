"""Session gate for /api/* (ADR 0005, #118).

A valid session cookie authenticates the request and pins the user id onto
``request.state`` for downstream handlers (the tenant-scoping sweep, #119,
consumes it). During the PR-A → PR-B cutover this middleware also falls back to
HTTP basic auth, so the still-basic-auth frontend keeps working while the new
session flow is exercisable. PR-B drops the basic-auth fallback and deletes the
legacy module.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from app.core.auth import evaluate_basic_auth
from app.core.config import settings
from app.services.auth import resolve_session_user_id_from_token


class SessionAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, exempt_prefixes: tuple[str, ...]) -> None:
        super().__init__(app)
        self._exempt_prefixes = tuple(exempt_prefixes)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if self._is_exempt(path):
            return await call_next(request)

        raw = request.cookies.get(settings.SESSION_COOKIE_NAME)
        if raw:
            user_id = resolve_session_user_id_from_token(raw)
            if user_id is not None:
                request.state.user_id = user_id
                return await call_next(request)

        # Transitional fallback; removed at the cutover (PR-B).
        rejection = evaluate_basic_auth(request)
        if rejection is not None:
            return rejection
        return await call_next(request)

    def _is_exempt(self, path: str) -> bool:
        return any(
            path == prefix or path.startswith(prefix + "/")
            for prefix in self._exempt_prefixes
        )
