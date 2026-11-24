"""SQLite persistence: upsert listings, keep a history of deadline changes, log sync runs."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from logr.models import Hackathon, Source, utcnow

TRACKED_DATES = (
    "registration_opens_at",
    "registration_closes_at",
    "submission_deadline",
    "starts_at",
    "ends_at",
)

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
    last_seen_at           TEXT NOT NULL
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

CREATE TABLE IF NOT EXISTS sync_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    fetched     INTEGER DEFAULT 0,
    created     INTEGER DEFAULT 0,
    updated     INTEGER DEFAULT 0,
    error       TEXT
);
"""


def _iso(dt: datetime | None) -> str | None:
    """UTC ISO-8601 with a 'Z' suffix — same form pydantic writes into ``data``."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


@dataclass
class UpsertResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    date_changes: list[tuple[str, str, str | None, str | None]] = field(default_factory=list)


class Store:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # --- writes -----------------------------------------------------------

    def upsert_many(self, items: Iterable[Hackathon]) -> UpsertResult:
        res = UpsertResult()
        now = _iso(utcnow())
        with self.db:
            for h in items:
                row = self.db.execute("SELECT data FROM hackathons WHERE uid = ?", (h.uid,)).fetchone()
                payload = h.model_dump(mode="json", exclude={"fetched_at"})
                blob = json.dumps(payload, sort_keys=True)
                if row is None:
                    self.db.execute(
                        "INSERT INTO hackathons (uid, source, source_id, title, url, registration_closes_at,"
                        " submission_deadline, starts_at, data, first_seen_at, last_seen_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (h.uid, h.source.value, h.source_id, h.title, h.url,
                         _iso(h.registration_closes_at), _iso(h.submission_deadline),
                         _iso(h.starts_at), blob, now, now),
                    )
                    res.created += 1
                    continue
                old = json.loads(row["data"])
                if json.dumps(old, sort_keys=True) == blob:
                    self.db.execute("UPDATE hackathons SET last_seen_at = ? WHERE uid = ?", (now, h.uid))
                    res.unchanged += 1
                    continue
                for f in TRACKED_DATES:
                    if old.get(f) != payload.get(f):
                        self.db.execute(
                            "INSERT INTO date_changes (uid, field, old_value, new_value, changed_at)"
                            " VALUES (?,?,?,?,?)",
                            (h.uid, f, old.get(f), payload.get(f), now),
                        )
                        res.date_changes.append((h.uid, f, old.get(f), payload.get(f)))
                self.db.execute(
                    "UPDATE hackathons SET title=?, url=?, registration_closes_at=?, submission_deadline=?,"
                    " starts_at=?, data=?, last_seen_at=? WHERE uid=?",
                    (h.title, h.url, _iso(h.registration_closes_at), _iso(h.submission_deadline),
                     _iso(h.starts_at), blob, now, h.uid),
                )
                res.updated += 1
        return res

    def start_run(self, source: Source) -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO sync_runs (source, started_at) VALUES (?, ?)", (source.value, _iso(utcnow()))
            )
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, *, fetched: int, result: UpsertResult | None, error: str | None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE sync_runs SET finished_at=?, fetched=?, created=?, updated=?, error=? WHERE id=?",
                (_iso(utcnow()), fetched, result.created if result else 0,
                 result.updated if result else 0, error, run_id),
            )

    # --- reads ------------------------------------------------------------

    def all(self, sources: Iterable[Source] | None = None) -> list[Hackathon]:
        sql, args = "SELECT data FROM hackathons", []
        if sources:
            srcs = [s.value for s in sources]
            sql += f" WHERE source IN ({','.join('?' * len(srcs))})"
            args = srcs
        return [Hackathon.model_validate_json(r["data"]) for r in self.db.execute(sql, args)]

    def get(self, uid: str) -> Hackathon | None:
        row = self.db.execute("SELECT data FROM hackathons WHERE uid = ?", (uid,)).fetchone()
        return Hackathon.model_validate_json(row["data"]) if row else None

    def history(self, uid: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT field, old_value, new_value, changed_at FROM date_changes WHERE uid=? ORDER BY id", (uid,)
        ).fetchall()

    def last_runs(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT r.* FROM sync_runs r JOIN (SELECT source, MAX(id) id FROM sync_runs GROUP BY source) m"
            " ON r.id = m.id ORDER BY r.source"
        ).fetchall()
