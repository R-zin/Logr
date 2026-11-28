"""Command-line interface.

Discovery: ``logr sync``, ``logr list``, ``logr show``, ``logr status`` (sync status).
Tracking: ``logr track/untrack/mark/mine/note/repo/team/todo``.
Saved searches and preferences: ``logr search ...``, ``logr prefs ...``.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone, tzinfo
from functools import cached_property
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from logr.dedupe import Listing, group
from logr.models import Hackathon, Mode, Source, utcnow
from logr.searches import PREF_KEYS, Filter, Preferences, SavedSearches
from logr.sources import CONNECTORS
from logr.sources.luma import resolve_place
from logr.store import Store
from logr.sync import SyncOptions, sync
from logr.tracking import TERMINAL, Status, Tracked, Tracker, next_action

DEFAULT_DB = Path(os.environ.get("LOGR_DB", Path.home() / ".local" / "share" / "logr" / "logr.db"))


class CliError(Exception):
    """Printed as ``error: <msg>`` on stderr; ``main`` returns ``code``."""

    def __init__(self, msg: str, code: int = 1) -> None:
        super().__init__(msg)
        self.code = code


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


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


def _sort_key(h: Hackathon, now: datetime) -> tuple[int, datetime]:
    nd = h.next_deadline(now)
    if nd:
        return (0, nd.at)
    return (1, h.starts_at or h.ends_at or datetime.max.replace(tzinfo=now.tzinfo))


def select(
    items: list[Hackathon],
    *,
    now: datetime,
    include_closed: bool = False,
    within_days: float | None = None,
    modes: list[Mode] | None = None,
    search: str | None = None,
    merge: bool = True,
    filter: Filter | None = None,
) -> list[Listing]:
    """Filter, merge cross-source duplicates, and sort by next deadline.

    The keyword criteria are layered over ``filter`` (they win where set).
    Merging happens before filtering so a hackathon is judged on its best record
    (e.g. Devfolio's exact dates rather than Luma's approximation)."""
    f = Filter(include_closed=include_closed, within_days=within_days, modes=modes or None, search=search or None)
    if filter is not None:
        f = filter.merged(f)
    listings = group(items) if merge else [Listing(h) for h in items]
    out = [lst for lst in listings if f.matches(lst.primary, now)]
    return sorted(out, key=lambda lst: _sort_key(lst.primary, now))


def _src_label(lst: Listing) -> str:
    return "+".join(s.value for s in lst.sources) if len(lst.sources) <= 2 else f"{lst.sources[0]}+{len(lst.sources) - 1}"


# --- context ----------------------------------------------------------------


class Ctx:
    """What every command gets: the store plus the Phase 3 stores on the same
    SQLite connection (built on first use, once per invocation) and the
    resolved display timezone."""

    def __init__(self, args: argparse.Namespace, store: Store) -> None:
        self.args = args
        self.store = store

    @cached_property
    def _best_by_uid(self) -> dict[str, Hackathon]:
        """uid → best record of its cross-source group (the merged primary)."""
        out: dict[str, Hackathon] = {}
        for lst in group(self.store.all(include_delisted=True)):
            for h in lst.all:
                out[h.uid] = lst.primary
        return out

    def best(self, uid: str) -> Hackathon | None:
        """The record to take deadlines from for ``uid``: if you tracked the Luma
        copy of a hackathon also on Devfolio, Devfolio's exact dates win."""
        return self._best_by_uid.get(uid) or self.store.get(uid)

    @cached_property
    def tracker(self) -> Tracker:
        return Tracker(self.store.db)

    @cached_property
    def searches(self) -> SavedSearches:
        return SavedSearches(self.store.db)

    @cached_property
    def prefs(self) -> Preferences:
        return Preferences(self.store.db)

    @cached_property
    def tz_name(self) -> str | None:
        """``--tz`` > ``$LOGR_TZ`` > pref ``tz`` > None (system local)."""
        return self.args.tz or os.environ.get("LOGR_TZ") or self.prefs.get("tz") or None

    @cached_property
    def tz(self) -> tzinfo | None:
        try:
            return _tz(self.tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise CliError(f"unknown timezone {self.tz_name!r}", 2) from None

    @property
    def tz_label(self) -> str:
        return self.tz_name or "local time"

    def tracked_map(self) -> dict[str, Tracked]:
        return {t.uid: t for t in self.tracker.all()}

    def require_tracked(self, uid: str) -> Tracked:
        t = self.tracker.get(uid)
        if t is None:
            raise CliError(f"not tracking {uid}; start with `logr track {uid}`")
        return t


def _parse_due(text: str, tz: tzinfo | None) -> datetime:
    """``YYYY-MM-DD`` (23:59 that day) or ``YYYY-MM-DD HH:MM``, in ``tz``
    (None = system local), returned as UTC."""
    text = text.strip()
    for fmt, end_of_day in (("%Y-%m-%d", True), ("%Y-%m-%d %H:%M", False), ("%Y-%m-%dT%H:%M", False)):
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if end_of_day:
            dt = dt.replace(hour=23, minute=59)
        local = dt.replace(tzinfo=tz) if tz is not None else dt.astimezone()
        return local.astimezone(timezone.utc)
    raise CliError(f"bad --due {text!r}; use YYYY-MM-DD or 'YYYY-MM-DD HH:MM'", 2)


def _norm_name(name: str) -> str:
    return name.strip().lower()


# --- discovery ----------------------------------------------------------------


def cmd_sync(args: argparse.Namespace, ctx: Ctx) -> int:
    sources = [Source(s) for s in args.source] if args.source else None
    places = list(args.luma_near or [])
    if not places:
        raw = os.environ.get("LOGR_LUMA_NEAR") or ctx.prefs.get("luma_near") or ""
        places = [p for p in raw.split(";") if p.strip()]
    try:
        for p in places:
            resolve_place(p)
    except ValueError as exc:
        raise CliError(str(exc), 2) from None
    opts = SyncOptions(
        enrich=not args.no_enrich,
        enrich_ttl=timedelta(hours=args.enrich_ttl),
        enrich_limit=args.enrich_limit,
        connector_opts={Source.LUMA: {"places": places}},
    )
    reports = sync(ctx.store, sources, options=opts)
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


def _filter_from_args(args: argparse.Namespace) -> Filter:
    return Filter(
        sources=[Source(s) for s in args.source] if args.source else None,
        modes=[Mode(m) for m in args.mode] if args.mode else None,
        within_days=args.within,
        search=args.search or None,
        include_closed=args.all,
        themes=list(args.theme) if args.theme else None,
        region=args.region or None,
        min_prize_usd=args.min_prize,
        team_size=args.team_size,
    )


def _base_filter(args: argparse.Namespace, ctx: Ctx) -> tuple[str | None, Filter | None]:
    """The saved search ``list`` starts from: ``--saved``, else the
    ``default_search`` pref unless ``--no-prefs``."""
    name = args.saved
    if name is None and not args.no_prefs:
        name = ctx.prefs.get("default_search")
    if name is None:
        return None, None
    name = _norm_name(name)
    f = ctx.searches.get(name)
    if f is None:
        hint = " (set by pref default_search; `logr prefs unset default_search`)" if args.saved is None else ""
        raise CliError(f"no saved search {name!r}{hint}", 2)
    return name, f


def cmd_list(args: argparse.Namespace, ctx: Ctx) -> int:
    now = utcnow()
    saved_name, base = _base_filter(args, ctx)
    f = _filter_from_args(args)
    if base is not None:
        f = base.merged(f)
    rows = select(ctx.store.all(f.sources, include_delisted=f.include_closed), now=now,
                  merge=not args.no_merge, filter=f)
    if args.limit:
        rows = rows[: args.limit]
    tracked = ctx.tracked_map()

    def tracked_of(lst: Listing) -> Tracked | None:
        return next((tracked[h.uid] for h in lst.all if h.uid in tracked), None)

    if args.format == "json":
        out = []
        for lst in rows:
            h = lst.primary
            nd = h.next_deadline(now)
            t = tracked_of(lst)
            out.append(h.model_dump(mode="json") | {
                "uid": h.uid,
                "next_deadline": nd._asdict() | {"at": nd.at.isoformat()} if nd else None,
                "also_listed": [{"uid": o.uid, "url": o.url} for o in lst.others],
                "tracked_status": t.status.value if t else None,
            })
        json.dump(out, sys.stdout, indent=2)
        print()
        return 0
    if args.format == "csv":
        w = csv.writer(sys.stdout)
        w.writerow(["uid", "title", "sources", "mode", "location", "next_deadline_type", "next_deadline",
                    "next_deadline_approx", "registration_closes_at", "submission_deadline", "starts_at",
                    "ends_at", "url", "also_listed_urls", "tracked_status"])
        for lst in rows:
            h = lst.primary
            nd = h.next_deadline(now)
            t = tracked_of(lst)
            w.writerow([h.uid, h.title, "+".join(lst.sources), h.mode, h.location or "",
                        nd.label if nd else "", nd.at.isoformat() if nd else "", nd.approximate if nd else "",
                        *[(getattr(h, f).isoformat() if getattr(h, f) else "")
                          for f in ("registration_closes_at", "submission_deadline", "starts_at", "ends_at")],
                        h.url, " ".join(o.url for o in lst.others), t.status.value if t else ""])
        return 0

    tz = ctx.tz
    using = f"Saved search '{saved_name}': {base.describe()}." if saved_name and base is not None else ""
    if not rows:
        print("No hackathons match. Run `logr sync` first, or widen the filters.")
        if using:
            print(using + (" Use --no-prefs to ignore it." if args.saved is None else ""))
        return 0
    print(f"{'NEXT DEADLINE':<22} {'IN':>8}  {'SOURCE':<16} {'MODE':<7} TITLE")
    for lst in rows:
        h = lst.primary
        nd = h.next_deadline(now)
        when, left = (_fmt_dt(nd.at, tz, nd.approximate), _fmt_delta(nd.at, now)) if nd else ("—", "")
        title = ("★ " if tracked_of(lst) else "") + h.title
        print(f"{when:<22} {left:>8}  {_src_label(lst):<16} {h.mode:<7} {_clip(title, 60)}")
        if args.verbose:
            kind = nd.label if nd else "no deadline published"
            print(f"{'':<50}{kind} · {h.uid} · {h.url}")
            for o in lst.others:
                print(f"{'':<50}also on {o.source}: {o.url}")
    merged = sum(len(lst.others) for lst in rows)
    note = f" ({merged} duplicate listing(s) merged)" if merged else ""
    star = " '★' = tracked." if any(tracked_of(lst) for lst in rows) else ""
    print(f"\n{len(rows)} hackathon(s){note}. '~' = approximate.{star} Times in {ctx.tz_label}.")
    if using:
        print(using + (" Use --no-prefs to ignore it." if args.saved is None else ""))
    return 0


def _print_tracking(t: Tracked, h: Hackathon | None, ctx: Ctx) -> None:
    tz, now = ctx.tz, utcnow()
    print("  Tracking:")
    print(f"    {'Status':<18} {t.status}  (tracking since {_fmt_dt(t.added_at, tz)})")
    na = next_action(ctx.best(t.uid) if h is not None else None, t, now)
    if na:
        print(f"    {'Next action':<18} {na.label}, {_fmt_dt(na.at, tz, na.approximate)} (in {_fmt_delta(na.at, now)})")
    if t.team:
        print(f"    {'Team':<18} {', '.join(t.team)}")
    if t.repo_url:
        print(f"    {'Repo':<18} {t.repo_url}")
    if t.checklist:
        print("    Checklist:")
        for it in t.checklist:
            due = f"  (due {_fmt_dt(it.due_at, tz)})" if it.due_at else ""
            print(f"      #{it.id:<4} [{'x' if it.done else ' '}] {it.text}{due}")
    if t.notes:
        print("    Notes:")
        for line in t.notes.splitlines():
            print(f"      {line}")
    hist = ctx.tracker.history(t.uid)
    if hist:
        print("    Status history:")
        for c in hist:
            note = f"  ({c.note})" if c.note else ""
            print(f"      {_fmt_dt(c.changed_at, tz)}  {c.old or '—'} → {c.new}{note}")


def cmd_show(args: argparse.Namespace, ctx: Ctx) -> int:
    store = ctx.store
    h = store.get(args.uid)
    t = ctx.tracker.get(args.uid)
    if not h:
        if t is None:
            print(f"Not found: {args.uid}", file=sys.stderr)
            return 1
        print(f"{args.uid}  [listing no longer in the database]")
        _print_tracking(t, None, ctx)
        return 0
    tz = ctx.tz
    meta = store.meta(h.uid)
    print(("★ " if t else "") + h.title + ("  [delisted]" if meta and meta.delisted_at else ""))
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
    if t:
        _print_tracking(t, h, ctx)
    return 0


DEFAULT_WEB_DIR = Path(__file__).resolve().parents[2] / "web" / "out"


def cmd_serve(args: argparse.Namespace, ctx: Ctx) -> int:
    try:
        from logr.api import run  # needs the [web] extra
    except ImportError as exc:
        raise CliError(f"the web server needs extra packages: pip install -e '.[web]' ({exc.name} missing)") from None
    static = args.static or (DEFAULT_WEB_DIR if DEFAULT_WEB_DIR.is_dir() else None)
    if static is None:
        print("note: no built dashboard found (web/out); serving the API only. "
              "Build it with: cd web && npm install && npm run build", file=sys.stderr)
    ctx.store.close()  # the server opens its own connection per request
    run(args.db, host=args.host, port=args.port, static_dir=static)
    return 0


def cmd_status(args: argparse.Namespace, ctx: Ctx) -> int:
    runs = ctx.store.last_runs()
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


# --- tracking -----------------------------------------------------------------


def _label(uid: str, h: Hackathon | None) -> str:
    return f"{h.title} ({uid})" if h else uid


def cmd_track(args: argparse.Namespace, ctx: Ctx) -> int:
    h = ctx.store.get(args.uid)
    if h is None:
        raise CliError(f"no hackathon {args.uid!r} in the database (see `logr list -v` for uids)")
    existing = ctx.tracker.get(args.uid)
    if existing:
        print(f"Already tracking {_label(args.uid, h)} — status: {existing.status}")
        if args.status and Status(args.status) != existing.status:
            print(f"Use `logr mark {args.uid} {args.status}` to change it.")
        return 0
    t = ctx.tracker.track(args.uid, Status(args.status or Status.INTERESTED))
    print(f"★ Tracking {_label(args.uid, h)} — status: {t.status}")
    return 0


def cmd_untrack(args: argparse.Namespace, ctx: Ctx) -> int:
    if not ctx.tracker.untrack(args.uid):
        raise CliError(f"not tracking {args.uid}")
    print(f"Stopped tracking {_label(args.uid, ctx.store.get(args.uid))}")
    return 0


def cmd_mark(args: argparse.Namespace, ctx: Ctx) -> int:
    old = ctx.require_tracked(args.uid).status
    new = Status(args.status)
    if old == new:
        print(f"{args.uid} is already {new}")
        return 0
    ctx.tracker.set_status(args.uid, new, args.note)
    print(f"{_label(args.uid, ctx.store.get(args.uid))}: {old} → {new}")
    return 0


def cmd_mine(args: argparse.Namespace, ctx: Ctx) -> int:
    now = utcnow()
    statuses = [Status(s) for s in args.status] if args.status else None
    items = ctx.tracker.all(statuses)
    hidden = 0
    if not args.all and not statuses:
        hidden = sum(1 for t in items if t.status in TERMINAL)
        items = [t for t in items if t.status not in TERMINAL]
    if not items:
        if hidden:
            print(f"Nothing active. {hidden} finished hackathon(s) hidden; use --all to show them.")
        elif statuses:
            print(f"Nothing tracked with status {', '.join(statuses)}.")
        else:
            print("You're not tracking any hackathons yet. Find one with `logr list -v`, then `logr track UID`.")
        return 0
    tz = ctx.tz
    rows = []
    for t in items:
        h = ctx.store.get(t.uid)
        rows.append((t, h, next_action(ctx.best(t.uid) if h else None, t, now)))
    far = datetime.max.replace(tzinfo=timezone.utc)
    rows.sort(key=lambda r: (r[2] is None, r[2].at if r[2] else far))
    print(f"{'STATUS':<12} {'NEXT ACTION':<28} {'WHEN':<22} {'IN':>8}  TITLE")
    for t, h, na in rows:
        label, when, left = (na.label, _fmt_dt(na.at, tz, na.approximate), _fmt_delta(na.at, now)) if na else ("—", "—", "")
        print(f"{t.status:<12} {_clip(label, 28):<28} {when:<22} {left:>8}  {_clip(h.title if h else t.uid, 60)}")
    extra = f" ({hidden} finished hidden; --all to show)" if hidden else ""
    print(f"\n{len(rows)} tracked{extra}. Times in {ctx.tz_label}.")
    return 0


def _tracker_call(fn, *a, **kw):
    """Run a Tracker mutation, turning its KeyError/ValueError into a CLI error."""
    try:
        return fn(*a, **kw)
    except ValueError as exc:
        raise CliError(str(exc)) from None
    except KeyError as exc:
        raise CliError(f"not found: {exc.args[0] if exc.args else exc}") from None


def cmd_note(args: argparse.Namespace, ctx: Ctx) -> int:
    ctx.require_tracked(args.uid)
    _tracker_call(ctx.tracker.append_note, args.uid, " ".join(args.text))
    print(f"Note added to {args.uid}")
    return 0


def cmd_repo(args: argparse.Namespace, ctx: Ctx) -> int:
    if args.clear == bool(args.url):
        raise CliError("give a URL, or --clear to remove it", 2)
    ctx.require_tracked(args.uid)
    t = _tracker_call(ctx.tracker.set_repo, args.uid, None if args.clear else args.url)
    print(f"Repo for {args.uid}: {t.repo_url}" if t.repo_url else f"Repo for {args.uid} cleared")
    return 0


def cmd_team(args: argparse.Namespace, ctx: Ctx) -> int:
    ctx.require_tracked(args.uid)
    name = " ".join(args.name)
    fn = ctx.tracker.add_member if args.action == "add" else ctx.tracker.remove_member
    t = _tracker_call(fn, args.uid, name)
    print(f"Team for {args.uid}: {', '.join(t.team) or '(nobody yet)'}")
    return 0


def cmd_todo(args: argparse.Namespace, ctx: Ctx) -> int:
    ctx.require_tracked(args.uid)
    tr = ctx.tracker
    if args.action == "add":
        due = _parse_due(args.due, ctx.tz) if args.due else None
        it = _tracker_call(tr.add_todo, args.uid, " ".join(args.text), due)
        when = f" (due {_fmt_dt(it.due_at, ctx.tz)})" if it.due_at else ""
        print(f"Added #{it.id}: {it.text}{when}")
        return 0
    try:
        if args.action == "rm":
            tr.remove_todo(args.uid, args.id)
        else:
            tr.set_todo_done(args.uid, args.id, args.action == "done")
    except KeyError:
        raise CliError(f"no todo #{args.id} on {args.uid}") from None
    verb = {"rm": "Removed", "done": "Done:", "undo": "Reopened"}[args.action]
    print(f"{verb} #{args.id}")
    return 0


# --- saved searches & preferences ---------------------------------------------


def cmd_search_save(args: argparse.Namespace, ctx: Ctx) -> int:
    f = _filter_from_args(args)
    if f.is_empty():
        raise CliError("nothing to save; give at least one filter (e.g. -m online, --theme ai, -w 14)", 2)
    try:
        ctx.searches.save(args.name, f)
    except ValueError as exc:
        raise CliError(str(exc), 2) from None
    print(f"Saved search '{_norm_name(args.name)}': {f.describe()}")
    return 0


def cmd_search_ls(args: argparse.Namespace, ctx: Ctx) -> int:
    rows = ctx.searches.all()
    if not rows:
        print("No saved searches. Create one with `logr search save NAME [filters]`.")
        return 0
    default = ctx.prefs.get("default_search")
    width = max(len(n) for n, _ in rows)
    for name, f in rows:
        mark = "  (default)" if default and _norm_name(default) == name else ""
        print(f"  {name:<{width}}  {f.describe()}{mark}")
    return 0


def cmd_search_rm(args: argparse.Namespace, ctx: Ctx) -> int:
    name = _norm_name(args.name)
    if not ctx.searches.delete(name):
        raise CliError(f"no saved search {name!r}")
    print(f"Deleted saved search '{name}'")
    default = ctx.prefs.get("default_search")
    if default and _norm_name(default) == name:
        ctx.prefs.unset("default_search")
        print("It was your default_search; that preference is now unset.")
    return 0


def cmd_prefs(args: argparse.Namespace, ctx: Ctx) -> int:
    values = ctx.prefs.all()
    for key, desc in PREF_KEYS.items():
        val = values.get(key)
        print(f"  {key:<15} {val if val is not None else '(unset)'}")
        print(f"  {'':<15} {desc}")
    return 0


def _validate_pref(key: str, value: str, ctx: Ctx) -> str:
    if key == "tz":
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise CliError(f"unknown timezone {value!r} (use an IANA name like Asia/Kolkata)", 2) from None
    elif key == "default_search":
        value = _norm_name(value)
        if ctx.searches.get(value) is None:
            raise CliError(f"no saved search {value!r}; create it with `logr search save {value} ...`", 2)
    elif key == "luma_near":
        places = [p.strip() for p in value.split(";") if p.strip()]
        if not places:
            raise CliError("luma_near needs at least one place", 2)
        for p in places:
            try:
                resolve_place(p)
            except ValueError as exc:
                raise CliError(str(exc), 2) from None
        value = ";".join(places)
    return value


def cmd_prefs_set(args: argparse.Namespace, ctx: Ctx) -> int:
    value = _validate_pref(args.key, " ".join(args.value).strip(), ctx)
    ctx.prefs.set(args.key, value)
    print(f"{args.key} = {value}")
    return 0


def cmd_prefs_unset(args: argparse.Namespace, ctx: Ctx) -> int:
    print(f"{args.key} unset" if ctx.prefs.unset(args.key) else f"{args.key} was not set")
    return 0


# --- parser -------------------------------------------------------------------


def _positive_int(s: str) -> int:
    n = int(s)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def _add_filter_args(p: argparse.ArgumentParser, src_choices: list[str]) -> None:
    p.add_argument("--source", "-s", action="append", choices=src_choices)
    p.add_argument("--mode", "-m", action="append", choices=[m.value for m in Mode])
    p.add_argument("--within", "-w", type=float, metavar="DAYS", help="only deadlines within N days")
    p.add_argument("--search", "-q", help="text match on title/organizer/location/themes")
    p.add_argument("--all", "-a", action="store_true", help="include closed, past and delisted listings")
    p.add_argument("--theme", action="append", metavar="T", help="match any of these themes (repeatable)")
    p.add_argument("--region", metavar="R", help="city/region in the location, e.g. bengaluru")
    p.add_argument("--min-prize", type=float, metavar="USD", help="prize worth at least this many USD (approx.)")
    p.add_argument("--team-size", type=_positive_int, metavar="N", help="hackathons that allow a team of N")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="logr", description="Find hackathons and track their deadlines.")
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help=f"SQLite file (default {DEFAULT_DB}, or $LOGR_DB)")
    p.add_argument("--tz", default=None,
                   help="IANA timezone for display, e.g. Asia/Kolkata (default $LOGR_TZ, then pref tz, then local)")
    sub = p.add_subparsers(dest="cmd", required=True)
    src_choices = [s.value for s in CONNECTORS]
    status_choices = [s.value for s in Status]

    s = sub.add_parser("sync", help="fetch listings (and detail pages) from sources")
    s.add_argument("--source", "-s", action="append", choices=src_choices)
    s.add_argument("--no-enrich", action="store_true", help="listings only; skip detail-page requests")
    s.add_argument("--enrich-ttl", type=float, default=12, metavar="HOURS",
                   help="re-fetch a detail page after this long (default 12)")
    s.add_argument("--enrich-limit", type=int, default=150, metavar="N",
                   help="max detail requests per source per sync (default 150)")
    s.add_argument("--luma-near", action="append", metavar="PLACE",
                   help="also search Luma near a city (bengaluru, delhi, london...) or 'lat,lng'; "
                        "repeatable; default from $LOGR_LUMA_NEAR (';'-separated), then pref luma_near")
    s.set_defaults(fn=cmd_sync)

    ls = sub.add_parser("list", help="list hackathons by next deadline")
    _add_filter_args(ls, src_choices)
    ls.add_argument("--saved", metavar="NAME", help="start from a saved search; other flags override it")
    ls.add_argument("--no-prefs", action="store_true", help="ignore the default_search preference")
    ls.add_argument("--no-merge", action="store_true", help="don't merge the same hackathon across sources")
    ls.add_argument("--limit", "-n", type=int)
    ls.add_argument("--format", "-f", choices=["table", "json", "csv"], default="table")
    ls.add_argument("--verbose", "-v", action="store_true", help="show uid, url and other listings")
    ls.set_defaults(fn=cmd_list)

    sh = sub.add_parser("show", help="details, timeline, date changes and your tracking for one hackathon")
    sh.add_argument("uid", help="e.g. devfolio:931ca9149c46417bb0526ce1eb44b920")
    sh.set_defaults(fn=cmd_show)

    st = sub.add_parser("status", help="last sync result per source")
    st.set_defaults(fn=cmd_status)

    sv = sub.add_parser("serve", help="run the web dashboard and JSON API")
    sv.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--static", type=Path, metavar="DIR",
                    help="built dashboard to serve at / (default web/out if present)")
    sv.set_defaults(fn=cmd_serve)

    # tracking
    tr = sub.add_parser("track", help="start tracking a hackathon")
    tr.add_argument("uid")
    tr.add_argument("--status", choices=status_choices, help="initial status (default interested)")
    tr.set_defaults(fn=cmd_track)

    ut = sub.add_parser("untrack", help="stop tracking (deletes notes, team, checklist)")
    ut.add_argument("uid")
    ut.set_defaults(fn=cmd_untrack)

    mk = sub.add_parser("mark", help="set your status for a tracked hackathon")
    mk.add_argument("uid")
    mk.add_argument("status", choices=status_choices)
    mk.add_argument("--note", metavar="TEXT", help="why (kept in the status history)")
    mk.set_defaults(fn=cmd_mark)

    mi = sub.add_parser("mine", help="your tracked hackathons by next action")
    mi.add_argument("--status", action="append", choices=status_choices, help="only these statuses (repeatable)")
    mi.add_argument("--all", "-a", action="store_true", help="include won/lost/withdrawn")
    mi.set_defaults(fn=cmd_mine)

    no = sub.add_parser("note", help="append a timestamped note")
    no.add_argument("uid")
    no.add_argument("text", nargs="+")
    no.set_defaults(fn=cmd_note)

    rp = sub.add_parser("repo", help="set (or --clear) the project repo URL")
    rp.add_argument("uid")
    rp.add_argument("url", nargs="?")
    rp.add_argument("--clear", action="store_true")
    rp.set_defaults(fn=cmd_repo)

    tm = sub.add_parser("team", help="add or remove a teammate")
    tm.add_argument("uid")
    tm.add_argument("action", choices=["add", "rm"])
    tm.add_argument("name", nargs="+")
    tm.set_defaults(fn=cmd_team)

    td = sub.add_parser("todo", help="checklist: add, done, undo, rm")
    td.add_argument("uid")
    tds = td.add_subparsers(dest="action", required=True)
    tda = tds.add_parser("add", help="add an item")
    tda.add_argument("text", nargs="+")
    tda.add_argument("--due", metavar="WHEN", help="YYYY-MM-DD (end of day) or 'YYYY-MM-DD HH:MM', display timezone")
    for action, help_ in (("done", "mark done"), ("undo", "mark not done"), ("rm", "delete")):
        x = tds.add_parser(action, help=help_)
        x.add_argument("id", type=int)
    td.set_defaults(fn=cmd_todo)

    # saved searches
    se = sub.add_parser("search", help="saved searches: save, ls, rm")
    ses = se.add_subparsers(dest="search_cmd", required=True)
    sv = ses.add_parser("save", help="save filters under a name (same flags as list)")
    sv.add_argument("name")
    _add_filter_args(sv, src_choices)
    sv.set_defaults(fn=cmd_search_save)
    ses.add_parser("ls", help="list saved searches").set_defaults(fn=cmd_search_ls)
    sr = ses.add_parser("rm", help="delete a saved search")
    sr.add_argument("name")
    sr.set_defaults(fn=cmd_search_rm)

    # preferences
    pr = sub.add_parser("prefs", help="show or change preferences")
    pr.set_defaults(fn=cmd_prefs)
    prs = pr.add_subparsers(dest="prefs_cmd")
    ps = prs.add_parser("set", help="set a preference")
    ps.add_argument("key", choices=list(PREF_KEYS))
    ps.add_argument("value", nargs="+")
    ps.set_defaults(fn=cmd_prefs_set)
    pu = prs.add_parser("unset", help="clear a preference")
    pu.add_argument("key", choices=list(PREF_KEYS))
    pu.set_defaults(fn=cmd_prefs_unset)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = Store(args.db)
    try:
        return args.fn(args, Ctx(args, store))
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
