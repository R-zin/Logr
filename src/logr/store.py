"""SQLite persistence: listings, enrichment cache, date-change history, sync log."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from logr.models import DATE_FIELDS, Hackathon, Source, utcnow

TRACKED_DATES = DATE_FIELDS
DELIST_AFTER = 3  # consecutive successful syncs a listing must be missing from

SCHEMA = """
CREATE TABLE IF NOT EXISTS hackathons (
    uid                    TEXT PRIMARY KEY,
    source                 TEXT NOT NULL,
    source_id              TEXT NOT NULL,
    title                  TEXT NOT NULL,
    url                    TEXT NOT NULL,
    registration_closes_at TEXT,
    submission_deadline    TEXT,
    starts_at              TEXT,
    data                   TEXT NOT NULL,
    first_seen_at          TEXT NOT NULL,
    last_seen_at           TEXT NOT NULL,
    missing_syncs          INTEGER NOT NULL DEFAULT 0,
    delisted_at            TEXT
);
CREATE INDEX IF NOT EXISTS ix_h_reg ON hackathons(registration_closes_at);
CREATE INDEX IF NOT EXISTS ix_h_sub ON hackathons(submission_deadline);
CREATE INDEX IF NOT EXISTS ix_h_source ON hackathons(source);

CREATE TABLE IF NOT EXISTS date_changes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    uid        TEXT NOT NULL REFERENCES hackathons(uid),
    field      TEXT NOT NULL,
    old_value  TEXT,
    new_value  TEXT,
    changed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS enrichments (
    uid        TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    patch      TEXT,            -- JSON patch overlaid on the listing; NULL = nothing extra found
    error      TEXT
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    fetched     INTEGER DEFAULT 0,
    created     INTEGER DEFAULT 0,
    updated     INTEGER DEFAULT 0,
    error       TEXT,
    enriched    INTEGER DEFAULT 0,
    enrich_errors INTEGER DEFAULT 0,
    delisted    INTEGER DEFAULT 0
);
"""

# Columns added after Phase 1: (table, column, DDL)
MIGRATIONS = [
    ("hackathons", "missing_syncs", "INTEGER NOT NULL DEFAULT 0"),
    ("hackathons", "delisted_at", "TEXT"),
    ("sync_runs", "enriched", "INTEGER DEFAULT 0"),
    ("sync_runs", "enrich_errors", "INTEGER DEFAULT 0"),
    ("sync_runs", "delisted", "INTEGER DEFAULT 0"),
]


def _iso(dt: datetime | None) -> str | None:
    """UTC ISO-8601 with a 'Z' suffix — same form pydantic writes into ``data``."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def _round_map(data: dict[str, Any]) -> dict[str, str]:
    return {r["name"]: f"{r.get('starts_at') or '—'} → {r.get('ends_at') or '—'}" for r in data.get("rounds") or []}


@dataclass
class UpsertResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    date_changes: list[tuple[str, str, str | None, str | None]] = field(default_factory=list)


@dataclass
class Enrichment:
    fetched_at: datetime
    patch: dict[str, Any] | None
    error: str | None


@dataclass
class Meta:
    first_seen_at: str
    last_seen_at: str
    delisted_at: str | None


class Store:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the web server may open a Store in one worker
        # thread and use it in another; each request gets its own Store.
        self.db = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        if str(path) != ":memory:":
            # WAL lets the web server read while a sync writes.
            self.db.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        # Old databases: CREATE TABLE IF NOT EXISTS leaves existing tables alone,
        # so add any missing columns before creating indexes that might use them.
        for table, col, ddl in MIGRATIONS:
            cols = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
            if cols and col not in cols:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # --- listings ---------------------------------------------------------

    def upsert_many(self, items: Iterable[Hackathon]) -> UpsertResult:
        res = UpsertResult()
        now = _iso(utcnow())
        with self.db:
            for h in items:
                row = self.db.execute("SELECT data FROM hackathons WHERE uid = ?", (h.uid,)).fetchone()
                payload = h.model_dump(mode="json", exclude={"fetched_at"})
                blob = json.dumps(payload, sort_keys=True)
                cols = (h.title, h.url, _iso(h.registration_closes_at), _iso(h.submission_deadline), _iso(h.starts_at))
                if row is None:
                    self.db.execute(
                        "INSERT INTO hackathons (uid, source, source_id, title, url, registration_closes_at,"
                        " submission_deadline, starts_at, data, first_seen_at, last_seen_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (h.uid, h.source.value, h.source_id, *cols, blob, now, now),
                    )
                    res.created += 1
                    continue
                old_raw = json.loads(row["data"])
                if json.dumps(old_raw, sort_keys=True) == blob:
                    self.db.execute("UPDATE hackathons SET last_seen_at = ? WHERE uid = ?", (now, h.uid))
                    res.unchanged += 1
                    continue
                for f, old_v, new_v in self._date_diff(old_raw, payload):
                    self.db.execute(
                        "INSERT INTO date_changes (uid, field, old_value, new_value, changed_at) VALUES (?,?,?,?,?)",
                        (h.uid, f, old_v, new_v, now),
                    )
                    res.date_changes.append((h.uid, f, old_v, new_v))
                self.db.execute(
                    "UPDATE hackathons SET title=?, url=?, registration_closes_at=?, submission_deadline=?,"
                    " starts_at=?, data=?, last_seen_at=? WHERE uid=?",
                    (*cols, blob, now, h.uid),
                )
                res.updated += 1
        return res

    @staticmethod
    def _date_diff(old_raw: dict[str, Any], new: dict[str, Any]) -> list[tuple[str, str | None, str | None]]:
        """Real date moves only. An approximate date being replaced by the exact
        one (or a round timeline appearing for the first time) is refinement,
        not the organizer changing anything, so it isn't logged."""
        old = Hackathon.model_validate(old_raw).model_dump(mode="json")
        changes = []
        for f in TRACKED_DATES:
            if old.get(f) != new.get(f) and f not in old.get("approx_fields", []):
                if old.get(f) is None:
                    continue  # newly published, not moved
                changes.append((f, old.get(f), new.get(f)))
        ro, rn = _round_map(old), _round_map(new)
        if ro:
            for name in ro.keys() | rn.keys():
                if ro.get(name) != rn.get(name):
                    changes.append((f"round: {name}", ro.get(name), rn.get(name)))
        return sorted(changes)

    def mark_seen(self, source: Source, seen_uids: set[str], *, delist_after: int = DELIST_AFTER) -> int:
        """After a *successful* sync: reset seen listings, count misses for the
        rest, and delist those missing ``delist_after`` times in a row.
        Returns how many were newly delisted."""
        now = _iso(utcnow())
        with self.db:
            known = {r["uid"] for r in self.db.execute("SELECT uid FROM hackathons WHERE source=?", (source.value,))}
            seen = known & seen_uids
            missing = known - seen_uids
            self.db.executemany("UPDATE hackathons SET missing_syncs=0, delisted_at=NULL WHERE uid=?",
                                [(u,) for u in seen])
            self.db.executemany("UPDATE hackathons SET missing_syncs=missing_syncs+1 WHERE uid=?",
                                [(u,) for u in missing])
            cur = self.db.execute(
                "UPDATE hackathons SET delisted_at=? WHERE source=? AND delisted_at IS NULL AND missing_syncs>=?",
                (now, source.value, delist_after),
            )
        return cur.rowcount

    # --- enrichment cache -------------------------------------------------

    def get_enrichment(self, uid: str) -> Enrichment | None:
        r = self.db.execute("SELECT * FROM enrichments WHERE uid=?", (uid,)).fetchone()
        if not r:
            return None
        return Enrichment(datetime.fromisoformat(r["fetched_at"].replace("Z", "+00:00")),
                          json.loads(r["patch"]) if r["patch"] else None, r["error"])

    def save_enrichment(self, uid: str, patch: dict[str, Any] | None, error: str | None = None) -> None:
        """On error the previous patch is kept, so a flaky detail page doesn't
        throw away good dates."""
        now = _iso(utcnow())
        with self.db:
            if error:
                self.db.execute(
                    "INSERT INTO enrichments (uid, fetched_at, patch, error) VALUES (?,?,NULL,?)"
                    " ON CONFLICT(uid) DO UPDATE SET fetched_at=excluded.fetched_at, error=excluded.error",
                    (uid, now, error),
                )
            else:
                self.db.execute(
                    "INSERT INTO enrichments (uid, fetched_at, patch, error) VALUES (?,?,?,NULL)"
                    " ON CONFLICT(uid) DO UPDATE SET fetched_at=excluded.fetched_at, patch=excluded.patch, error=NULL",
                    (uid, now, json.dumps(patch) if patch else None),
                )

    def enrichment_is_fresh(self, e: Enrichment | None, ttl: timedelta, now: datetime | None = None) -> bool:
        if e is None:
            return False
        age = (now or utcnow()) - e.fetched_at
        # failed lookups are retried sooner, but not on every sync
        return age < (min(ttl, timedelta(hours=1)) if e.error else ttl)

    # --- sync log ---------------------------------------------------------

    def start_run(self, source: Source) -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO sync_runs (source, started_at) VALUES (?, ?)", (source.value, _iso(utcnow()))
            )
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, *, fetched: int, result: UpsertResult | None, error: str | None,
                   enriched: int = 0, enrich_errors: int = 0, delisted: int = 0) -> None:
        with self.db:
            self.db.execute(
                "UPDATE sync_runs SET finished_at=?, fetched=?, created=?, updated=?, error=?,"
                " enriched=?, enrich_errors=?, delisted=? WHERE id=?",
                (_iso(utcnow()), fetched, result.created if result else 0, result.updated if result else 0,
                 error, enriched, enrich_errors, delisted, run_id),
            )

    # --- reads ------------------------------------------------------------

    def all(self, sources: Iterable[Source] | None = None, *, include_delisted: bool = False) -> list[Hackathon]:
        clauses, args = [], []
        if sources:
            srcs = [s.value for s in sources]
            clauses.append(f"source IN ({','.join('?' * len(srcs))})")
            args += srcs
        if not include_delisted:
            clauses.append("delisted_at IS NULL")
        sql = "SELECT data FROM hackathons" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        return [Hackathon.model_validate_json(r["data"]) for r in self.db.execute(sql, args)]

    def get(self, uid: str) -> Hackathon | None:
        row = self.db.execute("SELECT data FROM hackathons WHERE uid = ?", (uid,)).fetchone()
        return Hackathon.model_validate_json(row["data"]) if row else None

    def meta(self, uid: str) -> Meta | None:
        r = self.db.execute("SELECT first_seen_at, last_seen_at, delisted_at FROM hackathons WHERE uid=?",
                            (uid,)).fetchone()
        return Meta(r["first_seen_at"], r["last_seen_at"], r["delisted_at"]) if r else None

    def history(self, uid: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT field, old_value, new_value, changed_at FROM date_changes WHERE uid=? ORDER BY id", (uid,)
        ).fetchall()

    def last_runs(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT r.* FROM sync_runs r JOIN (SELECT source, MAX(id) id FROM sync_runs GROUP BY source) m"
            " ON r.id = m.id ORDER BY r.source"
        ).fetchall()
