"""Scheduler: driven through ``tick(now)`` with a fake clock and fake jobs."""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from logr.scheduler import Scheduler, SchedulerState, resolve_admins

T0 = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
M = timedelta(minutes=1)


class Recorder:
    def __init__(self, fail_on=()):
        self.calls: list[datetime] = []
        self.fail_on = set(fail_on)

    def __call__(self, now):
        self.calls.append(now)
        if len(self.calls) in self.fail_on:
            raise RuntimeError(f"boom #{len(self.calls)}")


def make(tmp_path, **kw):
    sync, notif = kw.pop("sync_fn", Recorder()), kw.pop("notify_fn", Recorder())
    s = Scheduler(tmp_path / "s.db", sync_fn=sync, notify_fn=notif, clock=lambda: T0, **kw)
    return s, sync, notif


def test_first_tick_runs_notify_and_delays_sync(tmp_path):
    s, sync, notif = make(tmp_path)
    assert s.tick(T0) == ["notify"]
    assert notif.calls == [T0] and sync.calls == []
    assert s.tick(T0 + 30 * timedelta(seconds=1)) == []
    assert s.tick(T0 + M) == ["sync"]  # default initial delay: 1 minute
    assert sync.calls == [T0 + M]


def test_custom_initial_delay_and_intervals(tmp_path):
    s, sync, notif = make(tmp_path, sync_every=timedelta(hours=6), notify_every=15 * M,
                          initial_sync_delay=5 * M)
    s.tick(T0)
    assert s.tick(T0 + 4 * M) == []
    assert s.tick(T0 + 5 * M) == ["sync"]
    assert s.tick(T0 + 14 * M) == []
    assert s.tick(T0 + 15 * M) == ["notify"]
    assert s.tick(T0 + 5 * M + timedelta(hours=6) - M) == ["notify"]  # 6h04: notify due, sync not yet
    assert s.tick(T0 + 5 * M + timedelta(hours=6)) == ["sync"]


def test_next_run_scheduled_from_start_time(tmp_path):
    s, _, notif = make(tmp_path, notify_every=15 * M, initial_sync_delay=timedelta(hours=2))
    s.tick(T0)
    # A late tick (loop was busy) runs the job; the next one is 15 min after
    # *that* run started, not after the original slot and not cumulatively late.
    assert s.tick(T0 + 20 * M) == ["notify"]
    assert s.jobs[0].next_at == T0 + 35 * M
    assert s.tick(T0 + 34 * M) == []
    assert s.tick(T0 + 35 * M) == ["notify"]
    assert notif.calls == [T0, T0 + 20 * M, T0 + 35 * M]
    assert s.next_due() == T0 + 50 * M  # next notify; sync's first slot is at +2h


def test_persisted_last_run_prevents_immediate_resync(tmp_path):
    s, sync, notif = make(tmp_path)
    s.tick(T0)
    s.tick(T0 + M)
    assert len(sync.calls) == 1
    assert SchedulerState(tmp_path / "s.db").last_run("sync") == T0 + M

    # "Restart" 10 minutes later: sync ran 9 min ago, so it waits until +6h.
    s2, sync2, notif2 = make(tmp_path)
    later = T0 + 11 * M
    assert s2.tick(later) == []  # notify ran 11 min ago (< 15) too
    assert s2.tick(T0 + 15 * M) == ["notify"]
    assert s2.tick(T0 + M + timedelta(hours=6) - timedelta(seconds=1)) == ["notify"]
    assert s2.tick(T0 + M + timedelta(hours=6)) == ["sync"]
    assert len(sync2.calls) == 1


def test_restart_after_long_downtime_still_waits_initial_delay(tmp_path):
    s, sync, _ = make(tmp_path)
    s.tick(T0)
    s.tick(T0 + M)
    s2, sync2, notif2 = make(tmp_path)
    t = T0 + timedelta(days=2)
    assert s2.tick(t) == ["notify"]
    assert s2.tick(t + M) == ["sync"]


def test_failed_run_is_logged_and_not_persisted(tmp_path, caplog):
    sync = Recorder(fail_on={1})
    s, _, notif = make(tmp_path, sync_fn=sync, sync_every=timedelta(hours=1))
    s.tick(T0)
    with caplog.at_level(logging.ERROR, logger="logr.scheduler"):
        assert s.tick(T0 + M) == ["sync"]
    assert "boom #1" in caplog.text and "sync" in caplog.text
    assert SchedulerState(tmp_path / "s.db").last_run("sync") is None
    # The other job keeps going, and the failed one runs again on schedule.
    assert s.tick(T0 + 15 * M) == ["notify"]
    assert "sync" in s.tick(T0 + M + timedelta(hours=1))
    assert len(sync.calls) == 2
    assert SchedulerState(tmp_path / "s.db").last_run("sync") == T0 + M + timedelta(hours=1)


def test_exception_in_notify_does_not_block_sync_in_same_tick(tmp_path):
    notif = Recorder(fail_on={1, 2})
    s, sync, _ = make(tmp_path, notify_fn=notif, initial_sync_delay=timedelta(0))
    assert s.tick(T0) == ["notify", "sync"]
    assert len(sync.calls) == 1
    assert s.tick(T0 + 15 * M) == ["notify"]
    assert s.tick(T0 + 30 * M) == ["notify"]
    assert len(notif.calls) == 3
    assert SchedulerState(tmp_path / "s.db").last_run("notify") == T0 + 30 * M


def test_no_overlap(tmp_path):
    inner: list[list[str]] = []
    s = None

    def slow_notify(now):
        # While running, the job comes due again (another tick fires): it must
        # not start a second time.
        inner.append(s.tick(now + timedelta(hours=1)))

    s, sync, _ = make(tmp_path, notify_fn=slow_notify)
    assert s.tick(T0) == ["notify"]
    assert inner == [["sync"]]  # the other job may run; notify itself did not re-enter
    assert s.tick(T0 + 15 * M) == ["notify"]


def test_no_overlap_across_threads(tmp_path):
    started, release = threading.Event(), threading.Event()
    calls = []

    def blocking_sync(now):
        calls.append(now)
        started.set()
        release.wait(5)

    s, _, _ = make(tmp_path, sync_fn=blocking_sync, initial_sync_delay=timedelta(0))
    th = threading.Thread(target=s.tick, args=(T0,))
    th.start()
    assert started.wait(5)
    assert "sync" not in s.tick(T0 + timedelta(days=1))
    release.set()
    th.join(5)
    assert len(calls) == 1


def test_rejects_bad_intervals(tmp_path):
    with pytest.raises(ValueError):
        Scheduler(tmp_path / "s.db", sync_every=timedelta(0))


def test_real_thread_start_stop(tmp_path):
    ran = threading.Event()
    s = Scheduler(tmp_path / "s.db", notify_fn=lambda now: ran.set(), sync_fn=lambda now: None)
    s.start()
    try:
        assert ran.wait(5)
        assert s.running
    finally:
        t = time.monotonic()
        assert s.stop(timeout=5)
        assert time.monotonic() - t < 2  # the Event wakes the loop; no 60 s wait
    assert not s.running


def test_resolve_admins(monkeypatch):
    assert resolve_admins({}) == ["local"]
    assert resolve_admins({"LOGR_ADMINS": "github:1"}) == ["local"]
