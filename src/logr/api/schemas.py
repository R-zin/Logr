"""Pydantic models mirroring docs/API.md exactly (responses and request bodies)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, PlainSerializer

from logr.tracking import Status


def iso_z(dt: datetime) -> str:
    """ISO-8601 UTC with a ``Z`` suffix (naive values are taken as UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


UTCDateTime = Annotated[datetime, PlainSerializer(iso_z, return_type=str, when_used="json")]


# --- hackathons ---------------------------------------------------------------


class DeadlineOut(BaseModel):
    label: str
    at: UTCDateTime
    approximate: bool


class AlsoListed(BaseModel):
    uid: str
    source: str
    url: str


class ListingOut(BaseModel):
    uid: str
    title: str
    url: str
    organizer: str | None
    mode: Literal["online", "offline", "hybrid", "unknown"]
    location: str | None
    themes: list[str]
    prize: str | None
    prize_usd: float | None
    participants: int | None
    team_size_min: int | None
    team_size_max: int | None
    image_url: str | None
    source: str
    sources: list[str]
    also_listed: list[AlsoListed]
    registration_opens_at: UTCDateTime | None
    registration_closes_at: UTCDateTime | None
    submission_deadline: UTCDateTime | None
    starts_at: UTCDateTime | None
    ends_at: UTCDateTime | None
    approx_fields: list[str]
    next_deadline: DeadlineOut | None
    tracked_status: Status | None


class HackathonList(BaseModel):
    items: list[ListingOut]
    total: int
    filter: dict[str, Any]
    saved: str | None
    now: UTCDateTime


class RoundOut(BaseModel):
    name: str
    kind: str
    starts_at: UTCDateTime | None
    ends_at: UTCDateTime | None


class DateChangeOut(BaseModel):
    field: str
    old_value: str | None
    new_value: str | None
    changed_at: UTCDateTime


# --- tracking -------------------------------------------------------------------


class ChecklistItemOut(BaseModel):
    id: int
    text: str
    done: bool
    due_at: UTCDateTime | None


class StatusChangeOut(BaseModel):
    old: Status | None
    new: Status
    changed_at: UTCDateTime
    note: str | None


class TrackedOut(BaseModel):
    uid: str
    status: Status
    added_at: UTCDateTime
    updated_at: UTCDateTime
    notes: str
    repo_url: str | None
    team: list[str]
    checklist: list[ChecklistItemOut]
    next_action: DeadlineOut | None
    history: list[StatusChangeOut]
    hackathon: ListingOut | None


class HackathonDetail(ListingOut):
    rounds: list[RoundOut]
    date_changes: list[DateChangeOut]
    first_seen_at: UTCDateTime | None
    last_seen_at: UTCDateTime | None
    delisted_at: UTCDateTime | None
    tracking: TrackedOut | None


class TrackIn(BaseModel):
    uid: str
    status: Status | None = None


class TrackedPatch(BaseModel):
    status: Status | None = None
    status_note: str | None = None
    repo_url: str | None = None


class NoteIn(BaseModel):
    text: str


class TeamIn(BaseModel):
    name: str


class TodoIn(BaseModel):
    text: str
    due_at: datetime | None = None


class TodoPatch(BaseModel):
    done: bool


# --- calendar -------------------------------------------------------------------


class CalendarEvent(BaseModel):
    uid: str
    title: str
    label: str
    at: UTCDateTime
    approximate: bool
    kind: Literal["deadline", "round_end", "start", "end", "todo"]
    tracked_status: Status | None


class CalendarOut(BaseModel):
    events: list[CalendarEvent]


# --- sync -----------------------------------------------------------------------


class SyncRunOut(BaseModel):
    source: str
    started_at: UTCDateTime
    finished_at: UTCDateTime | None
    fetched: int
    created: int
    updated: int
    enriched: int
    enrich_errors: int
    delisted: int
    error: str | None


class SyncStatusOut(BaseModel):
    runs: list[SyncRunOut]
    running: bool


class SyncStarted(BaseModel):
    started: bool


# --- searches & prefs -----------------------------------------------------------


class SearchOut(BaseModel):
    name: str
    filter: dict[str, Any]
    description: str
    is_default: bool


class SearchIn(BaseModel):
    filter: dict[str, Any]


class PrefOut(BaseModel):
    value: str | None
    description: str


class PrefIn(BaseModel):
    value: str


class PrefSet(BaseModel):
    key: str
    value: str
    description: str


# --- notifications and calendar feed (Phase 5) ------------------------------------


class FeedOut(BaseModel):
    url: str
    webcal_url: str


class ChannelOut(BaseModel):
    configured: bool
    available: bool
    destination: str | None


class NotifyOut(BaseModel):
    channels: dict[Literal["email", "telegram", "discord"], ChannelOut]
    remind_offsets: str
    last_pass_at: UTCDateTime | None


class NotifyTestOut(BaseModel):
    results: dict[str, str]
