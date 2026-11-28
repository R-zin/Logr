"""Sync: GET /api/sync/status (public), POST /api/sync (admin, background thread)."""

from __future__ import annotations

import logging
import os
import threading

from fastapi import APIRouter, HTTPException, Request

import logr.sync as logr_sync
from logr.models import Source
from logr.searches import Preferences
from logr.sources.luma import resolve_place
from logr.store import Store

from .deps import AdminDep, StoreDep, dt_or_none
from .schemas import SyncRunOut, SyncStarted, SyncStatusOut

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sync")

# One sync at a time per process, whichever app instance started it.
_lock = threading.Lock()


def is_running() -> bool:
    return _lock.locked()


def _luma_places(store: Store, user_id: str) -> list[str]:
    raw = Preferences(store.db, user_id).get("luma_near") or os.environ.get("LOGR_LUMA_NEAR") or ""
    places = []
    for p in (x.strip() for x in raw.split(";")):
        if not p:
            continue
        try:
            resolve_place(p)
        except ValueError:
            log.warning("ignoring bad luma_near place %r", p)
            continue
        places.append(p)
    return places


def _run(db_path: str, user_id: str) -> None:
    try:
        store = Store(db_path)
        try:
            opts = logr_sync.SyncOptions(connector_opts={Source.LUMA: {"places": _luma_places(store, user_id)}})
            logr_sync.sync(store, None, options=opts)  # looked up at call time (tests stub it)
        finally:
            store.close()
    except Exception:  # a background thread has nobody to raise to
        log.exception("background sync failed")
    finally:
        _lock.release()


@router.get("/status", response_model=SyncStatusOut)
def sync_status(store: StoreDep) -> SyncStatusOut:
    runs = [
        SyncRunOut(
            source=r["source"], started_at=dt_or_none(r["started_at"]), finished_at=dt_or_none(r["finished_at"]),
            fetched=r["fetched"] or 0, created=r["created"] or 0, updated=r["updated"] or 0,
            enriched=r["enriched"] or 0, enrich_errors=r["enrich_errors"] or 0, delisted=r["delisted"] or 0,
            error=r["error"],
        )
        for r in store.last_runs()
    ]
    return SyncStatusOut(runs=runs, running=is_running())


@router.post("", status_code=202, response_model=SyncStarted)
def start_sync(request: Request, admin: AdminDep) -> SyncStarted:
    if not _lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a sync is already running")
    try:
        threading.Thread(target=_run, args=(request.app.state.db_path, admin.id),
                         name="logr-sync", daemon=True).start()
    except BaseException:
        _lock.release()
        raise
    return SyncStarted(started=True)

