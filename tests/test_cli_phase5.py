"""CLI for Phase 5: notify, daemon, serve --schedule, ics, feed-url, notify prefs."""

from datetime import datetime, timedelta, timezone

import pytest

from logr import cli, notify
from logr.models import Hackathon, Source
from logr.searches import Preferences
from logr.store import Store
from logr.tracking import Tracker

NOW = datetime.now(timezone.utc)
WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/abcDEF_ghi-jklMNOpqrsTUVwxyz0123456789wxyz"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in list(__import__("os").environ):
        if var.startswith("LOGR_") and var != "LOGR_DB":
            monkeypatch.delenv(var, raising=False)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    s = Store(path)
    s.upsert_many([
        Hackathon(source=Source.DEVFOLIO, source_id="ai", title="AI Sprint", url="https://ai.example",
                  registration_closes_at=NOW + timedelta(days=2), submission_deadline=NOW + timedelta(days=9)),
    ])
    Tracker(s.db).track("devfolio:ai")
    s.close()
    return path


@pytest.fixture
def fake_config(monkeypatch):
    monkeypatch.setattr(notify.NotifierConfig, "from_env", classmethod(lambda cls, env=None: cls()))


def run(db, *argv):
    return cli.main(["--db", str(db), *argv])


def msg(subject):
    return notify.Message(kind="reminder", key=f"k:{subject}", subject=subject, text=subject)


# --- notify -------------------------------------------------------------------


def test_notify_dry_run(db, capsys, monkeypatch, fake_config):
    seen = {}

    def fake_run_pass(store, now, config, *, admins=(), dry_run=False, channel_factory=None):
        seen.update(admins=list(admins), dry_run=dry_run, store=store)
        return notify.PassReport(users=1, would_send=[("local", "email", msg("AI Sprint: registration closes in 1d"))],
                                 skipped_unconfigured=["local: telegram (no bot token)"])

    monkeypatch.setattr(notify, "run_pass", fake_run_pass)
    assert run(db, "notify", "--dry-run") == 0
    out = capsys.readouterr().out
    assert seen["dry_run"] is True and seen["admins"] == ["local"]
    assert "would be sent" in out
    assert "local" in out and "email" in out and "AI Sprint: registration closes in 1d" in out
    assert "telegram (no bot token)" in out


def test_notify_pass_summary_and_exit_code(db, capsys, monkeypatch, fake_config):
    reports = [
        notify.PassReport(users=2, sent=3),
        notify.PassReport(users=2, sent=1, failed=["local/discord: HTTP 404"]),
    ]
    calls = []

    def fake_run_pass(store, now, config, *, admins=(), dry_run=False, channel_factory=None):
        calls.append(dry_run)
        return reports[len(calls) - 1]

    monkeypatch.setattr(notify, "run_pass", fake_run_pass)
    assert run(db, "notify") == 0
    out = capsys.readouterr().out
    assert "2 user(s), 3 sent, 0 failed" in out
    assert run(db, "notify") == 1
    out = capsys.readouterr().out
    assert "1 failed" in out and "local/discord: HTTP 404" in out
    assert calls == [False, False]


def test_notify_admins_from_env(db, capsys, monkeypatch, fake_config):
    seen = {}
    monkeypatch.setattr(notify, "run_pass",
                        lambda store, now, config, *, admins=(), dry_run=False, **k: seen.update(a=list(admins))
                        or notify.PassReport())
    monkeypatch.setenv("LOGR_AUTH", "oauth")
    monkeypatch.setenv("LOGR_SESSION_SECRET", "x" * 40)
    monkeypatch.setenv("LOGR_GITHUB_CLIENT_ID", "id")
    monkeypatch.setenv("LOGR_GITHUB_CLIENT_SECRET", "secret")
    monkeypatch.setenv("LOGR_ADMINS", "github:1, google:2")
    assert run(db, "notify") == 0
    assert seen["a"] == ["github:1", "google:2"]


def test_notify_test(db, capsys, monkeypatch, fake_config):
    Preferences(Store(db).db).set("notify_email", "me@example.com")
    seen = {}

    def fake_send_test(prefs, config, *, channel_factory=None):
        seen["prefs"] = dict(prefs)
        return {"email": "ok"}

    monkeypatch.setattr(notify, "send_test", fake_send_test)
    assert run(db, "notify", "--test") == 0
    out = capsys.readouterr().out
    assert "✓ email" in out
    assert seen["prefs"]["notify_email"] == "me@example.com"

    monkeypatch.setattr(notify, "send_test", lambda prefs, config, **k: {
        "email": "ok", "telegram": "server has no LOGR_TELEGRAM_BOT_TOKEN"})
    assert run(db, "notify", "--test") == 1
    out = capsys.readouterr().out
    assert "✓ email" in out and "✗ telegram" in out and "LOGR_TELEGRAM_BOT_TOKEN" in out


def test_notify_test_no_channels_and_bad_combo(db, capsys, monkeypatch, fake_config):
    monkeypatch.setattr(notify, "send_test", lambda prefs, config, **k: {})
    assert run(db, "notify", "--test") == 1
    assert "no notification channels" in capsys.readouterr().err
    assert run(db, "notify", "--test", "--dry-run") == 2


def test_notify_bad_config_is_usage_error(db, capsys, monkeypatch):
    def bad(cls, env=None):
        raise ValueError("LOGR_SMTP_PORT must be a number")

    monkeypatch.setattr(notify.NotifierConfig, "from_env", classmethod(bad))
    assert run(db, "notify") == 2
    assert "LOGR_SMTP_PORT" in capsys.readouterr().err


# --- ics / feed-url -----------------------------------------------------------


def test_ics_stdout_and_file(db, capsys, tmp_path):
    assert run(db, "ics") == 0
    out = capsys.readouterr().out
    assert out.startswith("BEGIN:VCALENDAR") and "END:VCALENDAR" in out
    assert "AI Sprint" in out and "BEGIN:VEVENT" in out

    f = tmp_path / "cal.ics"
    assert run(db, "ics", "-o", str(f)) == 0
    assert "Wrote" in capsys.readouterr().out
    raw = f.read_bytes()
    assert raw.startswith(b"BEGIN:VCALENDAR\r\n")  # CRLF preserved on disk
    assert b"AI Sprint" in raw


def _feed_lines(capsys):
    return capsys.readouterr().out.splitlines()


def test_feed_url_stable_then_rotate(db, capsys, monkeypatch):
    assert run(db, "feed-url") == 0
    lines = _feed_lines(capsys)
    url = lines[0]
    assert url.startswith("http://127.0.0.1:8000/api/calendar.ics?token=")
    assert lines[1] == "webcal://" + url.removeprefix("http://")
    assert any("Other calendars" in ln and "From URL" in ln for ln in lines)

    assert run(db, "feed-url") == 0
    assert _feed_lines(capsys)[0] == url  # stable

    assert run(db, "feed-url", "--rotate") == 0
    rotated = _feed_lines(capsys)[0]
    assert rotated != url and rotated.startswith("http://127.0.0.1:8000/api/calendar.ics?token=")
    token = rotated.split("token=", 1)[1]

    monkeypatch.setenv("LOGR_BASE_URL", "https://logr.example.org/")
    assert run(db, "feed-url") == 0
    lines = _feed_lines(capsys)
    assert lines[0] == f"https://logr.example.org/api/calendar.ics?token={token}"
    assert lines[1] == f"webcal://logr.example.org/api/calendar.ics?token={token}"
    assert run(db, "feed-url", "--base", "https://cal.example.net") == 0  # --base wins
    assert _feed_lines(capsys)[0].startswith("https://cal.example.net/api/calendar.ics?token=")
    assert run(db, "feed-url", "--base", "ftp://nope") == 2


# --- prefs --------------------------------------------------------------------


def test_prefs_new_keys_validated(db, capsys):
    assert run(db, "prefs", "set", "notify_email", "not-an-email") == 2
    assert "notify_email" in capsys.readouterr().err
    assert run(db, "prefs", "set", "notify_telegram_chat", "hello world") == 2
    assert run(db, "prefs", "set", "notify_discord_webhook", "https://evil.example/api/webhooks/1/x") == 2
    assert run(db, "prefs", "set", "remind_offsets", "0h") == 2
    assert run(db, "prefs", "set", "remind_offsets", "3h,7d,1d") == 0
    assert Preferences(Store(db).db).get("remind_offsets") == "7d,1d,3h"  # normalized
    assert run(db, "prefs", "set", "notify_telegram_chat", "-1001234") == 0
    assert Preferences(Store(db).db).get("notify_telegram_chat") == "-1001234"


def test_prefs_validation_wiring(db, capsys, monkeypatch):
    seen = []

    def fake_validate(key, value):
        seen.append((key, value))
        if value == "bad":
            raise ValueError("nope, bad value")
        return value.upper()

    monkeypatch.setattr(notify, "validate_pref", fake_validate)
    assert run(db, "prefs", "set", "notify_email", "bad") == 2
    assert "nope, bad value" in capsys.readouterr().err
    assert run(db, "prefs", "set", "remind_offsets", "7d") == 0
    assert Preferences(Store(db).db).get("remind_offsets") == "7D"
    assert run(db, "prefs", "set", "tz", "UTC") == 0  # old keys don't go through notify
    assert seen == [("notify_email", "bad"), ("remind_offsets", "7d")]


def test_prefs_secret_masked(db, capsys):
    assert run(db, "prefs", "set", "notify_discord_webhook", WEBHOOK) == 0
    out = capsys.readouterr().out
    assert WEBHOOK not in out and "wxyz" in out
    assert Preferences(Store(db).db).get("notify_discord_webhook") == WEBHOOK
    assert run(db, "prefs") == 0
    out = capsys.readouterr().out
    assert WEBHOOK not in out and "abcDEF" not in out
    assert notify.mask_secret(WEBHOOK) in out
    for key in ("notify_email", "notify_telegram_chat", "notify_discord_webhook", "remind_offsets"):
        assert key in out


# --- daemon / serve --schedule ------------------------------------------------


class FakeScheduler:
    instances: list = []

    def __init__(self, db_path, *, sync_every, notify_every, **kw):
        self.db_path, self.sync_every, self.notify_every = db_path, sync_every, notify_every
        self.events = []
        FakeScheduler.instances.append(self)

    def start(self):
        self.events.append("start")

    def stop(self, timeout=None):
        self.events.append("stop")
        return True


@pytest.fixture
def fake_sched(monkeypatch, fake_config):
    FakeScheduler.instances = []
    monkeypatch.setattr(cli, "Scheduler", FakeScheduler)
    return FakeScheduler


def test_daemon_wiring(db, monkeypatch, fake_sched, capsys):
    waited = []
    monkeypatch.setattr(cli, "_wait_for_shutdown", lambda: waited.append(fake_sched.instances[-1].events[:]))
    assert run(db, "daemon") == 0
    (s,) = fake_sched.instances
    assert waited == [["start"]]  # started before blocking
    assert s.events == ["start", "stop"]
    assert s.sync_every == timedelta(hours=6) and s.notify_every == timedelta(minutes=15)
    assert str(s.db_path) == str(db)

    assert run(db, "daemon", "--sync-every", "2.5", "--notify-every", "5") == 0
    s = fake_sched.instances[-1]
    assert s.sync_every == timedelta(hours=2.5) and s.notify_every == timedelta(minutes=5)
    assert "notifying every 5min" in capsys.readouterr().err


def test_daemon_stops_on_interrupt(db, monkeypatch, fake_sched):
    def interrupted():
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_wait_for_shutdown", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run(db, "daemon")
    assert fake_sched.instances[-1].events == ["start", "stop"]


def test_daemon_rejects_bad_intervals(db, fake_sched):
    for argv in (["--sync-every", "0"], ["--notify-every", "-1"], ["--notify-every", "abc"]):
        with pytest.raises(SystemExit):
            run(db, "daemon", *argv)
    assert fake_sched.instances == []


def test_serve_schedule_wiring(db, monkeypatch, fake_sched):
    pytest.importorskip("fastapi")
    import logr.api

    calls = []

    def fake_api_run(db_path, host="127.0.0.1", port=8000, static_dir=None):
        calls.append((str(db_path), host, port, [s.events[:] for s in fake_sched.instances]))

    monkeypatch.setattr(logr.api, "run", fake_api_run)
    assert run(db, "serve", "--schedule", "--sync-every", "12", "--notify-every", "30", "--port", "9001") == 0
    (s,) = fake_sched.instances
    assert calls == [(str(db), "127.0.0.1", 9001, [["start"]])]  # scheduler running while serving
    assert s.events == ["start", "stop"]
    assert s.sync_every == timedelta(hours=12) and s.notify_every == timedelta(minutes=30)

    # stopped even when the server crashes
    def boom(*a, **k):
        raise RuntimeError("port in use")

    monkeypatch.setattr(logr.api, "run", boom)
    with pytest.raises(RuntimeError):
        run(db, "serve", "--schedule")
    assert fake_sched.instances[-1].events == ["start", "stop"]

    # no --schedule → no scheduler
    monkeypatch.setattr(logr.api, "run", fake_api_run)
    n = len(fake_sched.instances)
    assert run(db, "serve") == 0
    assert len(fake_sched.instances) == n
