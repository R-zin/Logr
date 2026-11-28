"""Phase 5 HTTP API: calendar feed, /api/feed, /api/notify, notification prefs (docs/API.md)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

import logr.auth
import logr.notify as notify
from logr.api import create_app
from logr.auth import AuthConfig, OAuthProvider, User
from logr.models import Hackathon, Mode, Source
from logr.notify import NotifierConfig, Outbox
from logr.store import Store

H = {"X-Requested-With": "logr"}
NOW = datetime.now(UTC).replace(microsecond=0)
WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/abcDEF_ghi-JKLmnop1234wxyz"
SMTP = NotifierConfig(smtp_host="smtp.example.com", smtp_user="bot@example.com", smtp_password="pw")


def d(days: float) -> datetime:
    return NOW + timedelta(days=days)


def seed() -> list[Hackathon]:
    return [
        Hackathon(source=Source.DEVFOLIO, source_id="alpha", title="Alpha ⏰ Quantum, AI; Hackathon",
                  url="https://devfolio.co/alpha", mode=Mode.ONLINE, registration_closes_at=d(5),
                  starts_at=d(10), ends_at=d(12)),
        Hackathon(source=Source.DEVPOST, source_id="gamma", title="Gamma Web Jam", url="https://devpost.com/gamma",
                  submission_deadline=d(30), approx_fields=["submission_deadline"]),
    ]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    s = Store(path)
    s.upsert_many(seed())
    s.close()
    return path


@pytest.fixture(autouse=True)
def _no_base_url_env(monkeypatch):
    monkeypatch.delenv("LOGR_BASE_URL", raising=False)


def make(db, notifier: NotifierConfig | None = None, auth: AuthConfig | None = None) -> TestClient:
    return TestClient(create_app(db, auth=auth or AuthConfig(mode="local"), notifier=notifier or NotifierConfig()))


@pytest.fixture
def client(db):
    return make(db)


def oauth_config() -> AuthConfig:
    gh = OAuthProvider(name="github", client_id="id", client_secret="secret",
                       authorize_url="https://github.com/login/oauth/authorize",
                       token_url="https://github.com/login/oauth/access_token",
                       userinfo_url="https://api.github.com/user", scope="read:user")
    return AuthConfig(mode="oauth", session_secret="x" * 32, providers={"github": gh})


USERS = {"alice": User(id="github:1", provider="github", login="alice", name="Alice"),
         "bob": User(id="github:2", provider="github", login="bob", name="Bob")}


@pytest.fixture
def oauth(db, monkeypatch):
    monkeypatch.setattr(logr.auth, "_resolve_user", lambda request: USERS.get(request.headers.get("X-Test-User")))
    return make(db, auth=oauth_config())


def token_of(url: str) -> str:
    return parse_qs(urlsplit(url).query)["token"][0]


def unfold(text: str) -> list[str]:
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    return text.replace("\r\n ", "").split("\r\n")


# --- feed URL ----------------------------------------------------------------------------


def test_feed_url_created_once_and_rotated(client):
    r = client.get("/api/feed")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"url", "webcal_url"}
    assert re.fullmatch(r"http://testserver/api/calendar\.ics\?token=[A-Za-z0-9_-]{43,}", body["url"])
    assert body["webcal_url"] == body["url"].replace("http://", "webcal://", 1)
    assert client.get("/api/feed").json() == body  # stable until rotated

    old = token_of(body["url"])
    assert client.get(f"/api/calendar.ics?token={old}").status_code == 200
    r = client.post("/api/feed/rotate", headers=H)
    assert r.status_code == 200
    new = r.json()
    assert token_of(new["url"]) != old and new["webcal_url"].startswith("webcal://testserver/")
    assert client.get("/api/feed").json() == new
    assert client.get(f"/api/calendar.ics?token={old}").status_code == 404
    assert client.get(f"/api/calendar.ics?token={token_of(new['url'])}").status_code == 200


def test_feed_base_url(db, monkeypatch):
    c = make(db, NotifierConfig(base_url="https://logr.example.com"))
    body = c.get("/api/feed").json()
    assert body["url"].startswith("https://logr.example.com/api/calendar.ics?token=")
    assert body["webcal_url"].startswith("webcal://logr.example.com/api/calendar.ics?token=")
    monkeypatch.setenv("LOGR_BASE_URL", "https://env.example.org/")
    body = make(db).get("/api/feed").json()
    assert body["url"].startswith("https://env.example.org/api/calendar.ics?token=")


def test_rotate_needs_csrf(client):
    assert client.post("/api/feed/rotate").status_code == 403
    assert client.post("/api/notify/test").status_code == 403


# --- calendar.ics --------------------------------------------------------------------------


def test_calendar_ics(client):
    client.post("/api/tracked", json={"uid": "devfolio:alpha"}, headers=H)
    client.post("/api/tracked", json={"uid": "devpost:gamma", "status": "registered"}, headers=H)
    client.post("/api/tracked/devpost:gamma/todos", json={"text": "demo video", "due_at": (d(3)).isoformat()},
                headers=H)
    url = client.get("/api/feed").json()["url"]
    r = client.get(url.replace("http://testserver", ""))
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/calendar; charset=utf-8"
    assert r.headers["cache-control"] == "private, max-age=300"
    lines = unfold(r.content.decode("utf-8"))
    assert lines[0] == "BEGIN:VCALENDAR" and lines[-2] == "END:VCALENDAR" and lines[-1] == ""
    assert "VERSION:2.0" in lines and "METHOD:PUBLISH" in lines
    assert lines.count("BEGIN:VEVENT") == lines.count("END:VEVENT") == 4
    summaries = [ln.removeprefix("SUMMARY:") for ln in lines if ln.startswith("SUMMARY:")]
    assert "⏰ Alpha ⏰ Quantum\\, AI\\; Hackathon — registration closes" in summaries
    assert "🏁 Alpha ⏰ Quantum\\, AI\\; Hackathon" in summaries
    assert "⏰ Gamma Web Jam — submission due (approx.)" in summaries
    assert "✅ Gamma Web Jam: demo video" in summaries
    assert "TRIGGER:-PT1440M" in lines and "TRIGGER:-PT180M" in lines


def test_calendar_ics_bad_tokens(client):
    client.get("/api/feed")
    for path in ("/api/calendar.ics", "/api/calendar.ics?token=", "/api/calendar.ics?token=short",
                 "/api/calendar.ics?token=" + "A" * 43):
        r = client.get(path)
        assert r.status_code == 404, path
        assert r.headers["content-type"].startswith("application/json")
        assert set(r.json()) == {"detail"}


def test_calendar_ics_oauth_without_cookie(oauth):
    alice, bob = {**H, "X-Test-User": "alice"}, {**H, "X-Test-User": "bob"}
    assert oauth.get("/api/feed").status_code == 401
    assert oauth.post("/api/feed/rotate", headers=H).status_code == 401
    oauth.post("/api/tracked", json={"uid": "devpost:gamma"}, headers=alice)
    oauth.post("/api/tracked", json={"uid": "devfolio:alpha"}, headers=bob)
    a_url, b_url = oauth.get("/api/feed", headers=alice).json()["url"], oauth.get("/api/feed", headers=bob).json()["url"]
    assert a_url != b_url
    # no session at all: the token alone is the credential, and picks the user
    a = oauth.get(f"/api/calendar.ics?token={token_of(a_url)}")
    b = oauth.get(f"/api/calendar.ics?token={token_of(b_url)}")
    assert a.status_code == b.status_code == 200
    assert "Gamma Web Jam" in a.text and "Alpha" not in a.text
    assert "Alpha" in b.text and "Gamma" not in b.text
    assert oauth.get("/api/calendar.ics").status_code == 404  # 404, not 401


# --- /api/notify ---------------------------------------------------------------------------


def test_notify_empty(client):
    assert client.get("/api/notify").json() == {
        "channels": {name: {"configured": False, "available": name == "discord", "destination": None}
                     for name in ("email", "telegram", "discord")},
        "remind_offsets": "7d,1d,3h",
        "last_pass_at": None,
    }


def test_notify_shape(db):
    c = make(db, NotifierConfig(telegram_bot_token="123:abc"))
    for key, value in [("notify_email", "Me@Example.COM"), ("notify_discord_webhook", WEBHOOK),
                       ("remind_offsets", "3h, 2d")]:
        assert c.put(f"/api/prefs/{key}", json={"value": value}, headers=H).status_code == 200
    s = Store(db)
    Outbox(s.db).set_last_pass("local", datetime(2026, 11, 28, 9, 30, tzinfo=UTC))
    s.close()
    body = c.get("/api/notify").json()
    assert body["channels"]["email"] == {"configured": True, "available": False, "destination": "Me@example.com"}
    assert body["channels"]["telegram"] == {"configured": False, "available": True, "destination": None}
    discord = body["channels"]["discord"]
    assert discord["configured"] and discord["available"]
    assert discord["destination"] == notify.mask_secret(WEBHOOK) and WEBHOOK not in str(body)
    assert discord["destination"].endswith("wxyz")
    assert body["remind_offsets"] == "2d,3h"
    assert body["last_pass_at"] == "2026-11-28T09:30:00Z"
    assert make(db, SMTP).get("/api/notify").json()["channels"]["email"]["available"] is True


def test_notify_test_needs_a_channel(client, monkeypatch):
    monkeypatch.setattr(notify, "send_test", lambda *a, **kw: pytest.fail("must not send"))
    r = client.post("/api/notify/test", headers=H)
    assert r.status_code == 400 and "channel" in r.json()["detail"]
    client.put("/api/prefs/remind_offsets", json={"value": "1d"}, headers=H)  # not a channel
    assert client.post("/api/notify/test", headers=H).status_code == 400


def test_notify_test_results(db, monkeypatch):
    c = make(db, SMTP)
    seen = {}

    def fake(prefs, config, **kw):
        seen.update(prefs=dict(prefs), config=config)
        return {"email": "ok", "discord": "HTTP 404 from Discord"}

    monkeypatch.setattr(notify, "send_test", fake)
    c.put("/api/prefs/notify_email", json={"value": "me@example.com"}, headers=H)
    c.put("/api/prefs/notify_discord_webhook", json={"value": WEBHOOK}, headers=H)
    r = c.post("/api/notify/test", headers=H)
    assert r.status_code == 200
    assert r.json() == {"results": {"email": "ok", "discord": "HTTP 404 from Discord"}}
    assert seen["prefs"]["notify_discord_webhook"] == WEBHOOK  # the real value, not the mask
    assert seen["config"] is c.app.state.notifier


def test_notify_test_with_fake_channels(db, monkeypatch):
    """The real send_test over fake channels: nothing touches the network."""
    sent = []

    class Fake:
        def __init__(self, name, fail=False):
            self.name, self.fail = name, fail

        def send(self, msg):
            if self.fail:
                raise notify.ChannelError("boom")
            sent.append((self.name, msg.kind))

    real = notify.send_test
    monkeypatch.setattr(notify, "send_test", lambda prefs, config: real(
        prefs, config, channel_factory=lambda p, cfg: [Fake("email"), Fake("discord", fail=True)]))
    c = make(db, SMTP)
    c.put("/api/prefs/notify_email", json={"value": "me@example.com"}, headers=H)
    c.put("/api/prefs/notify_telegram_chat", json={"value": "12345"}, headers=H)
    c.put("/api/prefs/notify_discord_webhook", json={"value": WEBHOOK}, headers=H)
    results = c.post("/api/notify/test", headers=H).json()["results"]
    assert results["email"] == "ok"
    assert "TELEGRAM" in results["telegram"]  # configured, but the server has no bot token
    assert results["discord"] != "ok" and WEBHOOK not in results["discord"]
    assert sent == [("email", "test")]


# --- prefs ---------------------------------------------------------------------------------


@pytest.mark.parametrize("key, good, stored, bad", [
    ("notify_email", " Me@Example.COM ", "Me@example.com", "not an email"),
    ("notify_telegram_chat", "-1001234567890", "-1001234567890", "chat 12"),
    ("notify_telegram_chat", "@mychannel", "@mychannel", "12abc"),
    ("notify_discord_webhook", WEBHOOK, None, "https://example.com/api/webhooks/1/x"),
    ("remind_offsets", "1d, 7d,3h", "7d,1d,3h", "5y"),
])
def test_notify_prefs_validated(client, key, good, stored, bad):
    r = client.put(f"/api/prefs/{key}", json={"value": good}, headers=H)
    assert r.status_code == 200, r.json()
    if stored is not None:
        assert r.json()["value"] == stored
        assert client.get("/api/prefs").json()[key]["value"] == stored
    r = client.put(f"/api/prefs/{key}", json={"value": bad}, headers=H)
    assert r.status_code == 400
    assert r.json()["detail"]
    if stored is not None:
        assert client.get("/api/prefs").json()[key]["value"] == stored  # unchanged


def test_remind_offsets_errors(client):
    for bad in ("0h", "-1d", "61d", "1d,2d,3d,4d,5d,6d,7d"):
        assert client.put("/api/prefs/remind_offsets", json={"value": bad}, headers=H).status_code == 400, bad


def test_discord_webhook_masked_and_not_written_back(client, db):
    r = client.put("/api/prefs/notify_discord_webhook", json={"value": WEBHOOK}, headers=H)
    assert r.status_code == 200 and WEBHOOK not in r.text
    prefs = client.get("/api/prefs").json()
    masked = prefs["notify_discord_webhook"]["value"]
    assert masked == notify.mask_secret(WEBHOOK) and masked != WEBHOOK and WEBHOOK not in str(prefs)
    assert prefs["notify_discord_webhook"]["description"]

    r = client.put("/api/prefs/notify_discord_webhook", json={"value": masked}, headers=H)
    assert r.status_code == 400
    assert r.json() == {"detail": "enter the full webhook URL"}
    s = Store(db)
    assert s.db.execute("SELECT value FROM preferences WHERE key='notify_discord_webhook'").fetchone()[0] == WEBHOOK
    s.close()

    other = WEBHOOK[:-4] + "zzzz"
    assert client.put("/api/prefs/notify_discord_webhook", json={"value": other}, headers=H).status_code == 200
    assert client.get("/api/prefs").json()["notify_discord_webhook"]["value"].endswith("zzzz")
    assert client.delete("/api/prefs/notify_discord_webhook", headers=H).status_code == 204
    assert client.get("/api/prefs").json()["notify_discord_webhook"]["value"] is None


def test_masked_form_rejected_even_without_stored_value(client):
    r = client.put("/api/prefs/notify_discord_webhook", json={"value": notify.mask_secret(WEBHOOK)}, headers=H)
    assert r.status_code == 400 and r.json() == {"detail": "enter the full webhook URL"}


def test_phase5_pref_puts_need_csrf(client):
    assert client.put("/api/prefs/notify_email", json={"value": "me@example.com"}).status_code == 403


def test_create_app_reads_notifier_from_env(db, monkeypatch):
    monkeypatch.setenv("LOGR_TELEGRAM_BOT_TOKEN", "123:abc")
    app = create_app(db, auth=AuthConfig(mode="local"))
    assert app.state.notifier.telegram_enabled
    assert TestClient(app).get("/api/notify").json()["channels"]["telegram"]["available"] is True
