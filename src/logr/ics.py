"""iCalendar (RFC 5545) feeds of your hackathon deadlines, for Google Calendar,
Apple Calendar or Outlook to subscribe to.

CONTRACT (Phase 5). Signatures and semantics here are fixed; ``cli.py`` and
``api/`` are written against them.

A subscription can't send cookies, so each user gets a secret feed token; the
URL ``/api/calendar.ics?token=<token>`` is the credential. Rotating the token
invalidates the old URL.
"""

from __future__ import annotations

import dataclasses
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from logr.dedupe import best_records
from logr.models import Deadline, Hackathon
from logr.store import Store
from logr.tracking import TERMINAL, Tracked, Tracker, upcoming

PRODID = "-//Logr//Hackathon deadlines//EN"

LOOKBACK = timedelta(days=30)
DEADLINE_ALARMS = [24 * 60, 3 * 60]  # 1 day and 3 hours before
MIN_TOKEN_LEN = 32
_TOKEN_BYTES = 32  # token_urlsafe(32) → 43 characters, 256 bits


@dataclass
class IcsEvent:
    uid: str  # globally unique and STABLE across regenerations, e.g. "<hackathon uid>/<label>@logr"
    summary: str  # e.g. "⏰ Inceptrix 3.0 — registration closes"
    start: datetime  # tz-aware
    end: datetime | None = None  # None → zero-length event at `start` (deadlines)
    description: str = ""
    url: str | None = None
    alarm_before: list[int] | None = None  # VALARM DISPLAY triggers, minutes before start


# ------------------------------------------------------------------ formatting


def _escape(text: str) -> str:
    """RFC 5545 §3.3.11 TEXT escaping."""
    text = text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("\n", "\\n")


def _no_breaks(text: str) -> str:
    """For non-TEXT values (URI): no control characters may survive."""
    return re.sub(r"[\x00-\x1f\x7f]", "", text)


def _utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _fold(line: str) -> str:
    """Fold to physical lines of at most 75 octets (CRLF + one space), never
    splitting a UTF-8 multi-byte sequence."""
    if len(line.encode("utf-8")) <= 75:
        return line
    parts: list[str] = []
    cur: list[str] = []
    size, limit = 0, 75  # the first line has 75 octets; continuations 74 + the leading space
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > limit:
            parts.append("".join(cur))
            cur, size, limit = [], 0, 74
        cur.append(ch)
        size += n
    parts.append("".join(cur))
    return "\r\n ".join(parts)


def _event_lines(e: IcsEvent, stamp: str) -> list[str]:
    end = e.end if e.end is not None and e.end >= e.start else e.start
    out = [
        "BEGIN:VEVENT",
        f"UID:{_escape(e.uid)}",
        f"DTSTAMP:{stamp}",
        f"DTSTART:{_utc(e.start)}",
    ]
    if end > e.start:
        out.append(f"DTEND:{_utc(end)}")
    # else: no DTEND. RFC 5545 §3.6.1: a DATE-TIME DTSTART without DTEND is an
    # instant (DTEND equal to DTSTART would violate "DTEND MUST be later").
    out.append(f"SUMMARY:{_escape(e.summary)}")
    if e.description:
        out.append(f"DESCRIPTION:{_escape(e.description)}")
    if e.url:
        out.append(f"URL:{_no_breaks(e.url)}")
    if end == e.start:
        out.append("TRANSP:TRANSPARENT")  # a deadline doesn't make you busy
    for minutes in sorted(set(e.alarm_before or []), reverse=True):
        out += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_escape(e.summary)}",
            f"TRIGGER:-PT{int(minutes)}M",
            "END:VALARM",
        ]
    out.append("END:VEVENT")
    return out


def build_calendar(events: list[IcsEvent], *, name: str = "Logr deadlines", now: datetime | None = None) -> str:
    """RFC 5545 text: CRLF line endings, lines folded at 75 octets (UTF-8 safe,
    never splitting a multi-byte character), TEXT values escaped (\\\\ ; , \\n),
    times as UTC "YYYYMMDDTHHMMSSZ", DTSTAMP = ``now`` (default utcnow),
    X-WR-CALNAME = name, REFRESH-INTERVAL / X-PUBLISHED-TTL of 1 hour.
    Events sorted by start then uid."""
    stamp = _utc(now or datetime.now(UTC))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(name)}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    for e in sorted(events, key=lambda e: (e.start.astimezone(UTC), e.uid)):
        lines += _event_lines(e, stamp)
    lines.append("END:VCALENDAR")
    return "".join(_fold(line) + "\r\n" for line in lines)


# ------------------------------------------------------------------ events


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def _uid_part(uid: str) -> str:
    return re.sub(r"[^A-Za-z0-9:._-]", "_", uid)


def _unique(uid: str, used: set[str]) -> str:
    out, n = uid, 2
    while out in used:
        out = uid.replace("@logr", f"-{n}@logr")
        n += 1
    used.add(out)
    return out


def _deadlines(h: Hackathon | None, t: Tracked, now: datetime, since: datetime) -> list[Deadline]:
    """``upcoming`` over [since, ∞): what's ahead is decided at ``now`` (so a
    submitted user doesn't get back a round that started after ``since``); the
    last month comes from ``upcoming(h, t, since)``, keeping only what's past."""
    ahead = upcoming(h, t, now)
    past = [d for d in upcoming(h, t, since) if d.at < now]
    return past + ahead


def tracked_events(store: Store, user_id: str, now: datetime) -> list[IcsEvent]:
    """Events for one user's tracked hackathons:
    - every deadline from ``tracking.upcoming(best, t, now - 30 days)`` (so the
      last month stays visible), using the merged group's best record
      (``dedupe.best_records``); approximate ones get "(approx.)" in the summary;
    - todos with due dates (summary "✅ <hackathon>: <todo>"), done ones excluded;
    - for each tracked hackathon with ``starts_at``, one event spanning
      starts_at → ends_at (or a 1-hour event) "🏁 <title>".
    Deadlines get alarms 1 day and 3 hours before. Descriptions include status
    and source URL. Terminal statuses contribute nothing."""
    since = now - LOOKBACK
    tracked = [t for t in Tracker(store.db, user_id).all() if t.status not in TERMINAL]
    if not tracked:
        return []
    best = best_records(store.all(include_delisted=True))
    events: list[IcsEvent] = []
    used: set[str] = set()
    for t in tracked:
        h = best.get(t.uid) or store.get(t.uid)
        title = h.title if h else t.uid
        url = h.url if h else None
        base = _uid_part(t.uid)
        status = t.status.value.replace("_", " ")

        def describe(*extra: str) -> str:
            return "\n".join([*extra, f"Status: {status}", *([url] if url else [])])

        # Listing deadlines (todos are handled below, with their own stable ids).
        for d in _deadlines(h, dataclasses.replace(t, checklist=[]), now, since):
            approx = " (approx.)" if d.approximate else ""
            events.append(IcsEvent(
                uid=_unique(f"{base}/{_slug(d.label)}@logr", used),
                summary=f"⏰ {title} — {d.label}{approx}",
                start=d.at,
                description=describe(*(["The exact time isn't published; this date is approximate."]
                                       if d.approximate else [])),
                url=url,
                alarm_before=list(DEADLINE_ALARMS),
            ))
        for item in t.checklist:
            if item.done or item.due_at is None or item.due_at < since:
                continue
            events.append(IcsEvent(
                uid=_unique(f"{base}/todo-{item.id}@logr", used),
                summary=f"✅ {title}: {item.text}",
                start=item.due_at,
                description=describe(f"Todo for {title}"),
                url=url,
                alarm_before=list(DEADLINE_ALARMS),
            ))
        if h is not None and h.starts_at is not None:
            end = h.ends_at if h.ends_at and h.ends_at > h.starts_at else h.starts_at + timedelta(hours=1)
            if end >= since:
                events.append(IcsEvent(
                    uid=_unique(f"{base}/event@logr", used),
                    summary=f"🏁 {title}",
                    start=h.starts_at,
                    end=end,
                    description=describe(*([h.location] if h.location else [])),
                    url=url,
                ))
    events.sort(key=lambda e: (e.start, e.uid))
    return events


# ------------------------------------------------------------------ tokens


class FeedTokens:
    """Table ``feed_tokens(user_id PRIMARY KEY, token UNIQUE, created_at)``.
    Tokens are 32+ bytes from ``secrets.token_urlsafe``."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        with self.db:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS feed_tokens ("
                " user_id    TEXT PRIMARY KEY,"
                " token      TEXT NOT NULL UNIQUE,"
                " created_at TEXT NOT NULL)"
            )

    def _get(self, user_id: str) -> str | None:
        row = self.db.execute("SELECT token FROM feed_tokens WHERE user_id = ?", (user_id,)).fetchone()
        return row[0] if row else None

    def _set(self, user_id: str, *, replace: bool) -> str:
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
        with self.db:
            self.db.execute(f"{verb} INTO feed_tokens (user_id, token, created_at) VALUES (?,?,?)",
                            (user_id, token, now))
        return self._get(user_id) or token

    def get_or_create(self, user_id: str) -> str:
        return self._get(user_id) or self._set(user_id, replace=False)

    def rotate(self, user_id: str) -> str:
        """New token; the old one stops working immediately."""
        return self._set(user_id, replace=True)

    def user_for(self, token: str) -> str | None:
        """Constant-time comparison isn't needed for a DB lookup by a 256-bit
        random token, but reject empty/short tokens without querying."""
        if not isinstance(token, str) or len(token) < MIN_TOKEN_LEN:
            return None
        row = self.db.execute("SELECT user_id FROM feed_tokens WHERE token = ?", (token,)).fetchone()
        return row[0] if row else None
