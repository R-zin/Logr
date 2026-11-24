"""Run connectors and persist results. One failing source never blocks the others."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from logr.http import Http
from logr.models import Source
from logr.sources import CONNECTORS
from logr.store import Store, UpsertResult


@dataclass
class SourceReport:
    source: Source
    fetched: int
    result: UpsertResult | None
    error: str | None


def sync(store: Store, sources: Iterable[Source] | None = None, *, http: Http | None = None) -> list[SourceReport]:
    own_http = http is None
    http = http or Http()
    reports: list[SourceReport] = []
    try:
        for src in sources or CONNECTORS:
            run_id = store.start_run(src)
            items, error, result = [], None, None
            try:
                items = list(CONNECTORS[src](http).fetch())
                result = store.upsert_many(items)
            except Exception as exc:  # noqa: BLE001 — isolate per-source failures
                error = f"{type(exc).__name__}: {exc}"[:500]
            store.finish_run(run_id, fetched=len(items), result=result, error=error)
            reports.append(SourceReport(src, len(items), result, error))
    finally:
        if own_http:
            http.close()
    return reports
