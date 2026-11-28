import json
import smtplib
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from logr import notify, store as store_mod, tracking
from logr.models import Hackathon, Round, Source
from logr.notify import (
    DEFAULT_OFFSETS, ChannelError, DiscordChannel, EmailChannel, Message, NotifierConfig, Outbox, TelegramChannel,
    channels_for, collect, health_messages, mask_secret, parse_offsets, run_pass, send_test, suppressed_keys,
    validate_pref,
)
from logr.searches import Preferences
from logr.store import Store
from logr.tracking import Status, Tracker

UTC = timezone.utc
T_TRACK = datetime(2026, 11, 1, 12, tzinfo=UTC)
NOW = datetime(2026, 11, 25, 6, tzinfo=UTC)
D = datetime(2026, 12, 1, 18, 30, tzinfo=UTC)  # the registration deadline in most tests
UID = "unstop:1"

TOKEN = "123456789:AAFakeTelegramTokenValue_abcdefghij"
HOOK_TOKEN = "SuperSecretWebhookToken_abcdefghijklmnopqrstuvwxyz0123"
HOOK = f"https://discord.com/api/webhooks/112233445566778899/{HOOK_TOKEN}"
PASSWORD = "hunter2-very-secret"


# --- fixtures and helpers -------------------------------------------------------------------


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(tracking, "utcnow", lambda: T_TRACK)
    s = Store(tmp_path / "n.db")
    yield s
    s.close()


def hk(source=Source.UNSTOP, source_id="1", title="Inceptrix 3.0", **kw):
    kw.setdefault("url", f"https://{source.value}.example/{source_id}")
    return Hackathon(source=source, source_id=source_id, title=title, **kw)


def upsert(store, monkeypatch, h, at):
    monkeypatch.setattr(store_mod, "utcnow", lambda: at)
    return store.upsert_many([h])


class Sink:
    """channel_factory that builds fake channels from the user's prefs and records
    every delivery as (channel, destination, Message)."""

    def __init__(self):
        self.sent = []
        self.failing = set()  # channel names that raise

    def factory(self, prefs, config):
        out = []
        for name, key in (("email", "notify_email"), ("telegram", "notify_telegram_chat"),
                          ("discord", "notify_discord_webhook")):
            if prefs.get(key):
                out.append(FakeChannel(name, prefs[key], self))
        return out

    def of(self, kind=None, dest=None, channel=None):
        return [m for c, d, m in self.sent
                if (kind is None or m.kind == kind) and (dest is None or d == dest)
                and (channel is None or c == channel)]


class FakeChannel:
    def __init__(self, name, dest, sink):
        self.name, self.dest, self.sink = name, dest, sink

    def send(self, msg):
        if self.name in self.sink.failing:
            raise ChannelError(f"{self.name}: server said no")
        self.sink.sent.append((self.name, self.dest, msg))


def user(store, uid=UID, *, user_id="local", status=Status.INTERESTED, **prefs):
    tr = Tracker(store.db, user_id)
    tr.track(uid, status)
    p = Preferences(store.db, user_id)
    for k, v in prefs.items():
        p.set(k, v)
    return tr


CFG = NotifierConfig()


def rem_key(uid, label, at, offset):
    return f"rem:{uid}:{label}:{at.isoformat()}:{int(offset.total_seconds())}"


# --- parse_offsets / validate_pref / mask_secret ----------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("7d,1d,3h", (timedelta(days=7), timedelta(days=1), timedelta(hours=3))),
    ("3h, 7d ,1d,1d", (timedelta(days=7), timedelta(days=1), timedelta(hours=3))),
    ("2w,30m", (timedelta(weeks=2), timedelta(minutes=30))),
    ("24h,1d", (timedelta(days=1),)),
    (None, DEFAULT_OFFSETS),
    ("", DEFAULT_OFFSETS),
    ("   ", DEFAULT_OFFSETS),
])
def test_parse_offsets_good(text, expected):
    assert parse_offsets(text) == expected


@pytest.mark.parametrize("text", ["0d", "-1d", "5x", "61d", "9w", "d", "7d,,1d", "1.5d", "1d,2d,3d,4d,5d,6d,7d"])
def test_parse_offsets_bad(text):
    with pytest.raises(ValueError):
        parse_offsets(text)


def test_parse_offsets_allows_exactly_six_and_sixty_days():
    assert len(parse_offsets("60d,1d,2d,3d,4d,5d")) == 6


@pytest.mark.parametrize("key,value,expected", [
    ("notify_email", " Me.Name+logr@Example.COM ", "Me.Name+logr@example.com"),
    ("notify_telegram_chat", "123456789", "123456789"),
    ("notify_telegram_chat", "-1001234567890", "-1001234567890"),
    ("notify_telegram_chat", "@my_channel", "@my_channel"),
    ("notify_discord_webhook", HOOK, HOOK),
    ("notify_discord_webhook", HOOK.replace("discord.com", "discordapp.com"), None),
    ("notify_discord_webhook", HOOK.replace("discord.com", "canary.discord.com"), None),
    ("notify_discord_webhook", HOOK.replace("discord.com", "ptb.discord.com"), None),
    ("remind_offsets", "3h, 7d,1d", "7d,1d,3h"),
    ("remind_offsets", "2w,90m", "14d,90m"),
])
def test_validate_pref_good(key, value, expected):
    assert validate_pref(key, value) == (expected if expected is not None else value)


@pytest.mark.parametrize("key,value", [
    ("notify_email", "a@b"),
    ("notify_email", "a@b.com,c@d.com"),
    ("notify_email", "x y@z.com"),
    ("notify_email", "a@b.com\r\nBcc: evil@x.com"),
    ("notify_email", "Me <me@example.com>"),
    ("notify_email", ""),
    ("notify_telegram_chat", "abc"),
    ("notify_telegram_chat", "12a"),
    ("notify_telegram_chat", "@ab"),
    ("notify_telegram_chat", "0"),
    ("notify_discord_webhook", HOOK.replace("https://", "http://")),
    ("notify_discord_webhook", HOOK.replace("discord.com", "evil.com")),
    ("notify_discord_webhook", HOOK.replace("discord.com", "discord.com.evil.com")),
    ("notify_discord_webhook", HOOK + "/../../x"),
    ("notify_discord_webhook", "https://discord.com/api/webhooks/123/"),
    ("remind_offsets", "soon"),
])
def test_validate_pref_bad(key, value):
    with pytest.raises(ValueError) as e:
        validate_pref(key, value)
    assert HOOK_TOKEN not in str(e.value)


def test_validate_pref_other_key_is_keyerror():
    with pytest.raises(KeyError):
        validate_pref("tz", "Asia/Kolkata")


def test_mask_secret():
    assert mask_secret(HOOK) == "https://discord.com/…/•••••0123"
    assert HOOK_TOKEN not in mask_secret(HOOK)
    assert mask_secret("abcdefghijkl1234") == "•••••1234"
    assert mask_secret("short") == "•••••"  # too short to reveal any of it
    assert mask_secret("") == "•••••"


# --- config / channels_for --------------------------------------------------------------------


def test_from_env_full():
    c = NotifierConfig.from_env({
        "LOGR_SMTP_HOST": "smtp.example.com", "LOGR_SMTP_PORT": "2525", "LOGR_SMTP_USER": "bot@example.com",
        "LOGR_SMTP_PASSWORD": PASSWORD, "LOGR_SMTP_FROM": "Logr <bot@example.com>", "LOGR_SMTP_STARTTLS": "false",
        "LOGR_TELEGRAM_BOT_TOKEN": TOKEN, "LOGR_BASE_URL": "https://logr.example/",
    })
    assert (c.smtp_host, c.smtp_port, c.smtp_user, c.smtp_password, c.smtp_from) == (
        "smtp.example.com", 2525, "bot@example.com", PASSWORD, "Logr <bot@example.com>")
    assert c.smtp_starttls is False and c.telegram_bot_token == TOKEN and c.base_url == "https://logr.example"
    assert c.email_enabled and c.telegram_enabled
    assert PASSWORD not in repr(c) and TOKEN not in repr(c)


def test_from_env_defaults_and_465():
    c = NotifierConfig.from_env({})
    assert c == NotifierConfig() and not c.email_enabled and not c.telegram_enabled
    c = NotifierConfig.from_env({"LOGR_SMTP_HOST": "h", "LOGR_SMTP_PORT": "465", "LOGR_SMTP_USER": "u"})
    assert c.smtp_port == 465 and c.smtp_starttls is False  # implicit TLS unless told otherwise
    c = NotifierConfig.from_env({"LOGR_SMTP_PORT": "465", "LOGR_SMTP_STARTTLS": "true"})
    assert c.smtp_starttls is True


@pytest.mark.parametrize("env", [{"LOGR_SMTP_PORT": "abc"}, {"LOGR_SMTP_PORT": "0"}, {"LOGR_SMTP_PORT": "70000"},
                                 {"LOGR_SMTP_STARTTLS": "maybe"}])
def test_from_env_bad(env):
    with pytest.raises(ValueError):
        NotifierConfig.from_env(env)


def test_from_env_reads_os_environ(monkeypatch):
    monkeypatch.setenv("LOGR_TELEGRAM_BOT_TOKEN", TOKEN)
    assert NotifierConfig.from_env().telegram_enabled


PREFS_ALL = {"notify_email": "me@example.com", "notify_telegram_chat": "42", "notify_discord_webhook": HOOK}


def test_channels_for_skips_unavailable_server_side():
    chans = channels_for(PREFS_ALL, NotifierConfig())
    assert [type(c) for c in chans] == [DiscordChannel]
    full = NotifierConfig(smtp_host="smtp.example.com", smtp_user="bot@example.com", telegram_bot_token=TOKEN)
    chans = channels_for(PREFS_ALL, full)
    assert [(type(c), c.name) for c in chans] == [
        (EmailChannel, "email"), (TelegramChannel, "telegram"), (DiscordChannel, "discord")]
    assert all(TOKEN not in repr(c) and HOOK_TOKEN not in repr(c) for c in chans)
    assert channels_for({}, full) == []


def test_run_pass_reports_unconfigured(store):
    user(store, **{"notify_email": "me@example.com", "notify_telegram_chat": "42"})
    rep = run_pass(store, NOW, NotifierConfig())
    assert rep.users == 1
    assert rep.skipped_unconfigured == ["local: email (no SMTP server)", "local: telegram (no bot token)"]


# --- reminders --------------------------------------------------------------------------------


@pytest.mark.parametrize("before,offset,suppressed", [
    (timedelta(days=8), None, []),
    (timedelta(days=7), timedelta(days=7), []),  # window start is inclusive
    (timedelta(days=6), timedelta(days=7), []),
    (timedelta(hours=20), timedelta(days=1), [timedelta(days=7)]),
    (timedelta(hours=3), timedelta(hours=3), [timedelta(days=7), timedelta(days=1)]),
    (timedelta(minutes=1), timedelta(hours=3), [timedelta(days=7), timedelta(days=1)]),
    (timedelta(0), None, []),  # at the deadline: too late
    (-timedelta(hours=1), None, []),
])
def test_reminder_due_windows(store, monkeypatch, before, offset, suppressed):
    upsert(store, monkeypatch, hk(registration_closes_at=D), T_TRACK)
    user(store)
    now = D - before
    msgs = collect(store, "local", now, since=None)
    if offset is None:
        assert msgs == []
    else:
        [m] = msgs
        assert m.kind == "reminder" and m.key == rem_key(UID, "registration closes", D, offset)
        assert m.uid == UID and m.url == "https://unstop.example/1"
    assert suppressed_keys(store, "local", now) == [rem_key(UID, "registration closes", D, o) for o in suppressed]


def test_custom_offsets(store, monkeypatch):
    upsert(store, monkeypatch, hk(registration_closes_at=D), T_TRACK)
    user(store, remind_offsets="2d,30m")
    assert collect(store, "local", D - timedelta(hours=20), since=None)[0].key == rem_key(
        UID, "registration closes", D, timedelta(days=2))
    assert collect(store, "local", D - timedelta(minutes=10), since=None)[0].key == rem_key(
        UID, "registration closes", D, timedelta(minutes=30))


def test_smallest_due_offset_only_one_message_and_suppressed_marked(store, monkeypatch):
    upsert(store, monkeypatch, hk(registration_closes_at=D), T_TRACK)
    user(store, notify_email="me@example.com", notify_discord_webhook=HOOK)
    sink = Sink()
    rep = run_pass(store, D - timedelta(hours=2), CFG, channel_factory=sink.factory)
    assert rep.sent == 2 and len(sink.of(channel="email")) == 1 and len(sink.of(channel="discord")) == 1
    assert sink.sent[0][2].subject == "⏰ Inceptrix 3.0 — registration closes in 2 hours"
    ob = Outbox(store.db)
    for o in DEFAULT_OFFSETS:
        for ch in ("email", "discord"):
            assert ob.was_sent("local", rem_key(UID, "registration closes", D, o), ch)
    # later passes before the deadline send nothing more
    assert run_pass(store, D - timedelta(minutes=5), CFG, channel_factory=sink.factory).sent == 0


def test_full_schedule_without_repeats(store, monkeypatch):
    upsert(store, monkeypatch, hk(registration_closes_at=D), T_TRACK)
    user(store, notify_email="me@example.com")
    sink = Sink()
    for before in (timedelta(days=6), timedelta(days=5), timedelta(hours=20), timedelta(hours=19),
                   timedelta(hours=2), timedelta(hours=1)):
        run_pass(store, D - before, CFG, channel_factory=sink.factory)
    subjects = [m.subject for m in sink.of()]
    assert subjects == ["⏰ Inceptrix 3.0 — registration closes in 6 days",
                        "⏰ Inceptrix 3.0 — registration closes in 20 hours",
                        "⏰ Inceptrix 3.0 — registration closes in 2 hours"]
    assert Outbox(store.db).last_pass("local") == D - timedelta(hours=1)


def test_moved_deadline_restarts_schedule(store, monkeypatch):
    upsert(store, monkeypatch, hk(registration_closes_at=D), T_TRACK)
    user(store, notify_email="me@example.com")
    sink = Sink()
    run_pass(store, D - timedelta(hours=20), CFG, channel_factory=sink.factory)
    assert len(sink.of("reminder")) == 1
    new_d = D + timedelta(days=3)
    upsert(store, monkeypatch, hk(registration_closes_at=new_d), D - timedelta(hours=10))
    run_pass(store, D - timedelta(hours=9), CFG, channel_factory=sink.factory)
    [moved] = sink.of("moved")
    assert "extended by 3d" in moved.text
    rems = sink.of("reminder")
    assert len(rems) == 2 and rems[1].key == rem_key(UID, "registration closes", new_d, timedelta(days=7))


def test_status_aware(store, monkeypatch):
    sub = D + timedelta(days=3)
    upsert(store, monkeypatch, hk(registration_closes_at=D, submission_deadline=sub), T_TRACK)
    now = D - timedelta(hours=2)  # both within the 7d window, registration within 3h
    tr = user(store, status=Status.REGISTERED)
    [m] = collect(store, "local", now, since=None)
    assert m.key == rem_key(UID, "submission due", sub, timedelta(days=7))
    tr.add_todo(UID, "book train", due_at=now + timedelta(hours=1))
    tr.set_status(UID, Status.WON)
    assert collect(store, "local", now, since=None) == []
    assert suppressed_keys(store, "local", now) == []


def test_terminal_status_gets_no_moved_alerts(store, monkeypatch):
    upsert(store, monkeypatch, hk(registration_closes_at=D), T_TRACK - timedelta(days=1))
    user(store, status=Status.WITHDRAWN)
    upsert(store, monkeypatch, hk(registration_closes_at=D + timedelta(days=1)), T_TRACK + timedelta(days=1))
    assert collect(store, "local", NOW, since=None) == []


def test_todos_remind(store):
    tr = user(store, "devpost:gone")  # listing not in the store: todos still count
    due = NOW + timedelta(minutes=90)
    tr.add_todo("devpost:gone", "record demo video", due_at=due)
    [m] = collect(store, "local", NOW, since=None)
    assert m.key == rem_key("devpost:gone", "todo: record demo video", due, timedelta(hours=3))
    # 90 minutes left: never round a countdown up ("2 hours" overstated it)
    assert m.subject == "⏰ devpost:gone — to-do due in 1 hour 30 minutes: record demo video"
    assert m.url is None


def test_best_record_of_merged_group_is_used(store, monkeypatch):
    start = D + timedelta(days=2)
    luma = hk(Source.LUMA, "evt-abc", starts_at=start, registration_closes_at=start,
              approx_fields=["registration_closes_at"])
    devfolio = hk(Source.DEVFOLIO, "inceptrix", starts_at=start, registration_closes_at=D,
                  submission_deadline=start + timedelta(days=1))
    store.upsert_many([luma, devfolio])
    user(store, "luma:evt-abc")
    msgs = collect(store, "local", D - timedelta(hours=2), since=None)
    assert msgs[0].key == rem_key("luma:evt-abc", "registration closes", D, timedelta(hours=3))
    assert msgs[0].url == "https://devfolio.example/inceptrix"
    assert "(approximate)" not in msgs[0].text


def test_soonest_first(store, monkeypatch):
    store.upsert_many([hk(source_id="1", title="Later", registration_closes_at=D + timedelta(days=1)),
                       hk(source_id="2", title="Sooner", registration_closes_at=D)])
    user(store, "unstop:1")
    user(store, "unstop:2")
    msgs = collect(store, "local", D - timedelta(hours=1), since=None)
    assert [m.uid for m in msgs] == ["unstop:2", "unstop:1"]


# --- message content --------------------------------------------------------------------------


def test_message_text_tz_approximate_and_dashboard(store, monkeypatch):
    d = datetime(2026, 12, 24, 18, 29, tzinfo=UTC)
    upsert(store, monkeypatch, hk(registration_closes_at=d, approx_fields=["registration_closes_at"]), T_TRACK)
    user(store, tz="Asia/Kolkata", notify_email="me@example.com")
    now = d - timedelta(hours=24)
    [m] = collect(store, "local", now, since=None)
    assert m.subject == "⏰ Inceptrix 3.0 — registration closes in 1 day"
    assert "Thu 24 Dec 2026, 23:59 IST (approximate)" in m.text
    assert "https://unstop.example/1" in m.text
    assert "/hackathon/" not in m.text  # the dashboard link needs base_url
    sink = Sink()
    run_pass(store, now, NotifierConfig(base_url="https://logr.example"), channel_factory=sink.factory)
    assert "https://logr.example/hackathon/?uid=unstop%3A1" in sink.of()[0].text


def test_bad_tz_falls_back_to_utc(store, monkeypatch):
    upsert(store, monkeypatch, hk(registration_closes_at=D), T_TRACK)
    user(store, tz="Mars/Olympus")
    [m] = collect(store, "local", D - timedelta(hours=1), since=None)
    assert "Tue 1 Dec 2026, 18:30 UTC" in m.text and "(approximate)" not in m.text


# --- moved alerts -----------------------------------------------------------------------------


def test_moved_alerts_since_last_pass_only(store, monkeypatch):
    far = datetime(2027, 3, 1, 18, 29, tzinfo=UTC)  # no reminders in play
    upsert(store, monkeypatch, hk(registration_closes_at=far), datetime(2026, 10, 1, tzinfo=UTC))
    upsert(store, monkeypatch, hk(registration_closes_at=far + timedelta(days=1)),
           datetime(2026, 10, 15, tzinfo=UTC))  # before tracking: never alerted
    user(store, tz="Asia/Kolkata", notify_email="me@example.com")  # tracked at T_TRACK
    upsert(store, monkeypatch, hk(registration_closes_at=far + timedelta(days=8)),
           datetime(2026, 11, 10, tzinfo=UTC))
    sink = Sink()
    run_pass(store, NOW, CFG, channel_factory=sink.factory)
    [m] = sink.of("moved")
    assert m.subject == "📅 Deadline moved: Inceptrix 3.0"
    assert m.text.splitlines()[0] == (
        "Inceptrix 3.0: registration closes moved from 2 Mar 23:59 to 9 Mar 23:59 IST (extended by 7d)")
    assert "https://unstop.example/1" in m.text and m.uid == UID
    run_pass(store, NOW + timedelta(hours=1), CFG, channel_factory=sink.factory)
    assert len(sink.of("moved")) == 1  # no repeat
    upsert(store, monkeypatch, hk(registration_closes_at=far + timedelta(days=6)), NOW + timedelta(days=1))
    run_pass(store, NOW + timedelta(days=2), CFG, channel_factory=sink.factory)
    moved = sink.of("moved")
    assert len(moved) == 2 and "(moved earlier by 2d)" in moved[1].text
    # explicit since: only changes after it
    assert [x.kind for x in collect(store, "local", NOW + timedelta(days=2), since=NOW)] == ["moved"]
    assert collect(store, "local", NOW + timedelta(days=2), since=NOW + timedelta(days=1, hours=1)) == []


def test_round_change_shows_old_and_new(store, monkeypatch):
    r_old = Round(name="Idea PPT", starts_at=datetime(2027, 1, 1, tzinfo=UTC), ends_at=datetime(2027, 1, 5, tzinfo=UTC))
    r_new = Round(name="Idea PPT", starts_at=datetime(2027, 1, 1, tzinfo=UTC), ends_at=datetime(2027, 1, 9, tzinfo=UTC))
    upsert(store, monkeypatch, hk(rounds=[r_old]), T_TRACK - timedelta(days=1))
    user(store)
    upsert(store, monkeypatch, hk(rounds=[r_new]), T_TRACK + timedelta(days=1))
    [m] = collect(store, "local", NOW, since=None)
    assert m.kind == "moved" and m.text.startswith("Inceptrix 3.0: round “Idea PPT” timeline changed")
    assert "Was: 1 Jan 00:00 → 5 Jan 00:00" in m.text and "Now: 1 Jan 00:00 → 9 Jan 00:00" in m.text


# --- health -----------------------------------------------------------------------------------


def add_run(store, source, *, at, fetched=10, error=None, finished=True):
    cur = store.db.execute(
        "INSERT INTO sync_runs (source, started_at, finished_at, fetched, error) VALUES (?,?,?,?,?)",
        (source, at.isoformat().replace("+00:00", "Z"),
         (at + timedelta(minutes=1)).isoformat().replace("+00:00", "Z") if finished else None, fetched, error))
    store.db.commit()
    return cur.lastrowid


def test_health_messages(store):
    assert health_messages(store, NOW) == []  # never synced: nothing to report
    add_run(store, "devpost", at=NOW - timedelta(days=2), error="old failure")
    err = add_run(store, "devpost", at=NOW - timedelta(hours=1), error="HTTP 503 from devpost")
    empty = add_run(store, "unstop", at=NOW - timedelta(hours=1), fetched=0)
    add_run(store, "luma", at=NOW - timedelta(hours=1))
    add_run(store, "devfolio", at=NOW - timedelta(minutes=1), fetched=0, finished=False)  # still running
    msgs = health_messages(store, NOW)
    assert [m.key for m in msgs] == [f"health:error:devpost:{err}", f"health:empty:unstop:{empty}"]
    assert msgs[0].subject == "⚠️ Logr sync: devpost failed" and "HTTP 503" in msgs[0].text


def test_health_stale(store):
    add_run(store, "luma", at=NOW - timedelta(hours=30))
    add_run(store, "devpost", at=NOW - timedelta(hours=2), error="boom")
    keys = [m.key for m in health_messages(store, NOW)]
    assert "health:stale:2026-11-25" in keys
    add_run(store, "unstop", at=NOW - timedelta(hours=3))
    assert "health:stale:2026-11-25" not in [m.key for m in health_messages(store, NOW)]


def test_health_only_to_admins_and_deduped(store):
    add_run(store, "luma", at=NOW - timedelta(hours=2))
    add_run(store, "devpost", at=NOW - timedelta(hours=1), error="boom")
    user(store, notify_email="me@example.com")
    Preferences(store.db, "admin").set("notify_discord_webhook", HOOK)
    sink = Sink()
    rep = run_pass(store, NOW, CFG, admins=["admin"], channel_factory=sink.factory)
    assert rep.users == 2  # the admin tracks nothing but is still considered
    assert [(c, m.kind) for c, _, m in sink.sent] == [("discord", "health")]
    run_pass(store, NOW + timedelta(minutes=15), CFG, admins=["admin"], channel_factory=sink.factory)
    assert len(sink.sent) == 1
    # a new failed run is a new alert
    add_run(store, "devpost", at=NOW + timedelta(minutes=20), error="boom again")
    run_pass(store, NOW + timedelta(minutes=30), CFG, admins=["admin"], channel_factory=sink.factory)
    assert len(sink.of("health")) == 2 and not sink.of(dest="me@example.com")


# --- delivery semantics -----------------------------------------------------------------------


def test_failed_channel_retried_other_not_repeated(store, monkeypatch):
    upsert(store, monkeypatch, hk(registration_closes_at=D), T_TRACK - timedelta(days=1))
    user(store, notify_email="me@example.com", notify_discord_webhook=HOOK)
    upsert(store, monkeypatch, hk(registration_closes_at=D + timedelta(hours=1)), T_TRACK + timedelta(days=1))
    sink = Sink()
    sink.failing = {"email"}
    now = D - timedelta(hours=20)
    rep = run_pass(store, now, CFG, channel_factory=sink.factory)
    assert rep.sent == 2 and sorted(m.kind for m in sink.of(channel="discord")) == ["moved", "reminder"]
    assert rep.failed == ["local/email: email: server said no"] * 2
    sink.failing = set()
    rep = run_pass(store, now + timedelta(minutes=15), CFG, channel_factory=sink.factory)
    assert rep.sent == 2 and rep.failed == []
    assert sorted(m.kind for m in sink.of(channel="email")) == ["moved", "reminder"]
    assert len(sink.of(channel="discord")) == 2  # not repeated
    assert run_pass(store, now + timedelta(minutes=30), CFG, channel_factory=sink.factory).sent == 0


def test_unexpected_exception_reported_by_type_only(store, monkeypatch):
    upsert(store, monkeypatch, hk(registration_closes_at=D), T_TRACK)
    user(store, notify_email="me@example.com")

    class Boom:
        name = "email"

        def send(self, msg):
            raise RuntimeError(f"failed at https://api.telegram.org/bot{TOKEN}/sendMessage")

    rep = run_pass(store, D - timedelta(hours=1), CFG, channel_factory=lambda p, c: [Boom()])
    assert rep.failed == ["local/email: unexpected RuntimeError"]


def test_dry_run_sends_and_marks_nothing(store, monkeypatch):
    upsert(store, monkeypatch, hk(registration_closes_at=D), T_TRACK)
    user(store, notify_email="me@example.com", notify_discord_webhook=HOOK)
    sink = Sink()
    now = D - timedelta(hours=2)
    rep = run_pass(store, now, CFG, dry_run=True, channel_factory=sink.factory)
    assert sink.sent == [] and rep.sent == 0
    assert [(u, c, m.kind) for u, c, m in rep.would_send] == [("local", "email", "reminder"),
                                                              ("local", "discord", "reminder")]
    ob = Outbox(store.db)
    assert ob.last_pass("local") is None
    assert not any(ob.was_sent("local", rem_key(UID, "registration closes", D, o), "email") for o in DEFAULT_OFFSETS)
    assert run_pass(store, now, CFG, channel_factory=sink.factory).sent == 2


def test_multiple_users_isolated(store, monkeypatch):
    store.upsert_many([hk(source_id="1", title="Alpha", registration_closes_at=D),
                       hk(source_id="2", title="Beta", registration_closes_at=D)])
    user(store, "unstop:1", user_id="alice", notify_email="alice@example.com")
    user(store, "unstop:2", user_id="bob", notify_telegram_chat="42", remind_offsets="30m")
    sink = Sink()
    rep = run_pass(store, D - timedelta(hours=2), CFG, channel_factory=sink.factory)
    assert rep.users == 2
    assert [(d, m.uid) for _, d, m in sink.sent] == [("alice@example.com", "unstop:1")]
    rep = run_pass(store, D - timedelta(minutes=20), CFG, channel_factory=sink.factory)
    assert [(d, m.uid) for _, d, m in sink.sent][1:] == [("42", "unstop:2")]
    ob = Outbox(store.db)
    assert ob.last_pass("alice") == ob.last_pass("bob") == D - timedelta(minutes=20)


def test_outbox_basics(store):
    ob = Outbox(store.db)
    assert not ob.was_sent("u", "k", "email") and ob.last_pass("u") is None
    ob.mark_sent("u", "k", "email", NOW)
    ob.mark_sent("u", "k", "email", NOW)  # idempotent
    assert ob.was_sent("u", "k", "email") and not ob.was_sent("u", "k", "discord")
    assert not ob.was_sent("v", "k", "email")
    ob.set_last_pass("u", NOW)
    ob.set_last_pass("u", NOW + timedelta(hours=1))
    assert ob.last_pass("u") == NOW + timedelta(hours=1)


def test_send_test(store):
    sink = Sink()
    sink.failing = {"discord"}
    res = send_test(PREFS_ALL, NotifierConfig(smtp_host="smtp.example.com", smtp_user="bot@example.com"),
                    channel_factory=sink.factory)
    assert res == {"email": "ok", "telegram": "server has no LOGR_TELEGRAM_BOT_TOKEN",
                   "discord": "discord: server said no"}
    assert [m.subject for _, _, m in sink.sent] == ["Logr test notification"]
    assert sink.sent[0][2].kind == "test" and sink.sent[0][2].key.startswith("test:")
    assert send_test({}, CFG, channel_factory=sink.factory) == {}


# --- real channels: Telegram / Discord over MockTransport ---------------------------------------


MSG = Message(kind="reminder", key="k", subject="⏰ Inceptrix 3.0 — registration closes in 1 day",
              text="Inceptrix 3.0: Registration closes in 1 day.\nLink: https://unstop.example/1",
              url="https://unstop.example/1", uid=UID)


def mock_client(handler, seen):
    def wrapped(request):
        seen.append(request)
        return handler(request)
    return httpx.Client(transport=httpx.MockTransport(wrapped))


def test_telegram_payload():
    seen = []
    ch = TelegramChannel(TOKEN, "-100123", client=mock_client(lambda r: httpx.Response(200, json={"ok": True}), seen))
    ch.send(MSG)
    [req] = seen
    assert req.method == "POST" and str(req.url) == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    body = json.loads(req.content)
    assert body == {"chat_id": "-100123", "disable_web_page_preview": True,
                    "text": f"{MSG.subject}\n\n{MSG.text}"}
    assert "parse_mode" not in body


@pytest.mark.parametrize("response", [
    httpx.Response(401, json={"ok": False, "description": "Unauthorized"}),
    httpx.Response(200, json={"ok": False, "description": f"Bad Request: chat not found (bot{TOKEN})"}),
    httpx.Response(502, text="<html>bad gateway</html>"),
])
def test_telegram_errors_never_leak_token(response):
    ch = TelegramChannel(TOKEN, "42", client=mock_client(lambda r: response, []))
    with pytest.raises(ChannelError) as e:
        ch.send(MSG)
    assert TOKEN not in str(e.value) and TOKEN not in repr(e.value)
    assert e.value.__cause__ is None and e.value.__context__ is None or TOKEN not in str(e.value.__context__)
    assert str(e.value).startswith("telegram: HTTP ")


def test_telegram_network_error_never_leaks_token():
    def boom(request):
        raise httpx.ConnectError(f"cannot connect to {request.url}", request=request)

    ch = TelegramChannel(TOKEN, "42", client=mock_client(boom, []))
    with pytest.raises(ChannelError) as e:
        ch.send(MSG)
    assert TOKEN not in str(e.value) and str(e.value) == "telegram: network error (ConnectError)"
    assert e.value.__suppress_context__


def test_discord_payload_and_truncation():
    seen = []
    ch = DiscordChannel(HOOK, client=mock_client(lambda r: httpx.Response(204), seen))
    ch.send(MSG)
    long = Message(kind="moved", key="k2", subject="**@everyone** _x_", text="y" * 5000)
    ch.send(long)
    first, second = (json.loads(r.content) for r in seen)
    assert str(seen[0].url) == HOOK
    assert first["allowed_mentions"] == {"parse": []}
    assert first["content"] == f"**{MSG.subject}**\n{MSG.text}"
    assert len(second["content"]) == 2000 and second["content"].startswith(r"**\*\*@everyone\*\* \_x\_**")


@pytest.mark.parametrize("response", [
    httpx.Response(404, json={"message": "Unknown Webhook", "code": 10015}),
    httpx.Response(400, json={"message": f"bad {HOOK}"}),
    httpx.Response(429, json={"retry_after": 1.5}),
    httpx.Response(500, text="oops"),
])
def test_discord_errors_never_leak_webhook(response):
    ch = DiscordChannel(HOOK, client=mock_client(lambda r: response, []))
    with pytest.raises(ChannelError) as e:
        ch.send(MSG)
    assert HOOK_TOKEN not in str(e.value) and "webhooks/112233" not in str(e.value)
    assert str(e.value).startswith(f"discord: HTTP {response.status_code}")


def test_discord_network_error_never_leaks_webhook():
    def boom(request):
        raise httpx.ReadTimeout(f"timed out talking to {request.url}", request=request)

    ch = DiscordChannel(HOOK, client=mock_client(boom, []))
    with pytest.raises(ChannelError) as e:
        ch.send(MSG)
    assert HOOK_TOKEN not in str(e.value)


def test_pass_report_has_no_secrets(store, monkeypatch):
    upsert(store, monkeypatch, hk(registration_closes_at=D), T_TRACK)
    user(store, notify_telegram_chat="42", notify_discord_webhook=HOOK)

    def leaky(request):
        raise httpx.ConnectError(f"cannot connect to {request.url}", request=request)

    def factory(prefs, config):
        return [TelegramChannel(config.telegram_bot_token, prefs["notify_telegram_chat"], client=mock_client(leaky, [])),
                DiscordChannel(prefs["notify_discord_webhook"],
                               client=mock_client(lambda r: httpx.Response(400, json={"message": HOOK}), []))]

    rep = run_pass(store, D - timedelta(hours=1), NotifierConfig(telegram_bot_token=TOKEN), channel_factory=factory)
    assert len(rep.failed) == 2
    blob = repr(rep)
    assert TOKEN not in blob and HOOK_TOKEN not in blob and HOOK not in blob


def test_httpx_log_lines_are_redacted(caplog):
    import logging
    caplog.set_level(logging.INFO, logger="httpx")
    logging.getLogger("httpx").info('HTTP Request: %s %s "%s"', "POST",
                                    f"https://api.telegram.org/bot{TOKEN}/sendMessage", "HTTP/1.1 200 OK")
    logging.getLogger("httpx").info("HTTP Request: POST %s", HOOK)
    text = caplog.text
    assert TOKEN not in text and HOOK_TOKEN not in text and "/bot***/sendMessage" in text


# --- real channel: email over a fake SMTP -------------------------------------------------------


class FakeSMTP:
    instances = []
    fail_login = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.calls, self.sent = [], []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append("quit")
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))
        if FakeSMTP.fail_login:
            raise smtplib.SMTPAuthenticationError(535, f"5.7.8 bad credentials for {user}:{password}".encode())

    def send_message(self, msg):
        self.calls.append("send_message")
        self.sent.append(msg)


class FakeSMTPSSL(FakeSMTP):
    pass


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.instances = []
    FakeSMTP.fail_login = False
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPSSL)
    return FakeSMTP


def email_cfg(**kw):
    base = dict(smtp_host="smtp.example.com", smtp_port=587, smtp_user="bot@example.com", smtp_password=PASSWORD)
    base.update(kw)
    return NotifierConfig(**base)


def test_email_starttls_login_send(fake_smtp):
    EmailChannel(email_cfg(), "me@example.com").send(MSG)
    [s] = fake_smtp.instances
    assert type(s) is FakeSMTP and (s.host, s.port) == ("smtp.example.com", 587) and s.timeout == 15
    assert s.calls == ["starttls", ("login", "bot@example.com", PASSWORD), "send_message", "quit"]
    [m] = s.sent
    assert m["Subject"] == MSG.subject and m["From"] == "bot@example.com" and m["To"] == "me@example.com"
    assert "https://unstop.example/1" in m.get_content()


def test_email_ssl_on_465_and_plain(fake_smtp):
    EmailChannel(email_cfg(smtp_port=465, smtp_starttls=False, smtp_from="logr@example.com"), "me@example.com").send(MSG)
    EmailChannel(email_cfg(smtp_port=25, smtp_starttls=False, smtp_password=None), "me@example.com").send(MSG)
    ssl_s, plain = fake_smtp.instances
    assert type(ssl_s) is FakeSMTPSSL and ssl_s.port == 465 and ssl_s.context is not None
    assert "starttls" not in ssl_s.calls and ssl_s.sent[0]["From"] == "logr@example.com"
    assert type(plain) is FakeSMTP and plain.calls == ["send_message", "quit"]  # no password: no login


def test_email_errors_wrapped_without_password(fake_smtp, monkeypatch):
    fake_smtp.fail_login = True
    with pytest.raises(ChannelError) as e:
        EmailChannel(email_cfg(), "me@example.com").send(MSG)
    assert PASSWORD not in str(e.value) and "SMTPAuthenticationError" in str(e.value)

    def refuse(*a, **kw):
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr(smtplib, "SMTP", refuse)
    with pytest.raises(ChannelError) as e:
        EmailChannel(email_cfg(), "me@example.com").send(MSG)
    assert str(e.value).startswith("email: ConnectionRefusedError")


def test_email_subject_newlines_flattened(fake_smtp):
    EmailChannel(email_cfg(), "me@example.com").send(
        Message(kind="test", key="t", subject="Hack\r\nBcc: evil@example.com", text="hi"))
    m = fake_smtp.instances[0].sent[0]
    assert m["Subject"] == "Hack Bcc: evil@example.com" and m["Bcc"] is None


@pytest.mark.parametrize("delta,text", [
    (timedelta(seconds=30), "1 minute"),
    (timedelta(minutes=45), "45 minutes"),
    (timedelta(minutes=90), "1 hour 30 minutes"),
    (timedelta(hours=2, minutes=59), "2 hours 59 minutes"),
    (timedelta(hours=23, minutes=59), "23 hours"),
    (timedelta(days=1), "1 day"),
    (timedelta(days=1, hours=5, minutes=40), "1 day 5 hours"),
    # live case: the 7-day reminder fired 6d 14h before the deadline
    (timedelta(days=6, hours=14, minutes=30), "6 days 14 hours"),
])
def test_relative_time_rounds_down(delta, text):
    from logr.notify import _rel
    assert _rel(delta) == text
