"""iCalendar (RFC 5545) feeds of your hackathon deadlines, for Google Calendar,
Apple Calendar or Outlook to subscribe to.

CONTRACT (Phase 5). Signatures and semantics here are fixed; ``cli.py`` and
``api/`` are written against them.

A subscription can't send cookies, so each user gets a secret feed token; the
URL ``/api/calendar.ics?token=<token>`` is the credential. Rotating the token
invalidates the old URL.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from logr.store import Store

PRODID = "-//Logr//Hackathon deadlines//EN"


@dataclass
class IcsEvent:
    uid: str  # globally unique and STABLE across regenerations, e.g. "<hackathon uid>/<label>@logr"
    summary: str  # e.g. "⏰ Inceptrix 3.0 — registration closes"
    start: datetime  # tz-aware
    end: datetime | None = None  # None → zero-length event at `start` (deadlines)
    description: str = ""
    url: str | None = None
    alarm_before: list[int] | None = None  # VALARM DISPLAY triggers, minutes before start


def build_calendar(events: list[IcsEvent], *, name: str = "Logr deadlines", now: datetime | None = None) -> str:
    """RFC 5545 text: CRLF line endings, lines folded at 75 octets (UTF-8 safe,
    never splitting a multi-byte character), TEXT values escaped (\\\\ ; , \\n),
    times as UTC "YYYYMMDDTHHMMSSZ", DTSTAMP = ``now`` (default utcnow),
    X-WR-CALNAME = name, REFRESH-INTERVAL / X-PUBLISHED-TTL of 1 hour.
    Events sorted by start then uid."""
    raise NotImplementedError


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
    raise NotImplementedError


class FeedTokens:
    """Table ``feed_tokens(user_id PRIMARY KEY, token UNIQUE, created_at)``.
    Tokens are 32+ bytes from ``secrets.token_urlsafe``."""

    def __init__(self, db: sqlite3.Connection) -> None:
        raise NotImplementedError

    def get_or_create(self, user_id: str) -> str:
        raise NotImplementedError

    def rotate(self, user_id: str) -> str:
        """New token; the old one stops working immediately."""
        raise NotImplementedError

    def user_for(self, token: str) -> str | None:
        """Constant-time comparison isn't needed for a DB lookup by a 256-bit
        random token, but reject empty/short tokens without querying."""
        raise NotImplementedError
