"""Authentication for the web API: local single-user mode, or GitHub/Google OAuth.

CONTRACT (Phase 4). Signatures and semantics here are fixed; ``logr/api/`` is
written against them. Behaviour of the HTTP endpoints is specified in
docs/API.md ("Auth" section).

The FastAPI app stores an ``AuthConfig`` on ``app.state.auth`` and the SQLite
path on ``app.state.db_path``; the dependencies below read them from
``request.app.state``.
"""

from __future__ import annotations

import hmac
import os
import secrets
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer
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

STATE_MAX_AGE = 10 * 60
_SESSION_SALT = "logr-session"
_STATE_SALT = "logr-oauth-state"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_HTTP_TIMEOUT = 10.0
_MISSING = object()

# (name, authorize_url, token_url, userinfo_url, scope)
_PROVIDER_ENDPOINTS: tuple[tuple[Any, str, str, str, str], ...] = (
    ("github", "https://github.com/login/oauth/authorize",
     "https://github.com/login/oauth/access_token", "https://api.github.com/user", "read:user"),
    ("google", "https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token",
     "https://openidconnect.googleapis.com/v1/userinfo", "openid email profile"),
)

_USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    login TEXT NOT NULL,
    name TEXT NOT NULL,
    avatar_url TEXT,
    created_at TEXT NOT NULL,
    last_login_at TEXT
)
"""


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
        if env is None:
            env = os.environ

        def get(key: str) -> str:
            return (env.get(key) or "").strip()

        mode = get("LOGR_AUTH").lower() or "local"
        if mode not in ("local", "oauth"):
            raise ValueError(f"LOGR_AUTH must be 'local' or 'oauth', not {mode!r}")
        base_url = (get("LOGR_BASE_URL") or cls.base_url).rstrip("/")
        secret = get("LOGR_SESSION_SECRET") or None
        admins = frozenset(a.strip() for a in get("LOGR_ADMINS").split(",") if a.strip())
        if mode == "local":
            # No login in local mode, so no providers are offered.
            return cls(mode="local", session_secret=secret, base_url=base_url, admins=admins)

        if not secret or len(secret) < 32:
            raise ValueError("LOGR_AUTH=oauth needs LOGR_SESSION_SECRET of at least 32 characters")
        providers: dict[str, OAuthProvider] = {}
        for name, authorize, token, userinfo, scope in _PROVIDER_ENDPOINTS:
            cid = get(f"LOGR_{name.upper()}_CLIENT_ID")
            csecret = get(f"LOGR_{name.upper()}_CLIENT_SECRET")
            if cid and csecret:
                providers[name] = OAuthProvider(
                    name=name, client_id=cid, client_secret=csecret, authorize_url=authorize,
                    token_url=token, userinfo_url=userinfo, scope=scope,
                )
        if not providers:
            raise ValueError(
                "LOGR_AUTH=oauth needs at least one provider: set LOGR_GITHUB_CLIENT_ID/SECRET "
                "and/or LOGR_GOOGLE_CLIENT_ID/SECRET"
            )
        return cls(mode="oauth", session_secret=secret, base_url=base_url,
                   providers=providers, admins=admins)

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")


def ensure_users_table(db_path: str) -> None:
    """Create the ``users`` table if missing: id PK, provider, login, name,
    avatar_url, created_at, last_login_at."""
    with _connect(db_path) as db:
        db.execute(_USERS_SCHEMA)


def current_user(request: Request) -> User | None:
    """FastAPI dependency. ``LOCAL_USER`` in local mode; in oauth mode the user
    from a valid session cookie (looked up in the users table), else None.
    A tampered/expired cookie counts as no session."""
    cached = getattr(request.state, "logr_user", _MISSING)
    if cached is not _MISSING:
        return cached
    user = _resolve_user(request)
    request.state.logr_user = user
    return user


def require_user(request: Request) -> User:
    """FastAPI dependency: ``current_user`` or HTTP 401 {"detail": "login required"}."""
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "login required")
    return user


def require_admin(request: Request) -> User:
    """FastAPI dependency: 401 if no user, 403 unless the user is an admin
    (local user in local mode; ids in ``AuthConfig.admins`` in oauth mode)."""
    user = require_user(request)
    if not is_admin(user, request.app.state.auth):
        raise HTTPException(403, "admin only")
    return user


def is_admin(user: User | None, config: AuthConfig) -> bool:
    if user is None:
        return False
    if config.mode == "local":
        return user.id == LOCAL_USER.id
    return user.id in config.admins


def require_csrf(request: Request) -> None:
    """FastAPI dependency for every non-GET route: HTTP 403 unless the request
    carries ``X-Requested-With: logr``. (Applied app-wide by the API.)"""
    if request.method in _SAFE_METHODS:
        return
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise HTTPException(403, f"missing {CSRF_HEADER}: {CSRF_VALUE} header")


# ---------------------------------------------------------------- internals


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Conn:
    """sqlite3 connection as a context manager that commits and always closes."""

    def __init__(self, db_path: str) -> None:
        self.db = sqlite3.connect(str(db_path), timeout=30)
        self.db.row_factory = sqlite3.Row

    def __enter__(self) -> sqlite3.Connection:
        return self.db

    def __exit__(self, exc_type: Any, *_: Any) -> None:
        try:
            if exc_type is None:
                self.db.commit()
        finally:
            self.db.close()


def _connect(db_path: str) -> _Conn:
    return _Conn(db_path)


def _serializer(config: AuthConfig, salt: str) -> URLSafeTimedSerializer:
    if not config.session_secret:
        raise RuntimeError("session_secret is not configured")
    return URLSafeTimedSerializer(config.session_secret, salt=salt)


def _load_user(db_path: str, user_id: str) -> User | None:
    with _connect(db_path) as db:
        row = db.execute(
            "SELECT id, provider, login, name, avatar_url FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return User(**dict(row)) if row else None


def _resolve_user(request: Request) -> User | None:
    config: AuthConfig = request.app.state.auth
    if config.mode == "local":
        return LOCAL_USER
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw or not config.session_secret:
        return None
    try:
        data = _serializer(config, _SESSION_SALT).loads(raw, max_age=SESSION_MAX_AGE)
    except BadSignature:  # includes SignatureExpired
        return None
    uid = data.get("uid") if isinstance(data, dict) else None
    if not isinstance(uid, str):
        return None
    return _load_user(request.app.state.db_path, uid)


def _upsert_user(db_path: str, user: User) -> None:
    now = _now_iso()
    with _connect(db_path) as db:
        db.execute(_USERS_SCHEMA)
        db.execute(
            "INSERT INTO users (id, provider, login, name, avatar_url, created_at, last_login_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "login = excluded.login, name = excluded.name, avatar_url = excluded.avatar_url, "
            "last_login_at = excluded.last_login_at",
            (user.id, user.provider, user.login, user.name, user.avatar_url, now, now),
        )


def _safe_next(value: str | None) -> str:
    """Only same-site relative paths: "/x" yes; "//evil", "/\\evil", "https://..." no."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        return "/"
    return value


def _set_cookie(response: Response, config: AuthConfig, name: str, value: str, max_age: int) -> None:
    response.set_cookie(name, value, max_age=max_age, path="/", httponly=True,
                        samesite="lax", secure=config.secure_cookies)


def _delete_cookie(response: Response, config: AuthConfig, name: str) -> None:
    response.delete_cookie(name, path="/", httponly=True, samesite="lax",
                           secure=config.secure_cookies)


def _redirect_uri(config: AuthConfig, provider: str) -> str:
    return f"{config.base_url}/api/auth/{provider}/callback"


def _get_provider(config: AuthConfig, provider: str) -> OAuthProvider:
    if config.mode != "oauth" or provider not in config.providers:
        raise HTTPException(404, f"unknown auth provider: {provider}")
    return config.providers[provider]


def _http_client() -> httpx.Client:
    """The client used for provider calls; tests monkeypatch this."""
    return httpx.Client(timeout=_HTTP_TIMEOUT, headers={"User-Agent": "logr"})


def _fetch_profile(provider: OAuthProvider, code: str, redirect_uri: str) -> dict[str, Any]:
    """Exchange ``code`` for an access token and return the raw profile JSON.
    Raises HTTPException(400) on any provider/transport failure."""
    try:
        with _http_client() as client:
            resp = client.post(
                provider.token_url,
                data={
                    "client_id": provider.client_id,
                    "client_secret": provider.client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
            token_data = resp.json() if resp.status_code == 200 else {}
            token = token_data.get("access_token") if isinstance(token_data, dict) else None
            if not token:
                raise HTTPException(400, "could not exchange the authorization code")
            resp = client.get(provider.userinfo_url, headers={
                "Authorization": f"Bearer {token}", "Accept": "application/json",
            })
            if resp.status_code != 200:
                raise HTTPException(400, "could not fetch the user profile")
            profile = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise HTTPException(400, "could not reach the login provider") from e
    if not isinstance(profile, dict):
        raise HTTPException(400, "unexpected user profile")
    return profile


def _user_from_profile(provider: str, profile: dict[str, Any]) -> User:
    def text(key: str) -> str | None:
        v = profile.get(key)
        return str(v) if v not in (None, "") else None

    if provider == "github":
        pid, login = profile.get("id"), text("login")
        if pid is None or isinstance(pid, bool) or not str(pid).isdigit() or not login:
            raise HTTPException(400, "unexpected GitHub profile")
        return User(id=f"github:{pid}", provider="github", login=login,
                    name=text("name") or login, avatar_url=text("avatar_url"))
    sub, email = text("sub"), text("email")
    if not sub:
        raise HTTPException(400, "unexpected Google profile")
    login = email or sub
    return User(id=f"google:{sub}", provider="google", login=login,
                name=text("name") or login, avatar_url=text("picture"))


# ------------------------------------------------------------------- routes

# Routes: GET /api/me, GET /api/auth/{provider}/login, GET /api/auth/{provider}/callback,
# POST /api/auth/logout — see docs/API.md. The app includes this router as-is.
router = APIRouter()


@router.get("/api/me")
def me(request: Request) -> dict[str, Any]:
    config: AuthConfig = request.app.state.auth
    user = current_user(request)
    return {
        "user": user.model_dump() if user else None,
        "auth_mode": config.mode,
        "providers": sorted(config.providers),
        "is_admin": is_admin(user, config),
    }


@router.get("/api/auth/{provider}/login")
def login(request: Request, provider: str, next: str | None = None) -> RedirectResponse:
    config: AuthConfig = request.app.state.auth
    p = _get_provider(config, provider)
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": p.client_id,
        "redirect_uri": _redirect_uri(config, provider),
        "scope": p.scope,
        "state": state,
        "response_type": "code",
    }
    if provider == "google":
        params["prompt"] = "select_account"
    response = RedirectResponse(f"{p.authorize_url}?{urlencode(params)}", status_code=302)
    cookie = _serializer(config, _STATE_SALT).dumps(
        {"state": state, "next": _safe_next(next), "provider": provider}
    )
    _set_cookie(response, config, STATE_COOKIE, cookie, STATE_MAX_AGE)
    return response


@router.get("/api/auth/{provider}/callback")
def callback(request: Request, provider: str, code: str | None = None, state: str | None = None,
             error: str | None = None) -> RedirectResponse:
    config: AuthConfig = request.app.state.auth
    p = _get_provider(config, provider)
    if error:
        raise HTTPException(400, f"login failed: {error[:200]}")
    raw = request.cookies.get(STATE_COOKIE)
    if not raw:
        raise HTTPException(400, "missing login state (cookie expired?); please try again")
    try:
        saved = _serializer(config, _STATE_SALT).loads(raw, max_age=STATE_MAX_AGE)
    except BadSignature:
        raise HTTPException(400, "invalid or expired login state; please try again") from None
    if (
        not isinstance(saved, dict)
        or not state
        or saved.get("provider") != provider
        or not isinstance(saved.get("state"), str)
        or not hmac.compare_digest(saved["state"].encode(), state.encode())
    ):
        raise HTTPException(400, "login state mismatch; please try again")
    if not code:
        raise HTTPException(400, "missing authorization code")

    profile = _fetch_profile(p, code, _redirect_uri(config, provider))
    user = _user_from_profile(provider, profile)
    _upsert_user(request.app.state.db_path, user)

    response = RedirectResponse(_safe_next(saved.get("next")), status_code=302)
    _delete_cookie(response, config, STATE_COOKIE)
    session = _serializer(config, _SESSION_SALT).dumps({"uid": user.id})
    _set_cookie(response, config, SESSION_COOKIE, session, SESSION_MAX_AGE)
    return response


@router.post("/api/auth/logout", status_code=204, dependencies=[Depends(require_csrf)])
def logout(request: Request) -> Response:
    response = Response(status_code=204)
    _delete_cookie(response, request.app.state.auth, SESSION_COOKIE)
    return response
