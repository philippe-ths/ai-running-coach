"""HTTP basic-auth middleware for Phase 1 deployment.

Gates everything except an explicit exempt list (currently `/api/health` and
`/api/webhooks/*`). Designed to be removed wholesale in Phase 2 when the
magic-link session auth from ADR 0005 lands.

TODO(phase-2): remove when session auth lands.
"""

from __future__ import annotations

import base64
import binascii
import logging
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.config import settings

logger = logging.getLogger(__name__)

_REALM = "running-coach"
_UNAUTH_HEADERS = {"WWW-Authenticate": f'Basic realm="{_REALM}", charset="UTF-8"'}


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Block requests that do not present matching HTTP basic credentials.

    Outside production the middleware is a no-op when either credential is
    unset, so local development without secrets continues to work. In
    production a missing credential fails closed (503), because a typo during
    secret rotation or a missed secret on first deploy would otherwise
    expose every gated route silently.
    """

    def __init__(self, app: ASGIApp, exempt_prefixes: tuple[str, ...]) -> None:
        super().__init__(app)
        self._exempt_prefixes = tuple(exempt_prefixes)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if self._is_exempt(path):
            return await call_next(request)

        expected_user = settings.BASIC_AUTH_USER
        expected_password = settings.BASIC_AUTH_PASSWORD
        if not expected_user or not expected_password:
            if settings.APP_ENV == "production":
                logger.critical(
                    "basic_auth_credentials_missing_in_production",
                    extra={"path": path},
                )
                return _service_unavailable()
            return await call_next(request)

        credentials = _parse_basic_auth(request.headers.get("authorization"))
        if credentials is None:
            return _unauthorized()

        user, password = credentials
        user_ok = secrets.compare_digest(user.encode("utf-8"), expected_user.encode("utf-8"))
        pass_ok = secrets.compare_digest(password.encode("utf-8"), expected_password.encode("utf-8"))
        if not (user_ok and pass_ok):
            return _unauthorized()

        return await call_next(request)

    def _is_exempt(self, path: str) -> bool:
        return any(
            path == prefix or path.startswith(prefix + "/")
            for prefix in self._exempt_prefixes
        )


def _parse_basic_auth(header_value: str | None) -> tuple[str, str] | None:
    if not header_value:
        return None
    parts = header_value.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(parts[1], validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if ":" not in decoded:
        return None
    user, _, password = decoded.partition(":")
    return user, password


def _unauthorized() -> Response:
    return Response(status_code=401, headers=_UNAUTH_HEADERS, content="Unauthorized")


def _service_unavailable() -> Response:
    return Response(status_code=503, content="Service Unavailable: auth misconfigured")
