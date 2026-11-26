"""Personal tracking: which hackathons you're doing, where you are in each, and
what you still have to do.

CONTRACT (Phase 3). Signatures and semantics here are fixed; other modules
(cli.py) are written against them.

Storage lives in the same SQLite file as the listings (pass ``Store.db``). Every
table has a ``user_id`` column, always ``"local"`` for now, so Phase 4 can add
real accounts without a data migration.

A tracked hackathon is referenced by its listing ``uid`` (e.g. ``unstop:1794479``).
Tracking survives the listing being delisted or closing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from logr.models import Deadline, Hackathon

LOCAL_USER = "local"


class Status(StrEnum):
    """Pipeline, in order. Any status can be set from any other (people fix
    mistakes); every change is recorded in the history."""

    INTERESTED = "interested"
    REGISTERED = "registered"
    TEAM_FORMED = "team_formed"
    SUBMITTED = "submitted"
    SHORTLISTED = "shortlisted"
    WON = "won"
    LOST = "lost"
    WITHDRAWN = "withdrawn"


PIPELINE: tuple[Status, ...] = tuple(Status)
TERMINAL: frozenset[Status] = frozenset({Status.WON, Status.LOST, Status.WITHDRAWN})


@dataclass
class ChecklistItem:
    id: int  # stable per item, unique within the database
    text: str
    done: bool = False
    due_at: datetime | None = None  # tz-aware UTC


@dataclass
class StatusChange:
    old: Status | None  # None for the initial "track"
    new: Status
    changed_at: datetime  # tz-aware UTC
    note: str | None = None


@dataclass
class Tracked:
    uid: str
    status: Status
    added_at: datetime  # tz-aware UTC
    updated_at: datetime  # tz-aware UTC; bumped by every mutation below
    notes: str = ""  # free text; append_note adds timestamped lines
    repo_url: str | None = None
    team: list[str] = field(default_factory=list)  # in insertion order, no duplicates (case-insensitive)
    checklist: list[ChecklistItem] = field(default_factory=list)  # in insertion order

    @property
    def is_active(self) -> bool:
        return self.status not in TERMINAL


class Tracker:
    """All mutating methods raise ``KeyError(uid)`` when ``uid`` isn't tracked
    (except ``track``) and return the updated ``Tracked``. Writes are committed
    immediately."""

    def __init__(self, db: sqlite3.Connection) -> None:
        """Create tables if missing (CREATE TABLE IF NOT EXISTS ...)."""
        raise NotImplementedError

    def track(self, uid: str, status: Status = Status.INTERESTED) -> Tracked:
        """Start tracking. Idempotent: if already tracked, returns it unchanged
        (the ``status`` argument is ignored then). Records an initial history
        entry (old=None)."""
        raise NotImplementedError

    def untrack(self, uid: str) -> bool:
        """Delete the tracking record and everything attached. False if it wasn't tracked."""
        raise NotImplementedError

    def get(self, uid: str) -> Tracked | None:
        raise NotImplementedError

    def all(self, statuses: Iterable[Status] | None = None) -> list[Tracked]:
        """Every tracked hackathon (optionally only these statuses), oldest added first."""
        raise NotImplementedError

    def set_status(self, uid: str, status: Status, note: str | None = None) -> Tracked:
        """Change status and record it. Setting the current status again is a no-op
        (no history entry)."""
        raise NotImplementedError

    def history(self, uid: str) -> list[StatusChange]:
        """Oldest first. Empty list if not tracked."""
        raise NotImplementedError

    def append_note(self, uid: str, text: str, now: datetime | None = None) -> Tracked:
        """Append a line ``"[YYYY-MM-DD HH:MM UTC] text"`` to notes (newline-separated)."""
        raise NotImplementedError

    def set_repo(self, uid: str, url: str | None) -> Tracked:
        raise NotImplementedError

    def add_member(self, uid: str, name: str) -> Tracked:
        """Add a teammate; a case-insensitive duplicate is ignored. Name is stripped;
        empty raises ValueError."""
        raise NotImplementedError

    def remove_member(self, uid: str, name: str) -> Tracked:
        """Case-insensitive. Raises ValueError if not on the team."""
        raise NotImplementedError

    def add_todo(self, uid: str, text: str, due_at: datetime | None = None) -> ChecklistItem:
        """Empty text raises ValueError; naive due_at raises ValueError."""
        raise NotImplementedError

    def set_todo_done(self, uid: str, item_id: int, done: bool = True) -> Tracked:
        """Raises KeyError if the item doesn't belong to ``uid``."""
        raise NotImplementedError

    def remove_todo(self, uid: str, item_id: int) -> Tracked:
        """Raises KeyError if the item doesn't belong to ``uid``."""
        raise NotImplementedError


def upcoming(h: Hackathon | None, t: Tracked, now: datetime) -> list[Deadline]:
    """What *you* still have to act on, soonest first, all ``>= now``:

    - terminal status (won/lost/withdrawn): nothing, not even todos.
    - interested: all of ``h.deadlines()``.
    - registered / team_formed / submitted / shortlisted: ``h.deadlines()`` minus
      "registration closes" (you're already in).
    - plus every not-done checklist item with a due date, labelled ``"todo: <text>"``
      (approximate=False).

    ``h`` may be None (listing vanished from the store); then only todos count.
    """
    raise NotImplementedError


def next_action(h: Hackathon | None, t: Tracked, now: datetime) -> Deadline | None:
    """First item of ``upcoming`` or None."""
    raise NotImplementedError
