"""Tracking (signed in): /api/tracked..."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response

from logr.tracking import Status, Tracked

from .deps import UserViewDep, View, bad_request, not_found
from .schemas import (
    NoteIn,
    TeamIn,
    TodoIn,
    TodoPatch,
    TrackedOut,
    TrackedPatch,
    TrackIn,
)

router = APIRouter(prefix="/api/tracked")


def _require(view: View, uid: str) -> Tracked:
    t = view.tracker.get(uid)
    if t is None:
        raise not_found(f"not tracking {uid!r}")
    return t


@router.get("", response_model=list[TrackedOut])
def list_tracked(view: UserViewDep, status: Annotated[list[Status] | None, Query()] = None) -> list[TrackedOut]:
    out = [view.tracked_out(t) for t in view.tracker.all(status or None)]
    far = datetime.max.replace(tzinfo=UTC)
    out.sort(key=lambda o: (o.next_action is None, o.next_action.at if o.next_action else far))
    return out


@router.post("", response_model=TrackedOut, status_code=201)
def track(body: TrackIn, view: UserViewDep, response: Response) -> TrackedOut:
    if view.store.get(body.uid) is None:
        raise not_found(f"no hackathon {body.uid!r}")
    existing = view.tracker.get(body.uid)
    if existing is not None:
        response.status_code = 200
        return view.tracked_out(existing)
    return view.tracked_out(view.tracker.track(body.uid, body.status or Status.INTERESTED))


@router.patch("/{uid}", response_model=TrackedOut)
def update_tracked(uid: str, body: TrackedPatch, view: UserViewDep) -> TrackedOut:
    t = _require(view, uid)
    if body.status is not None:
        t = view.tracker.set_status(uid, body.status, (body.status_note or "").strip() or None)
    if "repo_url" in body.model_fields_set:
        t = view.tracker.set_repo(uid, body.repo_url)
    return view.tracked_out(t)


@router.delete("/{uid}", status_code=204)
def untrack(uid: str, view: UserViewDep) -> Response:
    if not view.tracker.untrack(uid):
        raise not_found(f"not tracking {uid!r}")
    return Response(status_code=204)


@router.post("/{uid}/notes", response_model=TrackedOut)
def add_note(uid: str, body: NoteIn, view: UserViewDep) -> TrackedOut:
    _require(view, uid)
    if not body.text.strip():
        raise bad_request("note text is empty")
    return view.tracked_out(view.tracker.append_note(uid, body.text))


@router.post("/{uid}/team", response_model=TrackedOut)
def add_member(uid: str, body: TeamIn, view: UserViewDep) -> TrackedOut:
    _require(view, uid)
    try:
        return view.tracked_out(view.tracker.add_member(uid, body.name))
    except ValueError as exc:
        raise bad_request(str(exc)) from None


@router.delete("/{uid}/team/{name}", response_model=TrackedOut)
def remove_member(uid: str, name: str, view: UserViewDep) -> TrackedOut:
    _require(view, uid)
    try:
        return view.tracked_out(view.tracker.remove_member(uid, name))
    except ValueError as exc:
        raise not_found(str(exc)) from None


@router.post("/{uid}/todos", response_model=TrackedOut, status_code=201)
def add_todo(uid: str, body: TodoIn, view: UserViewDep) -> TrackedOut:
    _require(view, uid)
    try:
        view.tracker.add_todo(uid, body.text, body.due_at)
    except ValueError as exc:
        raise bad_request(str(exc)) from None
    return view.tracked_out(_require(view, uid))


@router.patch("/{uid}/todos/{item_id}", response_model=TrackedOut)
def set_todo(uid: str, item_id: int, body: TodoPatch, view: UserViewDep) -> TrackedOut:
    _require(view, uid)
    try:
        return view.tracked_out(view.tracker.set_todo_done(uid, item_id, body.done))
    except KeyError:
        raise not_found(f"no todo #{item_id} on {uid}") from None


@router.delete("/{uid}/todos/{item_id}", response_model=TrackedOut)
def remove_todo(uid: str, item_id: int, view: UserViewDep) -> TrackedOut:
    _require(view, uid)
    try:
        return view.tracked_out(view.tracker.remove_todo(uid, item_id))
    except KeyError:
        raise not_found(f"no todo #{item_id} on {uid}") from None
