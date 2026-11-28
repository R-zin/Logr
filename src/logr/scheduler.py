"""Periodic background jobs: sync every source, and run a notification pass.

One daemon thread runs a loop that calls :meth:`Scheduler.tick` and then waits
(on a ``threading.Event``, so :meth:`Scheduler.stop` wakes it at once) until the
next job is due. ``tick(now)`` runs whatever is due and is what tests drive
directly with a fake clock and fake job functions.

Rules:
- the next run of a job is scheduled from the *start* of its previous run
  (``start + every``), so run time doesn't make the schedule drift;
- on start the notify job is due immediately, while the first sync waits
  ``initial_sync_delay`` so a restart loop doesn't hammer the sources;
- each job's last *successful* run time is persisted in the
  ``scheduler_state(job, last_run_at)`` table, so after a restart a job that ran
  recently waits until ``last_run_at + every``;
- a job never overlaps itself (a per-job non-blocking lock); an exception in a
  run is logged and the job is rescheduled as usual, other jobs are unaffected.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from logr.models import Source, utcnow

log = logging.getLogger("logr.scheduler")

DEFAULT_SYNC_EVERY = timedelta(hours=6)
DEFAULT_NOTIFY_EVERY = timedelta(minutes=15)
DEFAULT_INITIAL_SYNC_DELAY = timedelta(minutes=1)
MAX_WAIT_SECONDS = 60.0  # the loop re-checks at least this often (clock jumps, suspend)

JobFn = Callable[[datetime], object]

STATE_SCHEMA = "CREATE TABLE IF NOT EXISTS scheduler_state (job TEXT PRIMARY KEY, last_run_at TEXT NOT NULL)"


# --- default jobs -------------------------------------------------------------


def resolve_admins(env: Mapping[str, str] | None = None) -> list[str]:
    """Users who receive health alerts: ``["local"]`` in local auth mode, else
    ``LOGR_ADMINS``. Uses ``auth.AuthConfig.from_env`` when the web extra is
    installed; falls back to reading the same variables directly."""
    env = os.environ if env is None else env
    try:
        from logr.auth import AuthConfig  # needs fastapi ([web] extra)
    except ImportError:
        mode = (env.get("LOGR_AUTH") or "local").strip().lower()
        admins = sorted({a.strip() for a in (env.get("LOGR_ADMINS") or "").split(",") if a.strip()})
        return ["local"] if mode == "local" else admins
    cfg = AuthConfig.from_env(env)
    return ["local"] if cfg.mode == "local" else sorted(cfg.admins)


def luma_places(prefs_value: str | None, env: Mapping[str, str] | None = None) -> list[str]:
    """``$LOGR_LUMA_NEAR`` > pref ``luma_near`` (``;``-separated); invalid places
    are dropped with a warning."""
    from logr.sources.luma import resolve_place

    env = os.environ if env is None else env
    raw = env.get("LOGR_LUMA_NEAR") or prefs_value or ""
    out = []
    for p in (x.strip() for x in raw.split(";")):
        if not p:
            continue
        try:
            resolve_place(p)
        except ValueError as exc:
            log.warning("ignoring Luma place: %s", exc)
            continue
        out.append(p)
    return out


def default_sync_fn(db_path: str | Path) -> JobFn:
    def run_sync(now: datetime) -> None:
        from logr.searches import Preferences
        from logr.store import Store
        from logr.sync import SyncOptions, sync

        store = Store(db_path)
        try:
            places = luma_places(Preferences(store.db).get("luma_near"))
            reports = sync(store, None, options=SyncOptions(connector_opts={Source.LUMA: {"places": places}}))
        finally:
            store.close()
        for r in reports:
            if r.error:
                log.warning("sync %s failed: %s", r.source, r.error)
            else:
                res = r.result
                log.info("sync %s: %d fetched, %d new, %d updated", r.source, r.fetched,
                         res.created if res else 0, res.updated if res else 0)
        if reports and all(r.error for r in reports):
            raise RuntimeError("every source failed")

    return run_sync


def default_notify_fn(db_path: str | Path) -> JobFn:
    def run_notify(now: datetime) -> None:
        from logr import notify
        from logr.store import Store

        config = notify.NotifierConfig.from_env()
        admins = resolve_admins()
        store = Store(db_path)
        try:
            rep = notify.run_pass(store, now, config, admins=admins)
        finally:
            store.close()
        log.info("notify: %d user(s), %d sent, %d failed", rep.users, rep.sent, len(rep.failed))
        for f in rep.failed:
            log.warning("notify delivery failed: %s", f)

    return run_notify


# --- persisted state ----------------------------------------------------------


class SchedulerState:
    """``scheduler_state(job, last_run_at)``; short-lived connections, so it is
    safe to use from the scheduler thread."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.execute(STATE_SCHEMA)
        return db

    def last_run(self, job: str) -> datetime | None:
        db = self._connect()
        try:
            row = db.execute("SELECT last_run_at FROM scheduler_state WHERE job = ?", (job,)).fetchone()
        finally:
            db.close()
        if not row:
            return None
        dt = datetime.fromisoformat(row[0])
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def set_last_run(self, job: str, at: datetime) -> None:
        db = self._connect()
        try:
            with db:
                db.execute("INSERT OR REPLACE INTO scheduler_state (job, last_run_at) VALUES (?, ?)",
                           (job, at.astimezone(timezone.utc).isoformat()))
        finally:
            db.close()


# --- scheduler ----------------------------------------------------------------


@dataclass
class Job:
    name: str
    every: timedelta
    fn: JobFn
    initial_delay: timedelta = timedelta(0)
    next_at: datetime | None = None  # None until the first tick
    last_ok: datetime | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class Scheduler:
    def __init__(
        self,
        db_path: str | Path,
        *,
        sync_every: timedelta = DEFAULT_SYNC_EVERY,
        notify_every: timedelta = DEFAULT_NOTIFY_EVERY,
        initial_sync_delay: timedelta = DEFAULT_INITIAL_SYNC_DELAY,
        clock: Callable[[], datetime] = utcnow,
        wait: Callable[[float], bool] | None = None,
        sync_fn: JobFn | None = None,
        notify_fn: JobFn | None = None,
        state: SchedulerState | None = None,
    ) -> None:
        if sync_every <= timedelta(0) or notify_every <= timedelta(0):
            raise ValueError("intervals must be positive")
        self.db_path = db_path
        self.sync_every = sync_every
        self.notify_every = notify_every
        self.clock = clock
        self._stop = threading.Event()
        self._wait = wait or self._stop.wait
        self.state = state or SchedulerState(db_path)
        # Order matters only for ties: notify first, so a due reminder isn't
        # held up behind a long sync.
        self.jobs: list[Job] = [
            Job("notify", notify_every, notify_fn or default_notify_fn(db_path)),
            Job("sync", sync_every, sync_fn or default_sync_fn(db_path), initial_delay=initial_sync_delay),
        ]
        self._thread: threading.Thread | None = None
        self._init_lock = threading.Lock()

    # -- scheduling

    def _init_schedule(self, now: datetime) -> None:
        with self._init_lock:
            for job in self.jobs:
                if job.next_at is not None:
                    continue
                first = now + job.initial_delay
                try:
                    last = self.state.last_run(job.name)
                except sqlite3.Error:
                    log.exception("could not read scheduler state for %s", job.name)
                    last = None
                job.last_ok = last
                job.next_at = max(first, last + job.every) if last else first

    def next_due(self) -> datetime | None:
        times = [j.next_at for j in self.jobs if j.next_at is not None]
        return min(times) if times else None

    def tick(self, now: datetime) -> list[str]:
        """Run every job due at ``now`` (synchronously, in this thread); return
        the names of the jobs that ran. A job already running is skipped."""
        self._init_schedule(now)
        ran: list[str] = []
        for job in self.jobs:
            if job.next_at is None or job.next_at > now:
                continue
            if not job.lock.acquire(blocking=False):
                continue  # still running from another tick: never overlap
            try:
                job.next_at = now + job.every
                ran.append(job.name)
                try:
                    job.fn(now)
                except Exception:  # noqa: BLE001 — a failed run must not kill the loop
                    log.exception("scheduled job %r failed", job.name)
                    continue
                job.last_ok = now
                try:
                    self.state.set_last_run(job.name, now)
                except sqlite3.Error:
                    log.exception("could not save scheduler state for %s", job.name)
            finally:
                job.lock.release()
        return ran

    # -- thread

    def _loop(self) -> None:
        log.info("scheduler started: sync every %s, notify every %s", self.sync_every, self.notify_every)
        while not self._stop.is_set():
            now = self.clock()
            try:
                self.tick(now)
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            if self._stop.is_set():
                break
            nxt = self.next_due()
            delay = MAX_WAIT_SECONDS if nxt is None else (nxt - self.clock()).total_seconds()
            self._wait(min(max(delay, 0.05), MAX_WAIT_SECONDS))
        log.info("scheduler stopped")

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="logr-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = 10.0) -> bool:
        """Ask the loop to stop and wait up to ``timeout`` seconds; True when the
        thread has exited (a running job is not interrupted, but the thread is
        a daemon so it won't keep the process alive)."""
        self._stop.set()
        t = self._thread
        if t is None:
            return True
        t.join(timeout)
        return not t.is_alive()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
