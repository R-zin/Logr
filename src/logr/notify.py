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

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Protocol

from logr.store import Store

DEFAULT_OFFSETS: tuple[timedelta, ...] = (timedelta(days=7), timedelta(days=1), timedelta(hours=3))

MessageKind = Literal["reminder", "moved", "health", "test"]


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
        raise NotImplementedError

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and (self.smtp_from or self.smtp_user))

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token)


def parse_offsets(text: str | None) -> tuple[timedelta, ...]:
    """"7d,1d,3h" → (7d, 1d, 3h), sorted largest first, duplicates removed.
    Units: m, h, d, w. None/empty → DEFAULT_OFFSETS. Anything else (zero,
    negative, unknown unit, > 60d, more than 6 offsets) → ValueError."""
    raise NotImplementedError


def validate_pref(key: str, value: str) -> str:
    """Validate and normalize a Phase 5 pref; returns the value to store.
    notify_email: a plausible single address (lower-cased domain).
    notify_telegram_chat: an integer chat id (may be negative) or @channelname.
    notify_discord_webhook: https://discord.com/api/webhooks/<id>/<token> (also
    discordapp.com; canary./ptb. subdomains allowed).
    remind_offsets: anything ``parse_offsets`` accepts; normalized to e.g. "7d,1d,3h".
    Raises ValueError with a user-facing message; KeyError for other keys."""
    raise NotImplementedError


def mask_secret(value: str) -> str:
    """For display of secret prefs: keep the scheme+host and the last 4 chars,
    e.g. "https://discord.com/…/•••••abcd"."""
    raise NotImplementedError


def channels_for(prefs: Mapping[str, str], config: NotifierConfig) -> list[Channel]:
    """Channels for one user from their prefs. Email needs ``config.email_enabled``,
    Telegram needs ``config.telegram_enabled``; a configured destination whose
    server side isn't configured is skipped (``run_pass`` reports it)."""
    raise NotImplementedError


class Outbox:
    """Remembers what was sent so passes are idempotent. Tables (user_id-scoped):
    ``notify_sent(user_id, key, channel, sent_at)`` with a unique
    (user_id, key, channel), and ``notify_state(user_id, last_pass_at)``."""

    def __init__(self, db: sqlite3.Connection) -> None:
        raise NotImplementedError

    def was_sent(self, user_id: str, key: str, channel: str) -> bool:
        raise NotImplementedError

    def mark_sent(self, user_id: str, key: str, channel: str, at: datetime) -> None:
        """Idempotent."""
        raise NotImplementedError

    def last_pass(self, user_id: str) -> datetime | None:
        raise NotImplementedError

    def set_last_pass(self, user_id: str, at: datetime) -> None:
        raise NotImplementedError


def collect(store: Store, user_id: str, now: datetime, *, since: datetime | None) -> list[Message]:
    """Reminders + moved alerts for one user (rules in the module docstring),
    soonest deadline first. ``since`` is the previous pass time (None = first
    pass). Pure apart from reading the store; dedupe against the Outbox happens
    in ``run_pass`` (but larger-offset suppression is reflected in the result via
    ``suppressed_keys``)."""
    raise NotImplementedError


def suppressed_keys(store: Store, user_id: str, now: datetime) -> list[str]:
    """Reminder keys for larger due offsets that ``collect`` skipped (see the
    "smallest due offset" rule); ``run_pass`` marks them sent on every channel."""
    raise NotImplementedError


def health_messages(store: Store, now: datetime) -> list[Message]:
    raise NotImplementedError


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
    raise NotImplementedError


def send_test(prefs: Mapping[str, str], config: NotifierConfig, *, channel_factory=channels_for) -> dict[str, str]:
    """Send a "Logr test notification" through each of the user's channels.
    Returns {channel: "ok" | error message}. Includes configured-but-unavailable
    channels as e.g. {"telegram": "server has no LOGR_TELEGRAM_BOT_TOKEN"}."""
    raise NotImplementedError
