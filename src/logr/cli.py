"""Command-line interface: ``logr sync``, ``logr list``, ``logr show``, ``logr status``."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

from logr.dedupe import Listing, group
from logr.models import Hackathon, Mode, Source, utcnow
from logr.sources import CONNECTORS
from logr.sources.luma import resolve_place
from logr.store import Store
from logr.sync import SyncOptions, sync

DEFAULT_DB = Path(os.environ.get("LOGR_DB", Path.home() / ".local" / "share" / "logr" / "logr.db"))


def _tz(name: str | None) -> tzinfo | None:
    return ZoneInfo(name) if name else None  # None → system local time


def _fmt_dt(dt: datetime | None, tz: tzinfo | None, approx: bool = False) -> str:
    if dt is None:
        return "—"
    s = dt.astimezone(tz).strftime("%d %b %Y %H:%M")
    return f"~{s}" if approx else s


def _fmt_delta(dt: datetime, now: datetime) -> str:
    secs = int((dt - now).total_seconds())
    if secs < 0:
        return "passed"
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    return f"{d}d {h}h" if d else f"{h}h {rem // 60}m"


def _sort_key(h: Hackathon, now: datetime) -> tuple[int, datetime]:
    nd = h.next_deadline(now)
    if nd:
        return (0, nd.at)
    return (1, h.starts_at or h.ends_at or datetime.max.replace(tzinfo=now.tzinfo))


def _matches(h: Hackathon, *, now, include_closed, horizon, modes, needle) -> bool:
    if not include_closed and not h.is_open(now):
        return False
    if horizon is not None:
        nd = h.next_deadline(now)
        if nd is None or nd.at > horizon:
            return False
    if modes and h.mode not in modes:
        return False
    if needle:
        hay = " ".join([h.title, h.organizer or "", h.location or "", *h.themes]).lower()
        if needle not in hay:
            return False
    return True


def select(
    items: list[Hackathon],
    *,
    now: datetime,
    include_closed: bool = False,
    within_days: float | None = None,
    modes: list[Mode] | None = None,
    search: str | None = None,
    merge: bool = True,
) -> list[Listing]:
    """Filter, merge cross-source duplicates, and sort by next deadline.

    Merging happens before filtering so a hackathon is judged on its best record
    (e.g. Devfolio's exact dates rather than Luma's approximation)."""
    listings = group(items) if merge else [Listing(h) for h in items]
    horizon = now + timedelta(days=within_days) if within_days is not None else None
    needle = search.lower() if search else None
    out = [
        lst for lst in listings
        if _matches(lst.primary, now=now, include_closed=include_closed, horizon=horizon, modes=modes, needle=needle)
    ]
    return sorted(out, key=lambda lst: _sort_key(lst.primary, now))


def _src_label(lst: Listing) -> str:
    return "+".join(s.value for s in lst.sources) if len(lst.sources) <= 2 else f"{lst.sources[0]}+{len(lst.sources) - 1}"


# --- commands ---------------------------------------------------------------


def cmd_sync(args: argparse.Namespace, store: Store) -> int:
    sources = [Source(s) for s in args.source] if args.source else None
    places = list(args.luma_near or [])
    if not places and os.environ.get("LOGR_LUMA_NEAR"):
        places = [p for p in os.environ["LOGR_LUMA_NEAR"].split(";") if p.strip()]
    try:
        for p in places:
            resolve_place(p)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    opts = SyncOptions(
        enrich=not args.no_enrich,
        enrich_ttl=timedelta(hours=args.enrich_ttl),
        enrich_limit=args.enrich_limit,
        connector_opts={Source.LUMA: {"places": places}},
    )
    reports = sync(store, sources, options=opts)
    failed = 0
    for r in reports:
        if r.error:
            failed += 1
            print(f"  ✗ {r.source:<11} {r.error}")
            continue
        res = r.result
        extra = ""
        if r.enriched or r.enrich_errors:
            extra += f" · {r.enriched} detail pages" + (f" ({r.enrich_errors} failed)" if r.enrich_errors else "")
        if r.delisted:
            extra += f" · {r.delisted} delisted"
        print(f"  ✓ {r.source:<11} {r.fetched:>4} fetched · {res.created} new · {res.updated} updated{extra}")
        for uid, f, old, new in res.date_changes:
            print(f"      ↳ {uid}: {f} {old or '—'} → {new or '—'}")
    return 1 if failed == len(reports) else 0


def cmd_list(args: argparse.Namespace, store: Store) -> int:
    now = utcnow()
    tz = _tz(args.tz)
    sources = [Source(s) for s in args.source] if args.source else None
    modes = [Mode(m) for m in args.mode] if args.mode else None
    rows = select(store.all(sources, include_delisted=args.all), now=now, include_closed=args.all,
                  within_days=args.within, modes=modes, search=args.search, merge=not args.no_merge)
    if args.limit:
        rows = rows[: args.limit]

    if args.format == "json":
        out = []
        for lst in rows:
            h = lst.primary
            nd = h.next_deadline(now)
            out.append(h.model_dump(mode="json") | {
                "uid": h.uid,
                "next_deadline": nd._asdict() | {"at": nd.at.isoformat()} if nd else None,
                "also_listed": [{"uid": o.uid, "url": o.url} for o in lst.others],
            })
        json.dump(out, sys.stdout, indent=2)
        print()
        return 0
    if args.format == "csv":
        w = csv.writer(sys.stdout)
        w.writerow(["uid", "title", "sources", "mode", "location", "next_deadline_type", "next_deadline",
                    "next_deadline_approx", "registration_closes_at", "submission_deadline", "starts_at",
                    "ends_at", "url", "also_listed_urls"])
        for lst in rows:
            h = lst.primary
            nd = h.next_deadline(now)
            w.writerow([h.uid, h.title, "+".join(lst.sources), h.mode, h.location or "",
                        nd.label if nd else "", nd.at.isoformat() if nd else "", nd.approximate if nd else "",
                        *[(getattr(h, f).isoformat() if getattr(h, f) else "")
                          for f in ("registration_closes_at", "submission_deadline", "starts_at", "ends_at")],
                        h.url, " ".join(o.url for o in lst.others)])
        return 0

    if not rows:
        print("No hackathons match. Run `logr sync` first, or widen the filters.")
        return 0
    print(f"{'NEXT DEADLINE':<22} {'IN':>8}  {'SOURCE':<16} {'MODE':<7} TITLE")
    for lst in rows:
        h = lst.primary
        nd = h.next_deadline(now)
        when, left = (_fmt_dt(nd.at, tz, nd.approximate), _fmt_delta(nd.at, now)) if nd else ("—", "")
        title = h.title if len(h.title) <= 60 else h.title[:57] + "..."
        print(f"{when:<22} {left:>8}  {_src_label(lst):<16} {h.mode:<7} {title}")
        if args.verbose:
            kind = nd.label if nd else "no deadline published"
            print(f"{'':<50}{kind} · {h.uid} · {h.url}")
            for o in lst.others:
                print(f"{'':<50}also on {o.source}: {o.url}")
    merged = sum(len(lst.others) for lst in rows)
    note = f" ({merged} duplicate listing(s) merged)" if merged else ""
    print(f"\n{len(rows)} hackathon(s){note}. '~' = approximate. Times in {args.tz or 'local time'}.")
    return 0


def cmd_show(args: argparse.Namespace, store: Store) -> int:
    h = store.get(args.uid)
    if not h:
        print(f"Not found: {args.uid}", file=sys.stderr)
        return 1
    tz = _tz(args.tz)
    meta = store.meta(h.uid)
    print(h.title + ("  [delisted]" if meta and meta.delisted_at else ""))
    print(f"  {h.url}")

    def d(f: str) -> str:
        return _fmt_dt(getattr(h, f), tz, h.is_approx(f))

    for label, val in [
        ("Source", h.source), ("Organizer", h.organizer), ("Mode", h.mode), ("Location", h.location),
        ("Themes", ", ".join(h.themes) or None), ("Prize", h.prize), ("Participants", h.participants),
        ("Team size", f"{h.team_size_min or '?'}–{h.team_size_max or '?'}" if h.team_size_max else None),
        ("Registration opens", d("registration_opens_at")),
        ("Registration closes", d("registration_closes_at")),
        ("Submission due", d("submission_deadline")),
        ("Starts", d("starts_at")), ("Ends", d("ends_at")),
    ]:
        if val not in (None, "—"):
            print(f"  {label:<20} {val}")
    nd = h.next_deadline()
    if nd:
        print(f"  {'Next deadline':<20} {nd.label}, {_fmt_dt(nd.at, tz, nd.approximate)} (in {_fmt_delta(nd.at, utcnow())})")
    if h.rounds:
        print("  Timeline:")
        for r in h.rounds:
            print(f"    {r.name:<28.28} {r.kind:<12} {_fmt_dt(r.starts_at, tz):>17} → {_fmt_dt(r.ends_at, tz)}")
    twins = next((lst for lst in group(store.all()) if h.uid in {x.uid for x in lst.all}), None)
    if twins and len(twins.all) > 1:
        print("  Also listed on:")
        for o in twins.all:
            if o.uid != h.uid:
                print(f"    {o.source:<11} {o.url}  ({o.uid})")
    hist = store.history(h.uid)
    if hist:
        print("  Date changes:")
        for r in hist:
            print(f"    {r['changed_at'][:16]}  {r['field']}: {r['old_value'] or '—'} → {r['new_value'] or '—'}")
    return 0


def cmd_status(args: argparse.Namespace, store: Store) -> int:
    runs = store.last_runs()
    if not runs:
        print("No syncs yet. Run `logr sync`.")
        return 0
    for r in runs:
        if r["error"]:
            state = f"✗ {r['error']}"
        else:
            state = f"✓ {r['fetched']} fetched, {r['created']} new, {r['updated']} updated"
            if r["enriched"] or r["enrich_errors"]:
                state += f", {r['enriched']} detail pages ({r['enrich_errors']} failed)"
            if r["delisted"]:
                state += f", {r['delisted']} delisted"
        print(f"  {r['source']:<11} {r['started_at'][:19]}Z  {state}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="logr", description="Find hackathons and track their deadlines.")
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"SQLite file (default {DEFAULT_DB}, or $LOGR_DB)")
    p.add_argument("--tz", default=os.environ.get("LOGR_TZ"), help="IANA timezone for display, e.g. Asia/Kolkata")
    sub = p.add_subparsers(dest="cmd", required=True)
    src_choices = [s.value for s in CONNECTORS]

    s = sub.add_parser("sync", help="fetch listings (and detail pages) from sources")
    s.add_argument("--source", "-s", action="append", choices=src_choices)
    s.add_argument("--no-enrich", action="store_true", help="listings only; skip detail-page requests")
    s.add_argument("--enrich-ttl", type=float, default=12, metavar="HOURS",
                   help="re-fetch a detail page after this long (default 12)")
    s.add_argument("--enrich-limit", type=int, default=150, metavar="N",
                   help="max detail requests per source per sync (default 150)")
    s.add_argument("--luma-near", action="append", metavar="PLACE",
                   help="also search Luma near a city (bengaluru, delhi, london...) or 'lat,lng'; "
                        "repeatable; default from $LOGR_LUMA_NEAR (';'-separated)")
    s.set_defaults(fn=cmd_sync)

    ls = sub.add_parser("list", help="list hackathons by next deadline")
    ls.add_argument("--source", "-s", action="append", choices=src_choices)
    ls.add_argument("--mode", "-m", action="append", choices=[m.value for m in Mode])
    ls.add_argument("--within", "-w", type=float, metavar="DAYS", help="only deadlines within N days")
    ls.add_argument("--search", "-q", help="text match on title/organizer/location/themes")
    ls.add_argument("--all", "-a", action="store_true", help="include closed, past and delisted listings")
    ls.add_argument("--no-merge", action="store_true", help="don't merge the same hackathon across sources")
    ls.add_argument("--limit", "-n", type=int)
    ls.add_argument("--format", "-f", choices=["table", "json", "csv"], default="table")
    ls.add_argument("--verbose", "-v", action="store_true", help="show uid, url and other listings")
    ls.set_defaults(fn=cmd_list)

    sh = sub.add_parser("show", help="details, timeline and date-change history for one hackathon")
    sh.add_argument("uid", help="e.g. devfolio:931ca9149c46417bb0526ce1eb44b920")
    sh.set_defaults(fn=cmd_show)

    st = sub.add_parser("status", help="last sync result per source")
    st.set_defaults(fn=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = Store(args.db)
    try:
        return args.fn(args, store)
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
