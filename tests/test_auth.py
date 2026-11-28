"""Auth: config parsing, local mode, OAuth flow (mocked providers), sessions, CSRF."""
from __future__ import annotations

import sqlite3
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

from logr import auth
from logr.auth import (
    CSRF_HEADER,
    CSRF_VALUE,
    LOCAL_USER,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    STATE_COOKIE,
    AuthConfig,
    User,
    current_user,
    ensure_users_table,
    is_admin,
    require_admin,
    require_csrf,
    require_user,
    router,
)

SECRET = "s" * 40
CSRF = {CSRF_HEADER: CSRF_VALUE}
GH_ENV = {"LOGR_GITHUB_CLIENT_ID": "gh-id", "LOGR_GITHUB_CLIENT_SECRET": "gh-secret"}
GO_ENV = {"LOGR_GOOGLE_CLIENT_ID": "go-id", "LOGR_GOOGLE_CLIENT_SECRET": "go-secret"}


def oauth_env(**extra: str) -> dict[str, str]:
    return {"LOGR_AUTH": "oauth", "LOGR_SESSION_SECRET": SECRET, **GH_ENV, **GO_ENV, **extra}


# ------------------------------------------------------------------ from_env


def test_from_env_defaults_to_local():
    c = AuthConfig.from_env({})
    assert c.mode == "local" and c.providers == {} and c.admins == frozenset()
    assert c.base_url == "http://127.0.0.1:8000" and not c.secure_cookies


def test_from_env_local_ignores_providers():
    c = AuthConfig.from_env({"LOGR_AUTH": "local", **GH_ENV})
    assert c.mode == "local" and c.providers == {}


def test_from_env_github_only():
    c = AuthConfig.from_env({"LOGR_AUTH": "oauth", "LOGR_SESSION_SECRET": SECRET, **GH_ENV})
    assert list(c.providers) == ["github"]
    gh = c.providers["github"]
    assert (gh.client_id, gh.client_secret, gh.scope) == ("gh-id", "gh-secret", "read:user")
    assert gh.authorize_url == "https://github.com/login/oauth/authorize"
    assert gh.token_url == "https://github.com/login/oauth/access_token"
    assert gh.userinfo_url == "https://api.github.com/user"


def test_from_env_google_only():
    c = AuthConfig.from_env({"LOGR_AUTH": "oauth", "LOGR_SESSION_SECRET": SECRET, **GO_ENV})
    assert list(c.providers) == ["google"]
    go = c.providers["google"]
    assert go.scope == "openid email profile"
    assert go.authorize_url == "https://accounts.google.com/o/oauth2/v2/auth"
    assert go.token_url == "https://oauth2.googleapis.com/token"
    assert go.userinfo_url == "https://openidconnect.googleapis.com/v1/userinfo"


def test_from_env_both_admins_base_url():
    c = AuthConfig.from_env(oauth_env(
        LOGR_ADMINS=" github:1, ,google:abc ", LOGR_BASE_URL="https://logr.example.com/"))
    assert sorted(c.providers) == ["github", "google"]
    assert c.admins == frozenset({"github:1", "google:abc"})
    assert c.base_url == "https://logr.example.com" and c.secure_cookies


def test_from_env_provider_needs_id_and_secret():
    env = {"LOGR_AUTH": "oauth", "LOGR_SESSION_SECRET": SECRET, **GH_ENV,
           "LOGR_GOOGLE_CLIENT_ID": "go-id"}
    assert list(AuthConfig.from_env(env).providers) == ["github"]


@pytest.mark.parametrize("env", [
    {"LOGR_AUTH": "magic"},
    {"LOGR_AUTH": "oauth", **GH_ENV},
    {"LOGR_AUTH": "oauth", "LOGR_SESSION_SECRET": "short", **GH_ENV},
    {"LOGR_AUTH": "oauth", "LOGR_SESSION_SECRET": SECRET},
    {"LOGR_AUTH": "oauth", "LOGR_SESSION_SECRET": SECRET, "LOGR_GITHUB_CLIENT_ID": "x"},
])
def test_from_env_errors(env):
    with pytest.raises(ValueError):
        AuthConfig.from_env(env)


def test_from_env_reads_os_environ(monkeypatch):
    for k in ("LOGR_AUTH", "LOGR_SESSION_SECRET", "LOGR_ADMINS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LOGR_BASE_URL", "https://x.test")
    assert AuthConfig.from_env().base_url == "https://x.test"


# ------------------------------------------------------------------ test app


def make_app(tmp_path, config: AuthConfig) -> FastAPI:
    app = FastAPI()
    db_path = str(tmp_path / "logr.db")
    app.state.auth = config
    app.state.db_path = db_path
    ensure_users_table(db_path)
    app.include_router(router)

    @app.get("/t/whoami")
    def whoami(user: User | None = Depends(current_user)):
        return {"user": user.id if user else None}

    @app.get("/t/private")
    def private(user: User = Depends(require_user)):
        return {"user": user.id}

    @app.get("/t/admin")
    def admin(user: User = Depends(require_admin)):
        return {"user": user.id}

    @app.post("/t/write", dependencies=[Depends(require_csrf)])
    def write():
        return {"ok": True}

    return app


def client_for(app: FastAPI, base_url: str = "http://testserver") -> TestClient:
    return TestClient(app, base_url=base_url, follow_redirects=False)


@pytest.fixture
def oauth_app(tmp_path):
    return make_app(tmp_path, AuthConfig.from_env(oauth_env(LOGR_ADMINS="github:42")))


def fake_providers(monkeypatch, *, github=None, google=None, token_status=200, calls=None):
    """Route provider HTTP calls to an httpx.MockTransport."""
    github = github or {"id": 42, "login": "octo", "name": "Octo Cat",
                        "avatar_url": "https://avatars.example/42"}
    google = google or {"sub": "g-123", "email": "a@example.com", "name": "Ada",
                        "picture": "https://pics.example/a"}
    calls = calls if calls is not None else []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        url = str(req.url)
        if url in ("https://github.com/login/oauth/access_token", "https://oauth2.googleapis.com/token"):
            if token_status != 200:
                return httpx.Response(token_status, json={"error": "bad_verification_code"})
            return httpx.Response(200, json={"access_token": "tok", "token_type": "bearer"})
        assert req.headers["Authorization"] == "Bearer tok"
        if url == "https://api.github.com/user":
            return httpx.Response(200, json=github)
        if url == "https://openidconnect.googleapis.com/v1/userinfo":
            return httpx.Response(200, json=google)
        raise AssertionError(f"unexpected request {url}")

    monkeypatch.setattr(auth, "_http_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return calls


def start_login(client: TestClient, provider: str, next_: str | None = "/tracked"):
    params = {"next": next_} if next_ is not None else {}
    r = client.get(f"/api/auth/{provider}/login", params=params)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    return r, q


def sign_in(client: TestClient, provider: str = "github", next_: str | None = "/tracked"):
    _, q = start_login(client, provider, next_)
    return client.get(f"/api/auth/{provider}/callback",
                      params={"code": "abc", "state": q["state"][0]})


def users(app: FastAPI) -> dict[str, sqlite3.Row]:
    db = sqlite3.connect(app.state.db_path)
    db.row_factory = sqlite3.Row
    try:
        return {r["id"]: r for r in db.execute("SELECT * FROM users")}
    finally:
        db.close()


# ------------------------------------------------------------------ local mode


def test_local_mode(tmp_path):
    c = client_for(make_app(tmp_path, AuthConfig()))
    assert c.get("/api/me").json() == {
        "user": LOCAL_USER.model_dump(), "auth_mode": "local", "providers": [], "is_admin": True,
    }
    assert c.get("/t/private").json() == {"user": "local"}
    assert c.get("/t/admin").status_code == 200
    assert c.get("/api/auth/github/login").status_code == 404
    assert c.get("/api/auth/github/callback", params={"code": "x", "state": "y"}).status_code == 404
    assert c.post("/api/auth/logout", headers=CSRF).status_code == 204


def test_is_admin():
    local, oauth = AuthConfig(), AuthConfig(mode="oauth", admins=frozenset({"github:1"}))
    gh = User(id="github:1", provider="github", login="a", name="a")
    other = User(id="github:2", provider="github", login="b", name="b")
    assert is_admin(LOCAL_USER, local) and not is_admin(None, local)
    assert is_admin(gh, oauth) and not is_admin(other, oauth) and not is_admin(None, oauth)
    assert not is_admin(LOCAL_USER, oauth)


# ------------------------------------------------------------------ oauth login


def test_me_anonymous(oauth_app):
    assert client_for(oauth_app).get("/api/me").json() == {
        "user": None, "auth_mode": "oauth", "providers": ["github", "google"], "is_admin": False,
    }


def test_login_redirect_github(oauth_app):
    r, q = start_login(client_for(oauth_app), "github")
    loc = urlparse(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == "https://github.com/login/oauth/authorize"
    assert q["client_id"] == ["gh-id"] and q["scope"] == ["read:user"]
    assert q["redirect_uri"] == ["http://127.0.0.1:8000/api/auth/github/callback"]
    assert len(q["state"][0]) >= 32
    set_cookie = r.headers["set-cookie"]
    assert set_cookie.startswith(f"{STATE_COOKIE}=")
    assert "HttpOnly" in set_cookie and "samesite=lax" in set_cookie.lower()
    assert "Path=/" in set_cookie and "Secure" not in set_cookie


def test_login_redirect_google(oauth_app):
    r, q = start_login(client_for(oauth_app), "google")
    assert r.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert q["client_id"] == ["go-id"] and q["scope"] == ["openid email profile"]
    assert q["response_type"] == ["code"]
    assert q["redirect_uri"] == ["http://127.0.0.1:8000/api/auth/google/callback"]


def test_login_unknown_or_unconfigured_provider(tmp_path):
    app = make_app(tmp_path, AuthConfig.from_env(
        {"LOGR_AUTH": "oauth", "LOGR_SESSION_SECRET": SECRET, **GH_ENV}))
    c = client_for(app)
    for p in ("google", "facebook"):
        r = c.get(f"/api/auth/{p}/login")
        assert r.status_code == 404 and "detail" in r.json()


@pytest.mark.parametrize("unsafe", [
    "//evil.example/x", "https://evil.example", "evil", "/\\evil.example", "/a\nb", "",
])
def test_unsafe_next_is_sanitized(oauth_app, monkeypatch, unsafe):
    fake_providers(monkeypatch)
    r = sign_in(client_for(oauth_app), next_=unsafe)
    assert r.status_code == 302 and r.headers["location"] == "/"


def test_callback_github_happy_path(oauth_app, monkeypatch):
    calls = fake_providers(monkeypatch)
    c = client_for(oauth_app)
    r = sign_in(c, "github", "/tracked?x=1")
    assert r.status_code == 302 and r.headers["location"] == "/tracked?x=1"
    cookies = r.headers.get_list("set-cookie")
    session = next(h for h in cookies if h.startswith(f"{SESSION_COOKIE}="))
    assert "HttpOnly" in session and f"Max-Age={SESSION_MAX_AGE}" in session
    assert any(h.startswith(f"{STATE_COOKIE}=") and "Max-Age=0" in h for h in cookies)

    # token exchange sent the right form + Accept header
    token_req = calls[0]
    assert token_req.method == "POST" and token_req.headers["Accept"] == "application/json"
    form = parse_qs(token_req.content.decode())
    assert form["code"] == ["abc"] and form["client_secret"] == ["gh-secret"]
    assert form["redirect_uri"] == ["http://127.0.0.1:8000/api/auth/github/callback"]

    row = users(oauth_app)["github:42"]
    assert (row["provider"], row["login"], row["name"]) == ("github", "octo", "Octo Cat")
    assert row["avatar_url"] == "https://avatars.example/42" and row["last_login_at"]
    me = c.get("/api/me").json()
    assert me["user"] == {"id": "github:42", "provider": "github", "login": "octo",
                          "name": "Octo Cat", "avatar_url": "https://avatars.example/42"}
    assert me["is_admin"] is True  # LOGR_ADMINS=github:42
    assert c.get("/t/admin").status_code == 200
    assert STATE_COOKIE not in c.cookies


def test_callback_google_happy_path(oauth_app, monkeypatch):
    fake_providers(monkeypatch, google={"sub": "g-1", "email": "a@example.com", "picture": None})
    c = client_for(oauth_app)
    r = sign_in(c, "google", None)
    assert r.status_code == 302 and r.headers["location"] == "/"
    me = c.get("/api/me").json()
    assert me["user"] == {"id": "google:g-1", "provider": "google", "login": "a@example.com",
                          "name": "a@example.com", "avatar_url": None}
    assert me["is_admin"] is False
    assert c.get("/t/private").json() == {"user": "google:g-1"}
    assert c.get("/t/admin").status_code == 403


def test_relogin_updates_profile(oauth_app, monkeypatch):
    fake_providers(monkeypatch, github={"id": 7, "login": "old", "name": None, "avatar_url": "a"})
    c = client_for(oauth_app)
    sign_in(c)
    first = users(oauth_app)["github:7"]
    assert first["name"] == "old"
    fake_providers(monkeypatch, github={"id": 7, "login": "new", "name": "New Name", "avatar_url": "b"})
    sign_in(client_for(oauth_app))
    row = users(oauth_app)["github:7"]
    assert (row["login"], row["name"], row["avatar_url"]) == ("new", "New Name", "b")
    assert row["created_at"] == first["created_at"]
    assert len(users(oauth_app)) == 1


# ------------------------------------------------------------------ callback errors


def test_callback_bad_state(oauth_app, monkeypatch):
    calls = fake_providers(monkeypatch)
    c = client_for(oauth_app)
    start_login(c, "github")
    r = c.get("/api/auth/github/callback", params={"code": "abc", "state": "wrong"})
    assert r.status_code == 400 and "detail" in r.json()
    r = c.get("/api/auth/github/callback", params={"code": "abc"})
    assert r.status_code == 400
    assert calls == []  # never contacted the provider


def test_callback_missing_state_cookie(oauth_app, monkeypatch):
    fake_providers(monkeypatch)
    r = client_for(oauth_app).get("/api/auth/github/callback", params={"code": "a", "state": "b"})
    assert r.status_code == 400 and "detail" in r.json()


def test_callback_tampered_state_cookie(oauth_app, monkeypatch):
    fake_providers(monkeypatch)
    c = client_for(oauth_app)
    _, q = start_login(c, "github")
    c.cookies.set(STATE_COOKIE, c.cookies[STATE_COOKIE] + "x")
    r = c.get("/api/auth/github/callback", params={"code": "a", "state": q["state"][0]})
    assert r.status_code == 400


def test_callback_state_from_other_provider(oauth_app, monkeypatch):
    fake_providers(monkeypatch)
    c = client_for(oauth_app)
    _, q = start_login(c, "github")
    r = c.get("/api/auth/google/callback", params={"code": "a", "state": q["state"][0]})
    assert r.status_code == 400


def test_callback_provider_error(oauth_app, monkeypatch):
    fake_providers(monkeypatch)
    c = client_for(oauth_app)
    _, q = start_login(c, "github")
    r = c.get("/api/auth/github/callback", params={"error": "access_denied", "state": q["state"][0]})
    assert r.status_code == 400 and "access_denied" in r.json()["detail"]
    r = c.get("/api/auth/github/callback", params={"state": q["state"][0]})  # no code
    assert r.status_code == 400


def test_callback_token_exchange_fails(oauth_app, monkeypatch):
    fake_providers(monkeypatch, token_status=401)
    r = sign_in(client_for(oauth_app))
    assert r.status_code == 400 and "detail" in r.json()
    assert users(oauth_app) == {}


def test_callback_network_error(oauth_app, monkeypatch):
    def boom(req):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(auth, "_http_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(boom)))
    assert sign_in(client_for(oauth_app)).status_code == 400


# ------------------------------------------------------------------ sessions


def session_for(uid: str, secret: str = SECRET) -> str:
    return URLSafeTimedSerializer(secret, salt="logr-session").dumps({"uid": uid})


def test_session_cookie_roundtrip_and_tampering(oauth_app, monkeypatch):
    fake_providers(monkeypatch)
    sign_in(client_for(oauth_app))  # creates github:42
    c = client_for(oauth_app)
    c.cookies.set(SESSION_COOKIE, session_for("github:42"))
    assert c.get("/t/whoami").json() == {"user": "github:42"}
    c.cookies.set(SESSION_COOKIE, session_for("github:42")[:-2] + "zz")
    assert c.get("/t/whoami").json() == {"user": None}
    c.cookies.set(SESSION_COOKIE, session_for("github:42", secret="o" * 40))
    assert c.get("/t/whoami").json() == {"user": None}
    c.cookies.set(SESSION_COOKIE, "garbage")
    assert c.get("/t/whoami").json() == {"user": None}


def test_session_for_unknown_or_deleted_user(oauth_app, monkeypatch):
    fake_providers(monkeypatch)
    c = client_for(oauth_app)
    sign_in(c)
    assert c.get("/t/whoami").json() == {"user": "github:42"}
    db = sqlite3.connect(oauth_app.state.db_path)
    db.execute("DELETE FROM users")
    db.commit()
    db.close()
    assert c.get("/t/whoami").json() == {"user": None}


def test_expired_session(oauth_app, monkeypatch):
    fake_providers(monkeypatch)
    sign_in(client_for(oauth_app))
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() - SESSION_MAX_AGE - 60)
    old = session_for("github:42")
    monkeypatch.setattr(time, "time", real)
    c = client_for(oauth_app)
    c.cookies.set(SESSION_COOKIE, old)
    assert c.get("/t/whoami").json() == {"user": None}
    # still valid just inside the window
    monkeypatch.setattr(time, "time", lambda: real() - SESSION_MAX_AGE + 60)
    fresh_enough = session_for("github:42")
    monkeypatch.setattr(time, "time", real)
    c.cookies.set(SESSION_COOKIE, fresh_enough)
    assert c.get("/t/whoami").json() == {"user": "github:42"}


def test_logout_clears_cookie(oauth_app, monkeypatch):
    fake_providers(monkeypatch)
    c = client_for(oauth_app)
    sign_in(c)
    assert c.post("/api/auth/logout").status_code == 403  # CSRF header required
    r = c.post("/api/auth/logout", headers=CSRF)
    assert r.status_code == 204
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f'{SESSION_COOKIE}=""') or cookie.startswith(f"{SESSION_COOKIE}=;")
    assert "Max-Age=0" in cookie
    assert c.get("/api/me").json()["user"] is None


def test_require_user_and_admin_anonymous(oauth_app):
    c = client_for(oauth_app)
    r = c.get("/t/private")
    assert r.status_code == 401 and r.json() == {"detail": "login required"}
    assert c.get("/t/admin").status_code == 401


def test_secure_cookies_on_https(tmp_path, monkeypatch):
    fake_providers(monkeypatch)
    app = make_app(tmp_path, AuthConfig.from_env(oauth_env(LOGR_BASE_URL="https://logr.example.com")))
    c = client_for(app, base_url="https://testserver")
    r, q = start_login(c, "github")
    assert "Secure" in r.headers["set-cookie"]
    assert q["redirect_uri"] == ["https://logr.example.com/api/auth/github/callback"]
    r = c.get("/api/auth/github/callback", params={"code": "abc", "state": q["state"][0]})
    assert r.status_code == 302
    assert all("Secure" in h for h in r.headers.get_list("set-cookie"))


# ------------------------------------------------------------------ CSRF


def test_csrf(tmp_path):
    c = client_for(make_app(tmp_path, AuthConfig()))
    r = c.post("/t/write")
    assert r.status_code == 403
    assert r.json() == {"detail": "missing X-Requested-With: logr header"}
    assert c.post("/t/write", headers={CSRF_HEADER: "other"}).status_code == 403
    assert c.post("/t/write", headers=CSRF).json() == {"ok": True}
