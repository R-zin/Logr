"""Unified data model every source connector normalizes into."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class Source(StrEnum):
    DEVPOST = "devpost"
    DEVFOLIO = "devfolio"
    UNSTOP = "unstop"
    HACK2SKILL = "hack2skill"
    LUMA = "luma"
    LINKEDIN = "linkedin"  # Phase 6: user-submitted posts, no scraping


class Mode(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    HYBRID = "hybrid"
    UNKNOWN = "unknown"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Hackathon(BaseModel):
    """One hackathon listing, normalized across platforms.

    All datetimes are timezone-aware UTC. A field is None when the source does
    not publish it; we never invent a date. When a value is derived rather than
    published (e.g. Devpost's date-only ranges, or a Luma event's start used as
    its registration cutoff), ``dates_approximate`` is True so the UI can say so.
    """

    source: Source
    source_id: str
    title: str
    url: str
    organizer: str | None = None
    mode: Mode = Mode.UNKNOWN
    location: str | None = None
    themes: list[str] = Field(default_factory=list)
    prize: str | None = None
    participants: int | None = None
    team_size_min: int | None = None
    team_size_max: int | None = None
    image_url: str | None = None

    registration_opens_at: datetime | None = None
    registration_closes_at: datetime | None = None
    submission_deadline: datetime | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    dates_approximate: bool = False

    fetched_at: datetime = Field(default_factory=utcnow)

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.source_id}"

    @field_validator(
        "registration_opens_at",
        "registration_closes_at",
        "submission_deadline",
        "starts_at",
        "ends_at",
        "fetched_at",
    )
    @classmethod
    def _to_utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            raise ValueError("naive datetime; connectors must attach a timezone")
        return v.astimezone(timezone.utc)

    def deadlines(self) -> list[tuple[str, datetime]]:
        """Actionable deadlines, in order. These are what the tracker alerts on."""
        out = [
            ("registration closes", self.registration_closes_at),
            ("submission due", self.submission_deadline),
        ]
        return sorted([(k, v) for k, v in out if v is not None], key=lambda kv: kv[1])

    def next_deadline(self, now: datetime | None = None) -> tuple[str, datetime] | None:
        now = now or utcnow()
        for label, when in self.deadlines():
            if when >= now:
                return label, when
        return None

    def is_open(self, now: datetime | None = None) -> bool:
        """Still possible to act on: a future deadline, or (lacking any) a future end."""
        now = now or utcnow()
        if self.deadlines():
            return self.next_deadline(now) is not None
        end = self.ends_at or self.starts_at
        return end is None or end >= now
