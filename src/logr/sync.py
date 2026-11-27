"""Run connectors, enrich with detail pages, persist. One failing source never blocks the others."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from logr.http import Http
from logr.models import Hackathon, Source, utcnow
from logr.sources import CONNECTORS
from logr.sources.base import Connector
from logr.store import DELIST_AFTER, Store, UpsertResult


@dataclass
class SyncOptions:
    enrich: bool = True
    enrich_ttl: timedelta = timedelta(hours=12)
    enrich_limit: int = 150  # detail requests per source per sync
    delist_after: int = DELIST_AFTER
    connector_opts: dict[Source, dict[str, Any]] = field(default_factory=dict)


@dataclass
class SourceReport:
    source: Source
    fetched: int
    result: UpsertResult | None
    error: str | None
    enriched: int = 0
    enrich_errors: int = 0
    delisted: int = 0


def _enrich(conn: Connector, store: Store, items: list[Hackathon], opts: SyncOptions) -> tuple[list[Hackathon], int, int]:
    """Fetch detail for open listings whose cache is stale, soonest deadline
    first, then overlay every cached patch (fresh or not) onto the listings."""
    now = utcnow()
    done = errors = 0
    if opts.enrich and conn.can_enrich:
        def urgency(h: Hackathon):
            nd = h.next_deadline(now)
            return (nd is None, nd.at if nd else now)

        for h in sorted((h for h in items if h.is_open(now)), key=urgency):
            if done + errors >= opts.enrich_limit:
                break
            if store.enrichment_is_fresh(store.get_enrichment(h.uid), opts.enrich_ttl, now):
                continue
            try:
                store.save_enrichment(h.uid, conn.enrich(h))
                done += 1
            except Exception as exc:  # noqa: BLE001 — one bad page must not stop the rest
                store.save_enrichment(h.uid, None, f"{type(exc).__name__}: {exc}"[:300])
                errors += 1
    out = []
    for h in items:
        e = store.get_enrichment(h.uid)
        out.append(h.with_patch(e.patch) if e and e.patch else h)
    return out, done, errors


def sync(
    store: Store,
    sources: Iterable[Source] | None = None,
    *,
    http: Http | None = None,
    options: SyncOptions | None = None,
) -> list[SourceReport]:
    opts = options or SyncOptions()
    own_http = http is None
    http = http or Http()
    reports: list[SourceReport] = []
    try:
        for src in sources or CONNECTORS:
            run_id = store.start_run(src)
            rep = SourceReport(src, 0, None, None)
            try:
                conn = CONNECTORS[src](http, **opts.connector_opts.get(src, {}))
                items = list(conn.fetch())
                rep.fetched = len(items)
                items, rep.enriched, rep.enrich_errors = _enrich(conn, store, items, opts)
                rep.result = store.upsert_many(items)
                # An empty result usually means the endpoint broke, not that every
                # hackathon vanished — don't let it delist the whole source.
                if items:
                    rep.delisted = store.mark_seen(src, {h.uid for h in items}, delist_after=opts.delist_after)
            except Exception as exc:  # noqa: BLE001 — isolate per-source failures
                rep.error = f"{type(exc).__name__}: {exc}"[:500]
            store.finish_run(run_id, fetched=rep.fetched, result=rep.result, error=rep.error,
                             enriched=rep.enriched, enrich_errors=rep.enrich_errors, delisted=rep.delisted)
            reports.append(rep)
    finally:
        if own_http:
            http.close()
    return reports
