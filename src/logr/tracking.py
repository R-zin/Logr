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
from datetime import datetime, timezone
from enum import StrEnum

from logr.models import Deadline, Hackathon, RoundKind, utcnow

LOCAL_USER = "local"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tracked (
    user_id    TEXT NOT NULL DEFAULT 'local',
    uid        TEXT NOT NULL,
    status     TEXT NOT NULL,
    added_at   TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    notes      TEXT NOT NULL DEFAULT '',
    repo_url   TEXT,
    PRIMARY KEY (user_id, uid)
);

CREATE TABLE IF NOT EXISTS tracked_status_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL DEFAULT 'local',
    uid        TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    note       TEXT
);
CREATE INDEX IF NOT EXISTS ix_tracked_hist ON tracked_status_history(user_id, uid);

CREATE TABLE IF NOT EXISTS tracked_team (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  TEXT NOT NULL DEFAULT 'local',
    uid      TEXT NOT NULL,
    name     TEXT NOT NULL,
    name_key TEXT NOT NULL,             -- casefolded name, for case-insensitive uniqueness
    UNIQUE (user_id, uid, name_key)
);

CREATE TABLE IF NOT EXISTS tracked_checklist (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL DEFAULT 'local',
    uid     TEXT NOT NULL,
    text    TEXT NOT NULL,
    done    INTEGER NOT NULL DEFAULT 0,
    due_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_tracked_checklist ON tracked_checklist(user_id, uid);
"""


def _iso(dt: datetime) -> str:
    """UTC ISO-8601 with a 'Z' suffix (same form as ``logr.store``)."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_opt(dt: datetime | None) -> str | None:
    return _iso(dt) if dt is not None else None


def _dt(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _dt_opt(s: str | None) -> datetime | None:
    return _dt(s) if s else None


def _aware_utc(dt: datetime | None, what: str) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f"{what} must be timezone-aware")
    return dt.astimezone(timezone.utc)


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

    def __init__(self, db: sqlite3.Connection, user_id: str = LOCAL_USER) -> None:
        """Create tables if missing (CREATE TABLE IF NOT EXISTS ...)."""
        self.user_id = user_id
        self.db = db
        self.db.executescript(_SCHEMA)
        self.db.commit()

    # --- internals ---------------------------------------------------------

    def _exists(self, uid: str) -> bool:
        return self.db.execute("SELECT 1 FROM tracked WHERE user_id=? AND uid=?",
                               (self.user_id, uid)).fetchone() is not None

    def _require(self, uid: str) -> None:
        if not self._exists(uid):
            raise KeyError(uid)

    def _touch(self, uid: str) -> None:
        """Bump ``updated_at``. Call inside the mutation's transaction."""
        self.db.execute("UPDATE tracked SET updated_at=? WHERE user_id=? AND uid=?",
                        (_iso(utcnow()), self.user_id, uid))

    def _get_or_raise(self, uid: str) -> Tracked:
        t = self.get(uid)
        if t is None:  # pragma: no cover - only if deleted concurrently
            raise KeyError(uid)
        return t

    def _require_item(self, uid: str, item_id: int) -> None:
        self._require(uid)
        row = self.db.execute("SELECT 1 FROM tracked_checklist WHERE user_id=? AND uid=? AND id=?",
                              (self.user_id, uid, item_id)).fetchone()
        if row is None:
            raise KeyError(item_id)

    def track(self, uid: str, status: Status = Status.INTERESTED) -> Tracked:
        """Start tracking. Idempotent: if already tracked, returns it unchanged
        (the ``status`` argument is ignored then). Records an initial history
        entry (old=None)."""
        status = Status(status)
        if not self._exists(uid):
            now = _iso(utcnow())
            with self.db:
                self.db.execute(
                    "INSERT INTO tracked (user_id, uid, status, added_at, updated_at, notes, repo_url)"
                    " VALUES (?,?,?,?,?,'',NULL)",
                    (self.user_id, uid, status.value, now, now))
                self.db.execute(
                    "INSERT INTO tracked_status_history (user_id, uid, old_status, new_status, changed_at, note)"
                    " VALUES (?,?,NULL,?,?,NULL)",
                    (self.user_id, uid, status.value, now))
        return self._get_or_raise(uid)

    def untrack(self, uid: str) -> bool:
        """Delete the tracking record and everything attached. False if it wasn't tracked."""
        with self.db:
            cur = self.db.execute("DELETE FROM tracked WHERE user_id=? AND uid=?", (self.user_id, uid))
            for table in ("tracked_status_history", "tracked_team", "tracked_checklist"):
                self.db.execute(f"DELETE FROM {table} WHERE user_id=? AND uid=?", (self.user_id, uid))
        return cur.rowcount > 0

    def get(self, uid: str) -> Tracked | None:
        row = self.db.execute(
            "SELECT uid, status, added_at, updated_at, notes, repo_url FROM tracked WHERE user_id=? AND uid=?",
            (self.user_id, uid)).fetchone()
        return self._build(row) if row is not None else None

    def _build(self, row: sqlite3.Row | tuple) -> Tracked:
        uid, status, added_at, updated_at, notes, repo_url = tuple(row)
        team = [r[0] for r in self.db.execute(
            "SELECT name FROM tracked_team WHERE user_id=? AND uid=? ORDER BY id", (self.user_id, uid))]
        checklist = [
            ChecklistItem(id=int(i), text=text, done=bool(done), due_at=_dt_opt(due))
            for i, text, done, due in (tuple(r) for r in self.db.execute(
                "SELECT id, text, done, due_at FROM tracked_checklist WHERE user_id=? AND uid=? ORDER BY id",
                (self.user_id, uid)))
        ]
        return Tracked(uid=uid, status=Status(status), added_at=_dt(added_at), updated_at=_dt(updated_at),
                       notes=notes or "", repo_url=repo_url, team=team, checklist=checklist)

    def all(self, statuses: Iterable[Status] | None = None) -> list[Tracked]:
        """Every tracked hackathon (optionally only these statuses), oldest added first."""
        sql = "SELECT uid, status, added_at, updated_at, notes, repo_url FROM tracked WHERE user_id=?"
        args: list[str] = [self.user_id]
        if statuses is not None:
            wanted = sorted({Status(s).value for s in statuses})
            if not wanted:
                return []
            sql += f" AND status IN ({','.join('?' * len(wanted))})"
            args += wanted
        sql += " ORDER BY added_at, rowid"
        return [self._build(r) for r in self.db.execute(sql, args).fetchall()]

    def set_status(self, uid: str, status: Status, note: str | None = None) -> Tracked:
        """Change status and record it. Setting the current status again is a no-op
        (no history entry)."""
        status = Status(status)
        cur = self.get(uid)
        if cur is None:
            raise KeyError(uid)
        if cur.status is status:
            return cur
        now = _iso(utcnow())
        with self.db:
            self.db.execute("UPDATE tracked SET status=?, updated_at=? WHERE user_id=? AND uid=?",
                            (status.value, now, self.user_id, uid))
            self.db.execute(
                "INSERT INTO tracked_status_history (user_id, uid, old_status, new_status, changed_at, note)"
                " VALUES (?,?,?,?,?,?)",
                (self.user_id, uid, cur.status.value, status.value, now, note))
        return self._get_or_raise(uid)

    def history(self, uid: str) -> list[StatusChange]:
        """Oldest first. Empty list if not tracked."""
        rows = self.db.execute(
            "SELECT old_status, new_status, changed_at, note FROM tracked_status_history"
            " WHERE user_id=? AND uid=? ORDER BY id", (self.user_id, uid)).fetchall()
        return [StatusChange(old=Status(o) if o is not None else None, new=Status(n), changed_at=_dt(at), note=note)
                for o, n, at, note in (tuple(r) for r in rows)]

    def append_note(self, uid: str, text: str, now: datetime | None = None) -> Tracked:
        """Append a line ``"[YYYY-MM-DD HH:MM UTC] text"`` to notes (newline-separated)."""
        self._require(uid)
        when = _aware_utc(now, "now") if now is not None else utcnow()
        line = f"[{when:%Y-%m-%d %H:%M} UTC] {text.strip()}"
        row = self.db.execute("SELECT notes FROM tracked WHERE user_id=? AND uid=?", (self.user_id, uid)).fetchone()
        existing = row[0] or ""
        notes = f"{existing}\n{line}" if existing else line
        with self.db:
            self.db.execute("UPDATE tracked SET notes=? WHERE user_id=? AND uid=?", (notes, self.user_id, uid))
            self._touch(uid)
        return self._get_or_raise(uid)

    def set_repo(self, uid: str, url: str | None) -> Tracked:
        self._require(uid)
        url = (url or "").strip() or None
        with self.db:
            self.db.execute("UPDATE tracked SET repo_url=? WHERE user_id=? AND uid=?", (url, self.user_id, uid))
            self._touch(uid)
        return self._get_or_raise(uid)

    def add_member(self, uid: str, name: str) -> Tracked:
        """Add a teammate; a case-insensitive duplicate is ignored. Name is stripped;
        empty raises ValueError."""
        name = (name or "").strip()
        if not name:
            raise ValueError("team member name is empty")
        self._require(uid)
        key = name.casefold()
        dup = self.db.execute("SELECT 1 FROM tracked_team WHERE user_id=? AND uid=? AND name_key=?",
                              (self.user_id, uid, key)).fetchone()
        if dup is None:
            with self.db:
                self.db.execute("INSERT INTO tracked_team (user_id, uid, name, name_key) VALUES (?,?,?,?)",
                                (self.user_id, uid, name, key))
                self._touch(uid)
        return self._get_or_raise(uid)

    def remove_member(self, uid: str, name: str) -> Tracked:
        """Case-insensitive. Raises ValueError if not on the team."""
        self._require(uid)
        key = (name or "").strip().casefold()
        with self.db:
            cur = self.db.execute("DELETE FROM tracked_team WHERE user_id=? AND uid=? AND name_key=?",
                                  (self.user_id, uid, key))
            if cur.rowcount == 0:
                raise ValueError(f"{name!r} is not on the team")
            self._touch(uid)
        return self._get_or_raise(uid)

    def add_todo(self, uid: str, text: str, due_at: datetime | None = None) -> ChecklistItem:
        """Empty text raises ValueError; naive due_at raises ValueError."""
        text = (text or "").strip()
        if not text:
            raise ValueError("todo text is empty")
        due = _aware_utc(due_at, "due_at")
        self._require(uid)
        with self.db:
            cur = self.db.execute(
                "INSERT INTO tracked_checklist (user_id, uid, text, done, due_at) VALUES (?,?,?,0,?)",
                (self.user_id, uid, text, _iso_opt(due)))
            self._touch(uid)
        return ChecklistItem(id=int(cur.lastrowid), text=text, done=False, due_at=due)

    def set_todo_done(self, uid: str, item_id: int, done: bool = True) -> Tracked:
        """Raises KeyError if the item doesn't belong to ``uid``."""
        self._require_item(uid, item_id)
        with self.db:
            self.db.execute("UPDATE tracked_checklist SET done=? WHERE user_id=? AND uid=? AND id=?",
                            (int(bool(done)), self.user_id, uid, item_id))
            self._touch(uid)
        return self._get_or_raise(uid)

    def remove_todo(self, uid: str, item_id: int) -> Tracked:
        """Raises KeyError if the item doesn't belong to ``uid``."""
        self._require_item(uid, item_id)
        with self.db:
            self.db.execute("DELETE FROM tracked_checklist WHERE user_id=? AND uid=? AND id=?",
                            (self.user_id, uid, item_id))
            self._touch(uid)
        return self._get_or_raise(uid)


def upcoming(h: Hackathon | None, t: Tracked, now: datetime) -> list[Deadline]:
    """What *you* still have to act on, soonest first, all ``>= now``:

    - terminal status (won/lost/withdrawn): nothing, not even todos.
    - interested: all of ``h.deadlines()``.
    - registered / team_formed: ``h.deadlines()`` minus "registration closes"
      (you're already in).
    - submitted / shortlisted: only submission rounds that haven't *started* yet
      (later stages, e.g. a prototype round after the idea round). The deadline
      you just submitted for, or the generic "submission due", no longer applies.
    - plus every not-done checklist item with a due date, labelled ``"todo: <text>"``
      (approximate=False).

    ``h`` may be None (listing vanished from the store); then only todos count.
    """
    if t.status in TERMINAL:
        return []
    out: list[Deadline] = []
    if h is not None:
        if t.status in (Status.SUBMITTED, Status.SHORTLISTED):
            ds = [Deadline(f"{r.name} ends", r.ends_at, False) for r in h.rounds
                  if r.kind is RoundKind.SUBMISSION and r.ends_at and r.starts_at and r.starts_at > now]
        else:
            ds = h.deadlines()
            if t.status is not Status.INTERESTED:
                ds = [d for d in ds if d.label != "registration closes"]
        out.extend(ds)
    out.extend(Deadline(f"todo: {item.text}", item.due_at, False)
               for item in t.checklist if not item.done and item.due_at is not None)
    out = [d for d in out if d.at >= now]
    out.sort(key=lambda d: d.at)  # stable: listing deadlines before todos on a tie
    return out


def next_action(h: Hackathon | None, t: Tracked, now: datetime) -> Deadline | None:
    """First item of ``upcoming`` or None."""
    items = upcoming(h, t, now)
    return items[0] if items else None
