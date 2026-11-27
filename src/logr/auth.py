"""Authentication for the web API: local single-user mode, or GitHub/Google OAuth.

CONTRACT (Phase 4). Signatures and semantics here are fixed; ``logr/api/`` is
written against them. Behaviour of the HTTP endpoints is specified in
docs/API.md ("Auth" section).

The FastAPI app stores an ``AuthConfig`` on ``app.state.auth`` and the SQLite
path on ``app.state.db_path``; the dependencies below read them from
``request.app.state``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

SESSION_COOKIE = "logr_session"
STATE_COOKIE = "logr_oauth_state"
SESSION_MAX_AGE = 30 * 24 * 3600
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "logr"


class User(BaseModel):
    id: str  # "local", "github:<id>", "google:<sub>"
    provider: Literal["local", "github", "google"]
    login: str
    name: str
    avatar_url: str | None = None


LOCAL_USER = User(id="local", provider="local", login="local", name="Local user")


@dataclass
class OAuthProvider:
    name: Literal["github", "google"]
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scope: str


@dataclass
class AuthConfig:
    mode: Literal["local", "oauth"] = "local"
    session_secret: str | None = None
    base_url: str = "http://127.0.0.1:8000"
    providers: dict[str, OAuthProvider] = field(default_factory=dict)
    admins: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AuthConfig:
        """Read LOGR_AUTH, LOGR_SESSION_SECRET, LOGR_BASE_URL, LOGR_ADMINS and the
        LOGR_{GITHUB,GOOGLE}_CLIENT_{ID,SECRET} variables (``env`` defaults to
        ``os.environ``). A provider is configured only if both its id and secret
        are set. Raises ValueError for: unknown LOGR_AUTH; oauth mode without
        LOGR_SESSION_SECRET (or shorter than 32 chars); oauth mode with no
        provider configured."""
        raise NotImplementedError

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")


def ensure_users_table(db_path: str) -> None:
    """Create the ``users`` table if missing: id PK, provider, login, name,
    avatar_url, created_at, last_login_at."""
    raise NotImplementedError


def current_user(request: Request) -> User | None:
    """FastAPI dependency. ``LOCAL_USER`` in local mode; in oauth mode the user
    from a valid session cookie (looked up in the users table), else None.
    A tampered/expired cookie counts as no session."""
    raise NotImplementedError


def require_user(request: Request) -> User:
    """FastAPI dependency: ``current_user`` or HTTP 401 {"detail": "login required"}."""
    raise NotImplementedError


def require_admin(request: Request) -> User:
    """FastAPI dependency: 401 if no user, 403 unless the user is an admin
    (local user in local mode; ids in ``AuthConfig.admins`` in oauth mode)."""
    raise NotImplementedError


def is_admin(user: User | None, config: AuthConfig) -> bool:
    raise NotImplementedError


def require_csrf(request: Request) -> None:
    """FastAPI dependency for every non-GET route: HTTP 403 unless the request
    carries ``X-Requested-With: logr``. (Applied app-wide by the API.)"""
    raise NotImplementedError


# Routes: GET /api/me, GET /api/auth/{provider}/login, GET /api/auth/{provider}/callback,
# POST /api/auth/logout — see docs/API.md. The app includes this router as-is.
router = APIRouter()
