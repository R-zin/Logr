"""Unified data model every source connector normalizes into."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, NamedTuple

from pydantic import BaseModel, Field, field_validator, model_validator

DATE_FIELDS = (
    "registration_opens_at",
    "registration_closes_at",
    "submission_deadline",
    "starts_at",
    "ends_at",
)


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


class RoundKind(StrEnum):
    REGISTRATION = "registration"
    SUBMISSION = "submission"  # anything you must hand in or attempt: ideas, PPTs, prototypes, quizzes
    JUDGING = "judging"
    RESULTS = "results"
    EVENT = "event"  # the hacking itself / finale / demo day
    OTHER = "other"


# Order matters: first match wins. Explicit hand-in words beat event words
# ("Prototype Submission – Finale" is a submission); event words beat the
# generic "round/phase/stage" ("On-Ground Final Round" is the event).
_ROUND_RULES: list[tuple[RoundKind, re.Pattern[str]]] = [
    (RoundKind.RESULTS, re.compile(r"winner|result|announce", re.I)),
    (RoundKind.JUDGING, re.compile(r"judg|evaluat|review|voting|jury", re.I)),
    (RoundKind.REGISTRATION, re.compile(r"regist|sign.?up|team formation", re.I)),
    (RoundKind.SUBMISSION, re.compile(
        r"submi|idea|ppt|proposal|prototype|screening|quiz|assessment|test|mvp|abstract", re.I)),
    (RoundKind.EVENT, re.compile(
        r"final|on.?ground|offline|on.?site|in.?person|demo|bootcamp|hack|sprint|build", re.I)),
    (RoundKind.SUBMISSION, re.compile(r"round|phase|stage", re.I)),
]


def classify_round(name: str) -> RoundKind:
    for kind, pat in _ROUND_RULES:
        if pat.search(name):
            return kind
    return RoundKind.OTHER


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utc(v: datetime | None) -> datetime | None:
    if v is None:
        return None
    if v.tzinfo is None:
        raise ValueError("naive datetime; connectors must attach a timezone")
    return v.astimezone(timezone.utc)


class Round(BaseModel):
    """One stage of a hackathon's timeline (Unstop rounds, Devpost periods).

    ``kind`` is always derived from the name, so improving the classifier
    re-labels rounds already stored in the database and enrichment cache."""

    name: str
    kind: RoundKind = RoundKind.OTHER
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    @model_validator(mode="after")
    def _derive_kind(self) -> Round:
        self.kind = classify_round(self.name)
        return self

    @field_validator("starts_at", "ends_at")
    @classmethod
    def _to_utc(cls, v: datetime | None) -> datetime | None:
        return _utc(v)


class Deadline(NamedTuple):
    label: str
    at: datetime
    approximate: bool


class Hackathon(BaseModel):
    """One hackathon listing, normalized across platforms.

    All datetimes are timezone-aware UTC. A field is None when the source does
    not publish it; we never invent a date. A date that is derived rather than
    published (Devpost's date-only ranges before enrichment, a Luma event's start
    used as its registration cutoff) is listed in ``approx_fields``.
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
    approx_fields: list[str] = Field(default_factory=list)
    rounds: list[Round] = Field(default_factory=list)

    fetched_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="before")
    @classmethod
    def _migrate_v1(cls, data: Any) -> Any:
        # Phase 1 stored a single ``dates_approximate`` flag for the whole record.
        if isinstance(data, dict) and "dates_approximate" in data:
            data = dict(data)
            if data.pop("dates_approximate") and "approx_fields" not in data:
                data["approx_fields"] = [f for f in DATE_FIELDS if data.get(f)]
        return data

    @field_validator(*DATE_FIELDS, "fetched_at")
    @classmethod
    def _to_utc(cls, v: datetime | None) -> datetime | None:
        return _utc(v)

    @field_validator("approx_fields")
    @classmethod
    def _known_fields(cls, v: list[str]) -> list[str]:
        bad = set(v) - set(DATE_FIELDS)
        if bad:
            raise ValueError(f"unknown date fields: {sorted(bad)}")
        return sorted(set(v), key=DATE_FIELDS.index)

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.source_id}"

    @property
    def dates_approximate(self) -> bool:
        return bool(self.approx_fields)

    def is_approx(self, field: str) -> bool:
        return field in self.approx_fields

    def deadlines(self) -> list[Deadline]:
        """Actionable deadlines in time order: registration close, every
        submission-type round end, and the final submission deadline."""
        out: list[Deadline] = []
        if self.registration_closes_at:
            out.append(Deadline("registration closes", self.registration_closes_at,
                                self.is_approx("registration_closes_at")))
        if self.submission_deadline:
            out.append(Deadline("submission due", self.submission_deadline,
                                self.is_approx("submission_deadline")))
        for r in self.rounds:
            if r.kind is RoundKind.SUBMISSION and r.ends_at:
                out.append(Deadline(f"{r.name} ends", r.ends_at, False))
        out.sort(key=lambda d: d.at)  # stable: on a tie the generic label above wins
        deduped: list[Deadline] = []
        for d in out:
            if deduped and deduped[-1].at == d.at:
                continue
            deduped.append(d)
        return deduped

    def next_deadline(self, now: datetime | None = None) -> Deadline | None:
        now = now or utcnow()
        for d in self.deadlines():
            if d.at >= now:
                return d
        return None

    def is_open(self, now: datetime | None = None) -> bool:
        """Still possible to act on: a future deadline, or (lacking any) a future end."""
        now = now or utcnow()
        if self.deadlines():
            return self.next_deadline(now) is not None
        end = self.ends_at or self.starts_at
        return end is None or end >= now

    def with_patch(self, patch: dict[str, Any] | None) -> Hackathon:
        """Overlay enrichment data. Dates supplied by an enricher come from the
        platform's exact timeline, so they stop being approximate."""
        if not patch:
            return self
        data = self.model_dump(mode="json")
        data.update(patch)
        exact = {f for f in DATE_FIELDS if f in patch and patch[f] is not None}
        data["approx_fields"] = [f for f in data.get("approx_fields", []) if f not in exact]
        return Hackathon.model_validate(data)
