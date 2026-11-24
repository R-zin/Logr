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

from logr.models import Hackathon, Mode, Source, utcnow
from logr.sources import CONNECTORS
from logr.store import Store
from logr.sync import sync

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
        return (0, nd[1])
    return (1, h.starts_at or h.ends_at or datetime.max.replace(tzinfo=now.tzinfo))


def select(
    items: list[Hackathon],
    *,
    now: datetime,
    include_closed: bool = False,
    within_days: float | None = None,
    modes: list[Mode] | None = None,
    search: str | None = None,
) -> list[Hackathon]:
    out = []
    horizon = now + timedelta(days=within_days) if within_days is not None else None
    needle = search.lower() if search else None
    for h in items:
        if not include_closed and not h.is_open(now):
            continue
        if horizon is not None:
            nd = h.next_deadline(now)
            if nd is None or nd[1] > horizon:
                continue
        if modes and h.mode not in modes:
            continue
        if needle:
            hay = " ".join([h.title, h.organizer or "", h.location or "", *h.themes]).lower()
            if needle not in hay:
                continue
        out.append(h)
    return sorted(out, key=lambda h: _sort_key(h, now))


# --- commands ---------------------------------------------------------------


def cmd_sync(args: argparse.Namespace, store: Store) -> int:
    sources = [Source(s) for s in args.source] if args.source else None
    reports = sync(store, sources)
    failed = 0
    for r in reports:
        if r.error:
            failed += 1
            print(f"  ✗ {r.source:<11} {r.error}")
        else:
            res = r.result
            print(f"  ✓ {r.source:<11} {r.fetched:>4} fetched · {res.created} new · {res.updated} updated")
            for uid, f, old, new in res.date_changes:
                print(f"      ↳ {uid}: {f} {old or '—'} → {new or '—'}")
    return 1 if failed == len(reports) else 0


def cmd_list(args: argparse.Namespace, store: Store) -> int:
    now = utcnow()
    tz = _tz(args.tz)
    sources = [Source(s) for s in args.source] if args.source else None
    modes = [Mode(m) for m in args.mode] if args.mode else None
    rows = select(store.all(sources), now=now, include_closed=args.all, within_days=args.within,
                  modes=modes, search=args.search)
    if args.limit:
        rows = rows[: args.limit]

    if args.format == "json":
        json.dump([h.model_dump(mode="json") | {"uid": h.uid} for h in rows], sys.stdout, indent=2)
        print()
        return 0
    if args.format == "csv":
        w = csv.writer(sys.stdout)
        w.writerow(["uid", "title", "source", "mode", "location", "next_deadline_type", "next_deadline",
                    "registration_closes_at", "submission_deadline", "starts_at", "ends_at", "approx", "url"])
        for h in rows:
            nd = h.next_deadline(now)
            w.writerow([h.uid, h.title, h.source, h.mode, h.location or "", nd[0] if nd else "",
                        nd[1].isoformat() if nd else "", *[
                            (getattr(h, f).isoformat() if getattr(h, f) else "")
                            for f in ("registration_closes_at", "submission_deadline", "starts_at", "ends_at")],
                        h.dates_approximate, h.url])
        return 0

    if not rows:
        print("No hackathons match. Run `logr sync` first, or widen the filters.")
        return 0
    print(f"{'NEXT DEADLINE':<22} {'IN':>8}  {'SOURCE':<10} {'MODE':<7} TITLE")
    for h in rows:
        nd = h.next_deadline(now)
        if nd:
            when, left = _fmt_dt(nd[1], tz, h.dates_approximate), _fmt_delta(nd[1], now)
        else:
            when, left = "—", ""
        title = h.title if len(h.title) <= 60 else h.title[:57] + "..."
        print(f"{when:<22} {left:>8}  {h.source:<10} {h.mode:<7} {title}")
        if args.verbose:
            kind = nd[0] if nd else "no deadline published"
            print(f"{'':<42}{kind} · {h.uid} · {h.url}")
    print(f"\n{len(rows)} hackathon(s). '~' = approximate date. Times in {args.tz or 'local time'}.")
    return 0


def cmd_show(args: argparse.Namespace, store: Store) -> int:
    h = store.get(args.uid)
    if not h:
        print(f"Not found: {args.uid}", file=sys.stderr)
        return 1
    tz, ap = _tz(args.tz), h.dates_approximate
    print(h.title)
    print(f"  {h.url}")
    for label, val in [
        ("Source", h.source), ("Organizer", h.organizer), ("Mode", h.mode), ("Location", h.location),
        ("Themes", ", ".join(h.themes) or None), ("Prize", h.prize), ("Participants", h.participants),
        ("Team size", f"{h.team_size_min or '?'}–{h.team_size_max or '?'}" if h.team_size_max else None),
        ("Registration opens", _fmt_dt(h.registration_opens_at, tz, ap)),
        ("Registration closes", _fmt_dt(h.registration_closes_at, tz, ap)),
        ("Submission due", _fmt_dt(h.submission_deadline, tz, ap)),
        ("Starts", _fmt_dt(h.starts_at, tz, ap)), ("Ends", _fmt_dt(h.ends_at, tz, ap)),
    ]:
        if val not in (None, "—"):
            print(f"  {label:<20} {val}")
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
        state = f"✗ {r['error']}" if r["error"] else f"✓ {r['fetched']} fetched, {r['created']} new, {r['updated']} updated"
        print(f"  {r['source']:<11} {r['started_at'][:19]}Z  {state}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="logr", description="Find hackathons and track their deadlines.")
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"SQLite file (default {DEFAULT_DB}, or $LOGR_DB)")
    p.add_argument("--tz", default=os.environ.get("LOGR_TZ"), help="IANA timezone for display, e.g. Asia/Kolkata")
    sub = p.add_subparsers(dest="cmd", required=True)
    src_choices = [s.value for s in CONNECTORS]

    s = sub.add_parser("sync", help="fetch listings from sources")
    s.add_argument("--source", "-s", action="append", choices=src_choices)
    s.set_defaults(fn=cmd_sync)

    ls = sub.add_parser("list", help="list hackathons by next deadline")
    ls.add_argument("--source", "-s", action="append", choices=src_choices)
    ls.add_argument("--mode", "-m", action="append", choices=[m.value for m in Mode])
    ls.add_argument("--within", "-w", type=float, metavar="DAYS", help="only deadlines within N days")
    ls.add_argument("--search", "-q", help="text match on title/organizer/location/themes")
    ls.add_argument("--all", "-a", action="store_true", help="include closed/past listings")
    ls.add_argument("--limit", "-n", type=int)
    ls.add_argument("--format", "-f", choices=["table", "json", "csv"], default="table")
    ls.add_argument("--verbose", "-v", action="store_true", help="show uid and url")
    ls.set_defaults(fn=cmd_list)

    sh = sub.add_parser("show", help="details and date-change history for one hackathon")
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
