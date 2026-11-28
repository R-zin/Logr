"""Public browsing: GET /api/hackathons, /api/hackathons/{uid}, /api/calendar."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query

from logr.cli import select
from logr.models import Hackathon, Mode, Source
from logr.searches import Filter, prize_usd
from logr.tracking import upcoming

from .deps import ViewDep, bad_request, dt_or_none, not_found
from .schemas import (
    CalendarEvent,
    CalendarOut,
    DateChangeOut,
    HackathonDetail,
    HackathonList,
    RoundOut,
)

router = APIRouter(prefix="/api")

MAX_CALENDAR_DAYS = 100
DEADLINE_LABELS = ("registration closes", "submission due")


def _norm_name(name: str) -> str:
    return name.strip().lower()


@router.get("/hackathons", response_model=HackathonList)
def list_hackathons(
    view: ViewDep,
    source: Annotated[list[Source] | None, Query()] = None,
    mode: Annotated[list[Mode] | None, Query()] = None,
    theme: Annotated[list[str] | None, Query()] = None,
    within: float | None = None,
    q: str | None = None,
    region: str | None = None,
    min_prize: float | None = None,
    team_size: Annotated[int | None, Query(ge=1)] = None,
    include_closed: bool | None = None,
    merge: bool = True,
    saved: str | None = None,
    no_prefs: bool = False,
    sort: Literal["deadline", "prize"] = "deadline",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HackathonList:
    params = Filter(
        sources=list(source) if source else None,
        modes=list(mode) if mode else None,
        within_days=within,
        search=q or None,
        include_closed=bool(include_closed),
        themes=[t for t in theme if t.strip()] or None if theme else None,
        region=region or None,
        min_prize_usd=min_prize,
        team_size=team_size,
    )
    has_filter_params = any(v is not None for v in (
        source, mode, theme, within, q, region, min_prize, team_size, include_closed))

    # Saved-search precedence (same as `logr list`): ?saved=, else the
    # default_search pref when the request carries no filter params at all.
    saved_name: str | None = None
    f = params
    if saved is not None:
        if view.user is None:
            raise HTTPException(status_code=401, detail="login required")
        saved_name = _norm_name(saved)
        base = view.searches.get(saved_name)
        if base is None:
            raise not_found(f"no saved search {saved_name!r}")
        f = base.merged(params)
    elif view.user is not None and not no_prefs and not has_filter_params:
        default = view.prefs.get("default_search")
        base = view.searches.get(_norm_name(default)) if default else None
        if base is not None:  # a dangling default_search is ignored
            saved_name = _norm_name(default)
            f = base.merged(params)

    rows = select(view.store.all(f.sources, include_delisted=f.include_closed), now=view.now, merge=merge, filter=f)
    if sort == "prize":
        # descending USD value, unknown last; ties keep deadline order (stable sort)
        rows.sort(key=lambda lst: (prize_usd(lst.primary.prize) is None, -(prize_usd(lst.primary.prize) or 0.0)))
    page = rows[offset: offset + limit]
    return HackathonList(
        items=[view.listing_out(lst.primary, lst.others) for lst in page],
        total=len(rows),
        filter=f.to_dict(),
        saved=saved_name,
        now=view.now,
    )


@router.get("/hackathons/{uid}", response_model=HackathonDetail)
def get_hackathon(uid: str, view: ViewDep) -> HackathonDetail:
    h = view.store.get(uid)
    if h is None:
        raise not_found(f"no hackathon {uid!r}")
    base = view.listing_out(h, view.others_of(h))
    meta = view.store.meta(uid)
    tracking = None
    if view.user is not None:
        lst = view.group_of(uid)
        members = [h, *(x for x in (lst.all if lst else []) if x.uid != uid)]
        t = view.tracked_of(members)  # this uid first, then the rest of its group
        tracking = view.tracked_out(t) if t else None
    return HackathonDetail(
        **dict(base),
        rounds=[RoundOut(name=r.name, kind=r.kind.value, starts_at=r.starts_at, ends_at=r.ends_at) for r in h.rounds],
        date_changes=[DateChangeOut(field=r["field"], old_value=r["old_value"], new_value=r["new_value"],
                                    changed_at=dt_or_none(r["changed_at"])) for r in view.store.history(uid)],
        first_seen_at=dt_or_none(meta.first_seen_at) if meta else None,
        last_seen_at=dt_or_none(meta.last_seen_at) if meta else None,
        delisted_at=dt_or_none(meta.delisted_at) if meta else None,
        tracking=tracking,
    )


# --- calendar -----------------------------------------------------------------


def _parse_when(name: str, value: str | None) -> datetime:
    if not value:
        raise bad_request(f"'{name}' is required (ISO-8601, e.g. 2026-12-01T00:00:00Z)")
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        raise bad_request(f"bad '{name}' {value!r}; use ISO-8601 like 2026-12-01T00:00:00Z") from None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC)


def _deadline_kind(label: str) -> str:
    if label.startswith("todo: "):
        return "todo"
    return "deadline" if label in DEADLINE_LABELS else "round_end"


@router.get("/calendar", response_model=CalendarOut)
def calendar(
    view: ViewDep,
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
    scope: str | None = None,
) -> CalendarOut:
    start, end = _parse_when("from", from_), _parse_when("to", to)
    if start >= end:
        raise bad_request("'from' must be before 'to'")
    if end - start > timedelta(days=MAX_CALENDAR_DAYS):
        raise bad_request(f"the range can be at most {MAX_CALENDAR_DAYS} days")
    if scope is None:
        scope = "tracked" if view.user is not None else "all"
    if scope not in ("tracked", "all"):
        raise bad_request("scope must be 'tracked' or 'all'")
    if scope == "tracked" and view.user is None:
        raise HTTPException(status_code=401, detail="login required")

    events: list[CalendarEvent] = []

    def add(uid: str, title: str, label: str, at: datetime | None, approx: bool, kind: str, status) -> None:
        if at is not None and start <= at < end:
            events.append(CalendarEvent(uid=uid, title=title, label=label, at=at, approximate=approx,
                                        kind=kind, tracked_status=status))

    if scope == "tracked":
        for t in view.tracker.all():
            own = view.store.get(t.uid)
            h = view.best(t.uid) if own else None
            title = own.title if own else t.uid
            # status-aware, as of the start of the range (todos included)
            for d in upcoming(h, t, start):
                add(t.uid, title, d.label, d.at, d.approximate, _deadline_kind(d.label), t.status)
    else:
        for lst in select(view.store.all(), now=start, include_closed=False):
            h: Hackathon = lst.primary
            t = view.tracked_of(lst.all)
            status = t.status if t else None
            for d in h.deadlines():
                add(h.uid, h.title, d.label, d.at, d.approximate, _deadline_kind(d.label), status)
            add(h.uid, h.title, "starts", h.starts_at, h.is_approx("starts_at"), "start", status)
            add(h.uid, h.title, "ends", h.ends_at, h.is_approx("ends_at"), "end", status)
    events.sort(key=lambda e: e.at)
    return CalendarOut(events=events)
