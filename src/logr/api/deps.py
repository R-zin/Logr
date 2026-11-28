"""Per-request dependencies and the helpers that turn store records into API shapes."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from functools import cached_property
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from logr import auth
from logr.auth import User
from logr.dedupe import Listing, group
from logr.models import Deadline, Hackathon, utcnow
from logr.searches import Preferences, SavedSearches, prize_usd
from logr.store import Store
from logr.tracking import Tracked, Tracker, next_action

from .schemas import (
    AlsoListed,
    ChecklistItemOut,
    DeadlineOut,
    ListingOut,
    StatusChangeOut,
    TrackedOut,
)

SAFE_METHODS = frozenset({"GET", "HEAD"})


def csrf(request: Request) -> None:
    """App-wide dependency: every non-GET request needs ``X-Requested-With: logr``."""
    if request.method not in SAFE_METHODS:
        auth.require_csrf(request)


def get_store(request: Request) -> Iterator[Store]:
    store = Store(request.app.state.db_path)
    try:
        yield store
    finally:
        store.close()


StoreDep = Annotated[Store, Depends(get_store)]
MaybeUser = Annotated[User | None, Depends(auth.current_user)]
UserDep = Annotated[User, Depends(auth.require_user)]
AdminDep = Annotated[User, Depends(auth.require_admin)]


def not_found(msg: str) -> HTTPException:
    return HTTPException(status_code=404, detail=msg)


def bad_request(msg: str) -> HTTPException:
    return HTTPException(status_code=400, detail=msg)


def deadline_out(d: Deadline | None) -> DeadlineOut | None:
    return DeadlineOut(label=d.label, at=d.at, approximate=d.approximate) if d else None


def dt_or_none(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


class View:
    """Everything one request needs: the store, the (optional) user and lazily
    built per-user stores and cross-source groups — the API's ``cli.Ctx``."""

    def __init__(self, store: Store, user: User | None, now: datetime | None = None) -> None:
        self.store = store
        self.user = user
        self.now = now or utcnow()

    # --- per-user stores (only when signed in) ---

    @cached_property
    def tracker(self) -> Tracker:
        assert self.user is not None
        return Tracker(self.store.db, self.user.id)

    @cached_property
    def searches(self) -> SavedSearches:
        assert self.user is not None
        return SavedSearches(self.store.db, self.user.id)

    @cached_property
    def prefs(self) -> Preferences:
        assert self.user is not None
        return Preferences(self.store.db, self.user.id)

    @cached_property
    def tracked_map(self) -> dict[str, Tracked]:
        return {t.uid: t for t in self.tracker.all()} if self.user is not None else {}

    def tracked_of(self, hs: list[Hackathon]) -> Tracked | None:
        tm = self.tracked_map
        return next((tm[h.uid] for h in hs if h.uid in tm), None)

    # --- cross-source groups ---

    @cached_property
    def _group_by_uid(self) -> dict[str, Listing]:
        out: dict[str, Listing] = {}
        for lst in group(self.store.all(include_delisted=True)):
            for h in lst.all:
                out[h.uid] = lst
        return out

    def group_of(self, uid: str) -> Listing | None:
        return self._group_by_uid.get(uid)

    def best(self, uid: str) -> Hackathon | None:
        """Record to take deadlines from: the merged primary (see ``cli.Ctx.best``)."""
        lst = self.group_of(uid)
        return lst.primary if lst else self.store.get(uid)

    # --- shapes ---

    def listing_out(self, h: Hackathon, others: list[Hackathon]) -> ListingOut:
        t = self.tracked_of([h, *others])
        return ListingOut(
            uid=h.uid, title=h.title, url=h.url, organizer=h.organizer, mode=h.mode.value,
            location=h.location, themes=list(h.themes), prize=h.prize, prize_usd=prize_usd(h.prize),
            participants=h.participants, team_size_min=h.team_size_min, team_size_max=h.team_size_max,
            image_url=h.image_url, source=h.source.value, sources=[x.source.value for x in (h, *others)],
            also_listed=[AlsoListed(uid=o.uid, source=o.source.value, url=o.url) for o in others],
            registration_opens_at=h.registration_opens_at, registration_closes_at=h.registration_closes_at,
            submission_deadline=h.submission_deadline, starts_at=h.starts_at, ends_at=h.ends_at,
            approx_fields=list(h.approx_fields), next_deadline=deadline_out(h.next_deadline(self.now)),
            tracked_status=t.status if t else None,
        )

    def others_of(self, h: Hackathon) -> list[Hackathon]:
        lst = self.group_of(h.uid)
        return [x for x in lst.all if x.uid != h.uid] if lst else []

    def listing_for(self, uid: str) -> ListingOut | None:
        """The uid's own record, with the rest of its merged group as ``also_listed``."""
        h = self.store.get(uid)
        return self.listing_out(h, self.others_of(h)) if h else None

    def next_action_of(self, t: Tracked) -> Deadline | None:
        h = self.store.get(t.uid)
        return next_action(self.best(t.uid) if h else None, t, self.now)

    def tracked_out(self, t: Tracked) -> TrackedOut:
        self.__dict__.pop("tracked_map", None)  # may have changed during this request
        return TrackedOut(
            uid=t.uid, status=t.status, added_at=t.added_at, updated_at=t.updated_at, notes=t.notes,
            repo_url=t.repo_url, team=list(t.team),
            checklist=[ChecklistItemOut(id=i.id, text=i.text, done=i.done, due_at=i.due_at) for i in t.checklist],
            next_action=deadline_out(self.next_action_of(t)),
            history=[StatusChangeOut(old=c.old, new=c.new, changed_at=c.changed_at, note=c.note)
                     for c in self.tracker.history(t.uid)],
            hackathon=self.listing_for(t.uid),
        )


def get_view(store: StoreDep, user: MaybeUser) -> View:
    return View(store, user)


def get_user_view(store: StoreDep, user: UserDep) -> View:
    return View(store, user)


ViewDep = Annotated[View, Depends(get_view)]
UserViewDep = Annotated[View, Depends(get_user_view)]
