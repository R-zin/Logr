"""Deadline reminders, "deadline moved" alerts and sync-health alerts, delivered
by email, Telegram or Discord.

CONTRACT (Phase 5). Signatures and semantics here are fixed; ``scheduler.py``,
``cli.py`` and ``api/`` are written against them.

Who gets what
-------------
- Every user who tracks at least one hackathon and has at least one channel
  configured (prefs ``notify_email`` / ``notify_telegram_chat`` /
  ``notify_discord_webhook``) gets reminders and moved-alerts for *their*
  tracked hackathons.
- Admins (``run_pass(admins=...)``) also get health alerts about the sync.

Server-wide secrets come from the environment (``NotifierConfig.from_env``):
SMTP credentials and the Telegram bot token. Per-user *destinations* are prefs.
The Discord webhook is per user and is itself a secret: display layers mask it
(``searches.SECRET_PREF_KEYS``) and nothing here may log it.

Reminder rules
--------------
- Deadlines come from ``tracking.upcoming(best, tracked, now)`` where ``best`` is
  ``dedupe.best_records(store.all(include_delisted=True))[uid]`` (falling back to
  ``store.get(uid)``): status-aware, includes todos, excludes terminal statuses.
- Offsets come from pref ``remind_offsets`` (``parse_offsets``), default
  ``DEFAULT_OFFSETS`` = 7d, 1d, 3h.
- For a deadline ``d`` an offset ``o`` is *due* when ``d.at - o <= now < d.at``.
  Only the **smallest** due offset produces a message; larger due offsets for
  the same deadline are marked sent without sending (no 3-message burst when a
  hackathon is tracked 2 hours before its deadline).
- Dedupe key: ``f"rem:{uid}:{d.label}:{d.at.isoformat()}:{int(o.total_seconds())}"``.
  If the deadline moves, ``d.at`` changes, so the reminder schedule restarts.

Moved alerts
------------
For each tracked uid of the user, every ``date_changes`` row (Store table) with
``changed_at`` after the user's previous pass (``Outbox.last_pass``; on the very
first pass, after the hackathon was tracked) yields one message, e.g.
"Inceptrix 3.0: registration closes moved from 24 Dec 23:59 to 31 Dec 23:59
(extended by 7d)". Key: ``f"moved:{date_changes.id}"``.

Health alerts (admins only)
---------------------------
From ``Store.last_runs()``: a source whose latest run has ``error`` → key
``f"health:error:{source}:{run_id}"``; a latest run with ``fetched == 0`` and no
error → ``f"health:empty:{source}:{run_id}"`` (the endpoint probably changed);
no successful run of any source in the last 24h → ``f"health:stale:{YYYY-MM-DD}"``.

Delivery
--------
Each message goes to every channel the user has. A message is marked sent per
channel only when that channel's ``send`` succeeds, so a failed channel retries
on the next pass while the others don't repeat. Times in message text use the
user's ``tz`` pref (else UTC) and every message for a hackathon includes its URL.
"""

from __future__ import annotations

import json
import logging
import os
import re
import smtplib
import sqlite3
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Literal, Protocol
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from logr.dedupe import best_records
from logr.models import Deadline, Hackathon
from logr.searches import Preferences
from logr.store import Store
from logr.tracking import TERMINAL, Tracker, upcoming

DEFAULT_OFFSETS: tuple[timedelta, ...] = (timedelta(days=7), timedelta(days=1), timedelta(hours=3))

MessageKind = Literal["reminder", "moved", "health", "test"]

MAX_OFFSET = timedelta(days=60)
MAX_OFFSETS = 6
STALE_AFTER = timedelta(hours=24)
RETRY_FOR = timedelta(days=7)  # an undelivered moved alert is retried this long
SMTP_TIMEOUT = 15.0
HTTP_TIMEOUT = 10.0
TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_MAX = 4096
DISCORD_MAX = 2000

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})
_UNITS = {"m": timedelta(minutes=1), "h": timedelta(hours=1), "d": timedelta(days=1), "w": timedelta(weeks=1)}
_OFFSET_RE = re.compile(r"(\d{1,6})\s*([mhdw])")
_VALIDATED_KEYS = frozenset({"notify_email", "notify_telegram_chat", "notify_discord_webhook", "remind_offsets"})
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}")
_TG_ID_RE = re.compile(r"-?[1-9]\d{0,19}")
_TG_CHANNEL_RE = re.compile(r"@[A-Za-z][A-Za-z0-9_]{3,31}")
_DISCORD_RE = re.compile(
    r"https://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/api/webhooks/(\d{5,25})/([A-Za-z0-9_-]{20,128})")


@dataclass
class Message:
    kind: MessageKind
    key: str  # dedupe key (see module docstring); "test:<timestamp>" for tests
    subject: str  # one line, used as email subject and bold first line elsewhere
    text: str  # plain text body (may contain newlines); no markup
    url: str | None = None  # link to the hackathon (source page)
    uid: str | None = None  # hackathon uid, when the message is about one


class ChannelError(Exception):
    """A channel failed to deliver (network, auth, bad destination). The message
    is retried on the next pass."""


class Channel(Protocol):
    name: Literal["email", "telegram", "discord"]

    def send(self, msg: Message) -> None:
        """Deliver or raise ChannelError. Must not log secrets."""
        ...


@dataclass
class NotifierConfig:
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None  # defaults to smtp_user
    smtp_starttls: bool = True  # False → implicit TLS when port is 465, plain otherwise
    telegram_bot_token: str | None = None
    base_url: str | None = None  # LOGR_BASE_URL; when set, messages also link the dashboard page

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> NotifierConfig:
        """LOGR_SMTP_HOST, LOGR_SMTP_PORT, LOGR_SMTP_USER, LOGR_SMTP_PASSWORD,
        LOGR_SMTP_FROM, LOGR_SMTP_STARTTLS (true/false), LOGR_TELEGRAM_BOT_TOKEN,
        LOGR_BASE_URL. ``env`` defaults to os.environ. Bad port → ValueError."""
        env = os.environ if env is None else env

        def get(name: str) -> str | None:
            v = (env.get(name) or "").strip()
            return v or None

        port_text = get("LOGR_SMTP_PORT")
        if port_text is None:
            port = 587
        else:
            try:
                port = int(port_text)
            except ValueError:
                raise ValueError(f"LOGR_SMTP_PORT must be a number, got {port_text!r}") from None
            if not 1 <= port <= 65535:
                raise ValueError(f"LOGR_SMTP_PORT out of range: {port}")
        tls_text = get("LOGR_SMTP_STARTTLS")
        if tls_text is None:
            starttls = port != 465  # 465 is implicit TLS; STARTTLS there would fail
        elif tls_text.lower() in _TRUE:
            starttls = True
        elif tls_text.lower() in _FALSE:
            starttls = False
        else:
            raise ValueError(f"LOGR_SMTP_STARTTLS must be true or false, got {tls_text!r}")
        base = get("LOGR_BASE_URL")
        return cls(
            smtp_host=get("LOGR_SMTP_HOST"),
            smtp_port=port,
            smtp_user=get("LOGR_SMTP_USER"),
            smtp_password=env.get("LOGR_SMTP_PASSWORD") or None,  # not stripped: may contain spaces
            smtp_from=get("LOGR_SMTP_FROM"),
            smtp_starttls=starttls,
            telegram_bot_token=get("LOGR_TELEGRAM_BOT_TOKEN"),
            base_url=base.rstrip("/") if base else None,
        )

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and (self.smtp_from or self.smtp_user))

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token)

    def __repr__(self) -> str:  # never show the SMTP password or the bot token
        return (f"NotifierConfig(smtp_host={self.smtp_host!r}, smtp_port={self.smtp_port!r}, "
                f"smtp_user={self.smtp_user!r}, smtp_password={'***' if self.smtp_password else None}, "
                f"smtp_from={self.smtp_from!r}, smtp_starttls={self.smtp_starttls!r}, "
                f"telegram_bot_token={'***' if self.telegram_bot_token else None}, base_url={self.base_url!r})")


def parse_offsets(text: str | None) -> tuple[timedelta, ...]:
    """"7d,1d,3h" → (7d, 1d, 3h), sorted largest first, duplicates removed.
    Units: m, h, d, w. None/empty → DEFAULT_OFFSETS. Anything else (zero,
    negative, unknown unit, > 60d, more than 6 offsets) → ValueError."""
    if text is None or not text.strip():
        return DEFAULT_OFFSETS
    out: list[timedelta] = []
    for part in text.split(","):
        part = part.strip().lower()
        m = _OFFSET_RE.fullmatch(part)
        if not m:
            raise ValueError(f"bad reminder offset {part!r}: use a number and a unit m, h, d or w, e.g. 7d,1d,3h")
        o = int(m.group(1)) * _UNITS[m.group(2)]
        if o <= timedelta(0):
            raise ValueError(f"reminder offset {part!r} must be greater than zero")
        if o > MAX_OFFSET:
            raise ValueError(f"reminder offset {part!r} is more than 60 days")
        if o not in out:
            out.append(o)
    if len(out) > MAX_OFFSETS:
        raise ValueError(f"at most {MAX_OFFSETS} reminder offsets")
    return tuple(sorted(out, reverse=True))


def validate_pref(key: str, value: str) -> str:
    """Validate and normalize a Phase 5 pref; returns the value to store.
    notify_email: a plausible single address (lower-cased domain).
    notify_telegram_chat: an integer chat id (may be negative) or @channelname.
    notify_discord_webhook: https://discord.com/api/webhooks/<id>/<token> (also
    discordapp.com; canary./ptb. subdomains allowed).
    remind_offsets: anything ``parse_offsets`` accepts; normalized to e.g. "7d,1d,3h".
    Raises ValueError with a user-facing message; KeyError for other keys."""
    if key not in _VALIDATED_KEYS:
        raise KeyError(key)
    value = (value or "").strip()
    if key == "notify_email":
        if len(value) > 254 or not _EMAIL_RE.fullmatch(value):
            raise ValueError("notify_email must be a single email address, e.g. you@example.com")
        local, _, domain = value.rpartition("@")
        return f"{local}@{domain.lower()}"
    if key == "notify_telegram_chat":
        if _TG_ID_RE.fullmatch(value) or _TG_CHANNEL_RE.fullmatch(value):
            return value
        raise ValueError("notify_telegram_chat must be a numeric chat id (e.g. 123456789 or -1001234567890) "
                         "or a public @channelname")
    if key == "notify_discord_webhook":
        m = _DISCORD_RE.fullmatch(value)
        if not m:
            # never echo the value: it is a secret
            raise ValueError("notify_discord_webhook must look like https://discord.com/api/webhooks/<id>/<token>")
        return value
    # remind_offsets
    return ",".join(_fmt_offset(o) for o in parse_offsets(value))


def mask_secret(value: str) -> str:
    """For display of secret prefs: keep the scheme+host and the last 4 chars,
    e.g. "https://discord.com/…/•••••abcd"."""
    value = value or ""
    tail = value[-4:] if len(value) >= 12 else ""
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        parts, host = None, None
    if parts is not None and parts.scheme in ("http", "https") and host:
        return f"{parts.scheme}://{host}/…/•••••{tail}"
    return f"•••••{tail}"


def channels_for(prefs: Mapping[str, str], config: NotifierConfig) -> list[Channel]:
    """Channels for one user from their prefs. Email needs ``config.email_enabled``,
    Telegram needs ``config.telegram_enabled``; a configured destination whose
    server side isn't configured is skipped (``run_pass`` reports it)."""
    out: list[Channel] = []
    email = (prefs.get("notify_email") or "").strip()
    if email and config.email_enabled:
        out.append(EmailChannel(config, email))
    chat = (prefs.get("notify_telegram_chat") or "").strip()
    if chat and config.telegram_enabled:
        out.append(TelegramChannel(config.telegram_bot_token or "", chat))
    hook = (prefs.get("notify_discord_webhook") or "").strip()
    if hook:
        out.append(DiscordChannel(hook))
    return out


class Outbox:
    """Remembers what was sent so passes are idempotent. Tables (user_id-scoped):
    ``notify_sent(user_id, key, channel, sent_at)`` with a unique
    (user_id, key, channel), and ``notify_state(user_id, last_pass_at)``."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self.db.executescript(_OUTBOX_SCHEMA)
        self.db.commit()

    def was_sent(self, user_id: str, key: str, channel: str) -> bool:
        row = self.db.execute("SELECT 1 FROM notify_sent WHERE user_id=? AND key=? AND channel=?",
                              (user_id, key, channel)).fetchone()
        return row is not None

    def mark_sent(self, user_id: str, key: str, channel: str, at: datetime) -> None:
        """Idempotent."""
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO notify_sent (user_id, key, channel, sent_at) VALUES (?,?,?,?)",
                            (user_id, key, channel, _iso(at)))

    def last_pass(self, user_id: str) -> datetime | None:
        row = self.db.execute("SELECT last_pass_at FROM notify_state WHERE user_id=?", (user_id,)).fetchone()
        return _parse_dt(row[0]) if row and row[0] else None

    def set_last_pass(self, user_id: str, at: datetime) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO notify_state (user_id, last_pass_at) VALUES (?,?)"
                " ON CONFLICT(user_id) DO UPDATE SET last_pass_at=excluded.last_pass_at",
                (user_id, _iso(at)))


def collect(store: Store, user_id: str, now: datetime, *, since: datetime | None) -> list[Message]:
    """Reminders + moved alerts for one user (rules in the module docstring),
    soonest deadline first. ``since`` is the previous pass time (None = first
    pass). Pure apart from reading the store; dedupe against the Outbox happens
    in ``run_pass`` (but larger-offset suppression is reflected in the result via
    ``suppressed_keys``)."""
    messages, _ = _collect_user(store, user_id, now, since)
    return messages


def suppressed_keys(store: Store, user_id: str, now: datetime) -> list[str]:
    """Reminder keys for larger due offsets that ``collect`` skipped (see the
    "smallest due offset" rule); ``run_pass`` marks them sent on every channel."""
    _, suppressed = _collect_user(store, user_id, now, None, moved=False)
    return suppressed


def health_messages(store: Store, now: datetime) -> list[Message]:
    now = _aware(now)
    out: list[Message] = []
    for r in store.last_runs():
        source, run_id = r["source"], r["id"]
        started = _parse_dt(r["started_at"]) if r["started_at"] else None
        when = f" (run started {_fmt_long(started, timezone.utc)})" if started else ""
        if r["error"]:
            err = _one_line(str(r["error"]))[:300]
            out.append(Message(
                kind="health", key=f"health:error:{source}:{run_id}",
                subject=f"⚠️ Logr sync: {source} failed",
                text=f"The latest {source} sync failed{when}:\n{err}\n\nListings from {source} are not being updated."))
        elif r["finished_at"] and not r["fetched"]:
            # an unfinished run (finished_at NULL) is still in progress: not "empty"
            out.append(Message(
                kind="health", key=f"health:empty:{source}:{run_id}",
                subject=f"⚠️ Logr sync: {source} returned nothing",
                text=f"The latest {source} sync finished without error but fetched 0 listings{when}.\n"
                     "The source's endpoint or page layout has probably changed."))
    ok_times = [
        _parse_dt(row[0]) for row in store.db.execute(
            "SELECT r.finished_at FROM sync_runs r JOIN (SELECT MAX(id) id FROM sync_runs"
            " WHERE error IS NULL AND finished_at IS NOT NULL GROUP BY source) m ON r.id = m.id")
    ]
    any_runs = store.db.execute("SELECT 1 FROM sync_runs LIMIT 1").fetchone() is not None
    if any_runs and not any(t >= now - STALE_AFTER for t in ok_times):
        last = max(ok_times) if ok_times else None
        detail = f"The last successful sync finished {_fmt_long(last, timezone.utc)}." if last else \
            "No sync has ever completed successfully."
        out.append(Message(
            kind="health", key=f"health:stale:{now.astimezone(timezone.utc):%Y-%m-%d}",
            subject="⚠️ Logr sync: no successful sync in 24h",
            text=f"No source has synced successfully in the last 24 hours. {detail}\n"
                 "Is the scheduler (`logr daemon`) running?"))
    return out


@dataclass
class PassReport:
    users: int = 0  # users considered
    sent: int = 0  # successful (message, channel) deliveries
    failed: list[str] = field(default_factory=list)  # "user/channel: error" (no secrets)
    skipped_unconfigured: list[str] = field(default_factory=list)  # "user: telegram (no bot token)"
    would_send: list[tuple[str, str, Message]] = field(default_factory=list)  # dry run: (user, channel, msg)


def run_pass(
    store: Store,
    now: datetime,
    config: NotifierConfig,
    *,
    admins: list[str] = (),
    dry_run: bool = False,
    channel_factory=channels_for,
) -> PassReport:
    """One notification pass over every user (distinct user_id in the tracking
    table, plus admins). For each: prefs → channels; collect(since=last_pass);
    send every (message, channel) not yet sent; mark sent on success; mark
    suppressed keys; set last_pass = now. Admins additionally get
    health_messages. ``dry_run``: send nothing, mark nothing, don't advance
    last_pass; fill ``would_send``. ``channel_factory`` is injectable for tests."""
    now = _aware(now)
    report = PassReport()
    outbox = Outbox(store.db)
    Tracker(store.db)  # make sure the tracking tables exist
    users = [r[0] for r in store.db.execute("SELECT DISTINCT user_id FROM tracked ORDER BY user_id")]
    admin_set = list(dict.fromkeys(a for a in admins if a))
    users = list(dict.fromkeys([*users, *admin_set]))
    best = _best_map(store)
    health = health_messages(store, now) if admin_set else []

    for user in users:
        report.users += 1
        prefs = Preferences(store.db, user).all()
        report.skipped_unconfigured.extend(f"{user}: {u}" for u in _unavailable(prefs, config))
        channels = list(channel_factory(prefs, config))
        try:
            msgs, suppressed = _collect_user(store, user, now, outbox.last_pass(user), best=best, prefs=prefs)
            if user in admin_set:
                msgs = msgs + health
            names = {ch.name for ch in channels}
            # moved alerts that a channel failed to deliver on an earlier pass
            fresh = {m.key for m in msgs}
            have = set(fresh)
            retries: dict[str, set[str]] = {}
            for msg, ch_name in outbox._retries(user, now):
                if ch_name not in names:
                    continue
                retries.setdefault(msg.key, set()).add(ch_name)
                if msg.key not in have:
                    msgs.append(msg)
                    have.add(msg.key)
            for msg in msgs:
                out_msg = _with_dashboard(msg, config.base_url)
                for ch in channels:
                    if msg.key in retries and msg.key not in fresh and ch.name not in retries[msg.key]:
                        continue  # a retried alert goes only to the channel(s) that missed it
                    if outbox.was_sent(user, msg.key, ch.name):
                        continue
                    if dry_run:
                        report.would_send.append((user, ch.name, out_msg))
                        continue
                    try:
                        ch.send(out_msg)
                    except Exception as exc:  # noqa: BLE001 - one bad channel must not stop the pass
                        report.failed.append(f"{user}/{ch.name}: {_safe_error(exc, prefs, config)}")
                        _keep_for_retry(outbox, user, msg, ch.name, now)
                        continue
                    outbox.mark_sent(user, msg.key, ch.name, now)
                    outbox._drop_retry(user, msg.key, ch.name)
                    report.sent += 1
            if not dry_run:
                for key in suppressed:
                    for ch in channels:
                        outbox.mark_sent(user, key, ch.name, now)
                outbox.set_last_pass(user, now)
        finally:
            for ch in channels:
                close = getattr(ch, "close", None)
                if callable(close):
                    close()
    return report


def send_test(prefs: Mapping[str, str], config: NotifierConfig, *, channel_factory=channels_for) -> dict[str, str]:
    """Send a "Logr test notification" through each of the user's channels.
    Returns {channel: "ok" | error message}. Includes configured-but-unavailable
    channels as e.g. {"telegram": "server has no LOGR_TELEGRAM_BOT_TOKEN"}."""
    now = datetime.now(timezone.utc)
    out: dict[str, str] = {}
    unavailable = dict(_unavailable_reasons(prefs, config))
    channels = {ch.name: ch for ch in channel_factory(prefs, config)}
    msg = Message(
        kind="test", key=f"test:{now.isoformat()}", subject="Logr test notification",
        text="This is a test notification from Logr. If you can read it, deadline reminders will reach you here.")
    msg = _with_dashboard(msg, config.base_url)
    for name in ("email", "telegram", "discord"):
        if name in unavailable:
            out[name] = unavailable[name]
            continue
        ch = channels.get(name)
        if ch is None:
            continue
        try:
            ch.send(msg)
            out[name] = "ok"
        except Exception as exc:  # noqa: BLE001
            out[name] = _safe_error(exc, prefs, config)
        finally:
            close = getattr(ch, "close", None)
            if callable(close):
                close()
    return out


# --- private helpers ---------------------------------------------------------------

_OUTBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS notify_sent (
    user_id TEXT NOT NULL,
    key     TEXT NOT NULL,
    channel TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    UNIQUE (user_id, key, channel)
);
CREATE TABLE IF NOT EXISTS notify_state (
    user_id      TEXT PRIMARY KEY,
    last_pass_at TEXT
);
-- moved alerts a channel failed to deliver; collect() won't produce them again
-- once last_pass has moved past the change, so they're kept here for retrying.
CREATE TABLE IF NOT EXISTS notify_retry (
    user_id    TEXT NOT NULL,
    key        TEXT NOT NULL,
    channel    TEXT NOT NULL,
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, key, channel)
);
"""


def _outbox_retries(self: Outbox, user_id: str, now: datetime) -> list[tuple[Message, str]]:
    """Undelivered moved alerts for ``user_id`` (expired ones are dropped)."""
    cutoff = now - RETRY_FOR
    out: list[tuple[Message, str]] = []
    expired: list[tuple[str, str]] = []
    for key, channel, blob, created in self.db.execute(
            "SELECT key, channel, message, created_at FROM notify_retry WHERE user_id=? ORDER BY created_at, key",
            (user_id,)).fetchall():
        if _parse_dt(created) < cutoff:
            expired.append((key, channel))
            continue
        try:
            out.append((Message(**json.loads(blob)), channel))
        except (TypeError, ValueError):
            expired.append((key, channel))
    for key, channel in expired:
        self._drop_retry(user_id, key, channel)
    return out


def _outbox_queue_retry(self: Outbox, user_id: str, msg: Message, channel: str, at: datetime) -> None:
    blob = json.dumps({"kind": msg.kind, "key": msg.key, "subject": msg.subject, "text": msg.text,
                       "url": msg.url, "uid": msg.uid})
    with self.db:
        self.db.execute("INSERT OR IGNORE INTO notify_retry (user_id, key, channel, message, created_at)"
                        " VALUES (?,?,?,?,?)", (user_id, msg.key, channel, blob, _iso(at)))


def _outbox_drop_retry(self: Outbox, user_id: str, key: str, channel: str) -> None:
    with self.db:
        self.db.execute("DELETE FROM notify_retry WHERE user_id=? AND key=? AND channel=?", (user_id, key, channel))


Outbox._retries = _outbox_retries  # type: ignore[attr-defined]
Outbox._queue_retry = _outbox_queue_retry  # type: ignore[attr-defined]
Outbox._drop_retry = _outbox_drop_retry  # type: ignore[attr-defined]


def _keep_for_retry(outbox: Outbox, user: str, msg: Message, channel: str, now: datetime) -> None:
    # Reminders and health alerts are re-derived every pass while still relevant;
    # only moved alerts would be lost once last_pass moves past the change.
    if msg.kind == "moved":
        outbox._queue_retry(user, msg, channel, now)  # type: ignore[attr-defined]


def _iso(dt: datetime) -> str:
    return _aware(dt).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_dt(s: str) -> datetime:
    d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _parse_dt_opt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return _parse_dt(s)
    except ValueError:
        return None


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError("now must be timezone-aware")
    return dt


def _fmt_offset(o: timedelta) -> str:
    secs = int(o.total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs % size == 0:
            return f"{secs // size}{unit}"
    return f"{secs // 60}m"


def _offsets_for(prefs: Mapping[str, str]) -> tuple[timedelta, ...]:
    try:
        return parse_offsets(prefs.get("remind_offsets"))
    except ValueError:
        return DEFAULT_OFFSETS  # a bad stored value must not stop reminders


def _tz_for(prefs: Mapping[str, str]):
    name = (prefs.get("tz") or "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return timezone.utc


def _tzname(dt: datetime) -> str:
    return dt.tzname() or "UTC"


def _fmt_long(dt: datetime, tz) -> str:
    """"Thu 24 Dec 2026, 23:59 IST"."""
    d = dt.astimezone(tz)
    return f"{d:%a} {d.day} {d:%b %Y}, {d:%H:%M} {_tzname(d)}"


def _fmt_short(dt: datetime, tz) -> str:
    """"24 Dec 23:59"."""
    d = dt.astimezone(tz)
    return f"{d.day} {d:%b %H:%M}"


def _plural(n: int, unit: str) -> str:
    return f"{n} {unit}" if n == 1 else f"{n} {unit}s"


def _rel(delta: timedelta) -> str:
    """Human "time until", always rounded DOWN so a reminder never overstates
    how long you have: "45 minutes", "3 hours", "1 day 5 hours", "6 days 14 hours"."""
    mins = max(0, int(delta.total_seconds() // 60))
    if mins < 60:
        return _plural(max(mins, 1), "minute")
    days, rem = divmod(mins, 1440)
    hours = rem // 60
    if days == 0:
        left = mins % 60
        if hours < 3 and left:  # close to the deadline, minutes matter
            return f"{_plural(hours, 'hour')} {_plural(left, 'minute')}"
        return _plural(hours, "hour")
    return _plural(days, "day") + (f" {_plural(hours, 'hour')}" if hours else "")


def _short_delta(delta: timedelta) -> str:
    """"7d", "1d 3h", "3h", "45m"."""
    mins = round(abs(delta.total_seconds()) / 60)
    d, rem = divmod(mins, 1440)
    h, m = divmod(rem, 60)
    parts = [f"{d}d"] if d else []
    if h:
        parts.append(f"{h}h")
    if m and not d:
        parts.append(f"{m}m")
    return " ".join(parts) or "0m"


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def _best_map(store: Store) -> dict[str, Hackathon]:
    return best_records(store.all(include_delisted=True))


_FIELD_LABELS = {
    "registration_opens_at": "registration opens",
    "registration_closes_at": "registration closes",
    "submission_deadline": "submission due",
    "starts_at": "start",
    "ends_at": "end",
}


def _reminder(h: Hackathon | None, uid: str, d: Deadline, now: datetime, tz) -> Message:
    title = _one_line(h.title) if h else uid
    rel = _rel(d.at - now)
    if d.label.startswith("todo: "):
        what = f"To-do “{d.label[len('todo: '):]}” is due"
        subj_what = f"to-do due in {rel}: {d.label[len('todo: '):]}"
    else:
        what = f"{d.label[:1].upper()}{d.label[1:]}"
        subj_what = f"{d.label} in {rel}"
    approx = " (approximate)" if d.approximate else ""
    lines = [f"{title}: {what} in {rel}.",
             f"When: {_fmt_long(d.at, tz)}{approx}"]
    if h:
        lines.append(f"Link: {h.url}")
    return Message(kind="reminder", key=_rem_key(uid, d, 0), subject=_one_line(f"⏰ {title} — {subj_what}"),
                   text="\n".join(lines), url=h.url if h else None, uid=uid)


def _rem_key(uid: str, d: Deadline, offset_seconds: int) -> str:
    return f"rem:{uid}:{d.label}:{d.at.isoformat()}:{offset_seconds}"


def _fmt_round_value(v: str | None, tz) -> str:
    if v is None:
        return "(none)"
    parts = []
    for p in v.split("→"):
        p = p.strip()
        dt = _parse_dt_opt(p) if p and p != "—" else None
        parts.append(_fmt_short(dt, tz) if dt else p)
    return " → ".join(parts)


def _moved(row: sqlite3.Row, h: Hackathon | None, uid: str, tz) -> tuple[Message, datetime | None]:
    title = _one_line(h.title) if h else uid
    fld, old_v, new_v = row["field"], row["old_value"], row["new_value"]
    sort_at: datetime | None = None
    if fld.startswith("round: "):
        name = fld[len("round: "):]
        head = f"{title}: round “{name}” timeline changed"
        detail = [f"Was: {_fmt_round_value(old_v, tz)}", f"Now: {_fmt_round_value(new_v, tz)}"]
        if new_v:
            ends = [_parse_dt_opt(p.strip()) for p in new_v.split("→")]
            sort_at = next((e for e in reversed(ends) if e), None)
    else:
        label = _FIELD_LABELS.get(fld, fld.replace("_", " "))
        old_dt, new_dt = _parse_dt_opt(old_v), _parse_dt_opt(new_v)
        sort_at = new_dt
        old_s = _fmt_short(old_dt, tz) if old_dt else (old_v or "(none)")
        if new_dt is None and not new_v:
            head = f"{title}: {label} date removed (was {old_s})"
            detail = []
        else:
            new_s = f"{_fmt_short(new_dt, tz)} {_tzname(new_dt.astimezone(tz))}" if new_dt else new_v
            head = f"{title}: {label} moved from {old_s} to {new_s}"
            if old_dt and new_dt and old_dt != new_dt:
                delta = new_dt - old_dt
                head += (f" (extended by {_short_delta(delta)})" if delta > timedelta(0)
                         else f" (moved earlier by {_short_delta(delta)})")
            detail = [f"New date: {_fmt_long(new_dt, tz)}"] if new_dt else []
    lines = [head, *detail]
    if h:
        lines.append(f"Link: {h.url}")
    msg = Message(kind="moved", key=f"moved:{row['id']}", subject=_one_line(f"📅 Deadline moved: {title}"),
                  text="\n".join(lines), url=h.url if h else None, uid=uid)
    return msg, sort_at


def _collect_user(store: Store, user_id: str, now: datetime, since: datetime | None, *,
                  best: dict[str, Hackathon] | None = None, prefs: Mapping[str, str] | None = None,
                  moved: bool = True) -> tuple[list[Message], list[str]]:
    """(messages soonest deadline first, suppressed reminder keys) for one user."""
    now = _aware(now)
    if prefs is None:
        prefs = Preferences(store.db, user_id).all()
    if best is None:
        best = _best_map(store)
    tz = _tz_for(prefs)
    offsets = _offsets_for(prefs)
    tracker = Tracker(store.db, user_id)
    items: list[tuple[datetime, int, str, Message]] = []
    suppressed: list[str] = []
    for t in tracker.all():
        h = best.get(t.uid) or store.get(t.uid)
        for d in upcoming(h, t, now):
            due = [o for o in offsets if d.at - o <= now < d.at]
            if not due:
                continue
            smallest = min(due)
            msg = _reminder(h, t.uid, d, now, tz)
            msg.key = _rem_key(t.uid, d, int(smallest.total_seconds()))
            items.append((d.at, 0, msg.key, msg))
            suppressed.extend(_rem_key(t.uid, d, int(o.total_seconds())) for o in due if o != smallest)
        if not moved or t.status in TERMINAL:
            continue
        # changes since the previous pass, but never from before the user tracked it
        after = max(since, t.added_at) if since is not None else t.added_at
        own = store.get(t.uid) or h
        for row in store.db.execute(
                "SELECT id, field, old_value, new_value, changed_at FROM date_changes WHERE uid=? ORDER BY id",
                (t.uid,)).fetchall():
            changed = _parse_dt_opt(row["changed_at"])
            if changed is None or changed <= after or changed > now:
                continue
            msg, sort_at = _moved(row, own, t.uid, tz)
            items.append((sort_at or now, 1, msg.key, msg))
    items.sort(key=lambda x: (x[0], x[1], x[2]))
    return [m for *_, m in items], suppressed


def _with_dashboard(msg: Message, base_url: str | None) -> Message:
    if not base_url or not msg.uid:
        return msg
    link = f"{base_url.rstrip('/')}/hackathon/?uid={quote(msg.uid, safe='')}"
    return replace(msg, text=f"{msg.text}\nIn Logr: {link}")


def _unavailable_reasons(prefs: Mapping[str, str], config: NotifierConfig) -> list[tuple[str, str]]:
    out = []
    if (prefs.get("notify_email") or "").strip() and not config.email_enabled:
        out.append(("email", "server has no SMTP configured (LOGR_SMTP_HOST and LOGR_SMTP_FROM/LOGR_SMTP_USER)"))
    if (prefs.get("notify_telegram_chat") or "").strip() and not config.telegram_enabled:
        out.append(("telegram", "server has no LOGR_TELEGRAM_BOT_TOKEN"))
    return out


def _unavailable(prefs: Mapping[str, str], config: NotifierConfig) -> list[str]:
    short = {"email": "email (no SMTP server)", "telegram": "telegram (no bot token)"}
    return [short[name] for name, _ in _unavailable_reasons(prefs, config)]


def _secrets(prefs: Mapping[str, str], config: NotifierConfig) -> list[str]:
    vals = [config.smtp_password, config.telegram_bot_token, prefs.get("notify_discord_webhook")]
    hook = prefs.get("notify_discord_webhook") or ""
    m = _DISCORD_RE.search(hook)
    if m:
        vals.append(m.group(2))
    return [v for v in vals if v]


def _redact(text: str, secrets: list[str]) -> str:
    for s in sorted(secrets, key=len, reverse=True):
        if s:
            text = text.replace(s, "***")
    return _SECRET_PATTERNS.sub(_redact_match, text)


def _safe_error(exc: BaseException, prefs: Mapping[str, str], config: NotifierConfig) -> str:
    """Error text for reports: ChannelError messages (already sanitized by our
    channels, redacted again to be sure); anything else only by type, since
    library exceptions can embed URLs that carry tokens."""
    if isinstance(exc, ChannelError):
        return _redact(str(exc), _secrets(prefs, config))[:500]
    return f"unexpected {type(exc).__name__}"


# Telegram bot tokens in URLs and Discord webhook tokens.
_SECRET_PATTERNS = re.compile(r"(/bot)\d+:[A-Za-z0-9_-]+|(/api/webhooks/\d+/)[A-Za-z0-9_-]+")


def _redact_match(m: re.Match[str]) -> str:
    return f"{m.group(1) or m.group(2)}***"


class _RedactSecretsFilter(logging.Filter):
    """httpx logs every request URL at INFO; Telegram's URL contains the bot
    token and a Discord webhook URL is itself the credential."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        clean = _SECRET_PATTERNS.sub(_redact_match, text)
        if clean != text:
            record.msg, record.args = clean, None
        return True


if not any(isinstance(f, _RedactSecretsFilter) for f in logging.getLogger("httpx").filters):
    logging.getLogger("httpx").addFilter(_RedactSecretsFilter())


def _body(msg: Message) -> str:
    text = msg.text
    if msg.url and msg.url not in text:
        text = f"{text}\nLink: {msg.url}"
    return text


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- channels ----------------------------------------------------------------------


class EmailChannel:
    name: Literal["email"] = "email"

    def __init__(self, config: NotifierConfig, to: str, *, timeout: float = SMTP_TIMEOUT) -> None:
        self.config = config
        self.to = to
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"EmailChannel(to={self.to!r}, host={self.config.smtp_host!r})"

    def send(self, msg: Message) -> None:
        c = self.config
        if not c.email_enabled:
            raise ChannelError("email: server has no SMTP configured")
        em = EmailMessage()
        em["Subject"] = _one_line(msg.subject)
        em["From"] = c.smtp_from or c.smtp_user or ""
        em["To"] = self.to
        em.set_content(_body(msg))
        try:
            ctx = ssl.create_default_context()
            if c.smtp_starttls:
                smtp = smtplib.SMTP(c.smtp_host, c.smtp_port, timeout=self.timeout)
            elif c.smtp_port == 465:
                smtp = smtplib.SMTP_SSL(c.smtp_host, c.smtp_port, timeout=self.timeout, context=ctx)
            else:
                smtp = smtplib.SMTP(c.smtp_host, c.smtp_port, timeout=self.timeout)
            with smtp:
                if c.smtp_starttls:
                    smtp.starttls(context=ctx)
                if c.smtp_user and c.smtp_password:
                    smtp.login(c.smtp_user, c.smtp_password)
                smtp.send_message(em)
        except Exception as exc:  # noqa: BLE001 - smtplib, socket, ssl, encoding errors
            detail = _redact(_one_line(str(exc)), [s for s in (c.smtp_password,) if s])[:300]
            raise ChannelError(f"email: {type(exc).__name__}: {detail}" if detail
                               else f"email: {type(exc).__name__}") from None


class _HttpChannel:
    def __init__(self, client: httpx.Client | None) -> None:
        self._own = client is None
        self.client = client if client is not None else httpx.Client(timeout=HTTP_TIMEOUT)

    def close(self) -> None:
        if self._own:
            self.client.close()

    def _post(self, url: str, payload: dict) -> httpx.Response:
        try:
            return self.client.post(url, json=payload)
        except httpx.HTTPError as exc:
            # str(exc) and exc.request carry the URL, which holds the secret
            raise ChannelError(f"{self.name}: network error ({type(exc).__name__})") from None  # type: ignore[attr-defined]


class TelegramChannel(_HttpChannel):
    name: Literal["telegram"] = "telegram"

    def __init__(self, token: str, chat_id: str, *, client: httpx.Client | None = None) -> None:
        super().__init__(client)
        self._token = token
        self.chat_id = chat_id

    def __repr__(self) -> str:
        return f"TelegramChannel(chat_id={self.chat_id!r})"

    def send(self, msg: Message) -> None:
        text = _truncate(f"{_one_line(msg.subject)}\n\n{_body(msg)}", TELEGRAM_MAX)
        resp = self._post(f"{TELEGRAM_API}/bot{self._token}/sendMessage",
                          {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True})
        try:
            data = resp.json()
        except ValueError:
            data = None
        ok = resp.is_success and isinstance(data, dict) and data.get("ok") is True
        if not ok:
            desc = data.get("description") if isinstance(data, dict) else None
            desc = _redact(_one_line(str(desc)), [self._token])[:300] if desc else "no description"
            raise ChannelError(f"telegram: HTTP {resp.status_code}: {desc}")


class DiscordChannel(_HttpChannel):
    name: Literal["discord"] = "discord"

    def __init__(self, webhook_url: str, *, client: httpx.Client | None = None) -> None:
        super().__init__(client)
        self._url = webhook_url

    def __repr__(self) -> str:
        return f"DiscordChannel(webhook={mask_secret(self._url)!r})"

    def send(self, msg: Message) -> None:
        subject = re.sub(r"([\\*_~`|>])", r"\\\1", _one_line(msg.subject))
        content = _truncate(f"**{subject}**\n{_body(msg)}", DISCORD_MAX)
        resp = self._post(self._url, {"content": content, "allowed_mentions": {"parse": []}})
        if resp.status_code in (200, 204):
            return
        detail = ""
        try:
            data = resp.json()
            if isinstance(data, dict) and data.get("message"):
                detail = ": " + _redact(_one_line(str(data["message"])), _secrets({"notify_discord_webhook": self._url},
                                                                                  NotifierConfig()))[:300]
        except ValueError:
            pass
        if resp.status_code == 429:
            detail = detail or ": rate limited"
        raise ChannelError(f"discord: HTTP {resp.status_code}{detail}")
