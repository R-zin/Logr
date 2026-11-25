from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from logr.http import Http
from logr.models import Hackathon, Round, Source


class Connector(ABC):
    """A source connector fetches listings and, optionally, per-listing detail.

    ``fetch`` handles pagination/network; ``parse`` is a pure function over one
    raw payload so it can be unit-tested against recorded fixtures.

    Connectors that can do better than the listing implement ``enrich``: one
    request for one hackathon, returning a *patch* (a dict of Hackathon fields,
    JSON-serializable) that is cached and overlaid on later listings.
    """

    source: Source
    can_enrich: bool = False

    def __init__(self, http: Http, *, max_pages: int = 30, **_: Any) -> None:
        self.http = http
        self.max_pages = max_pages

    @abstractmethod
    def fetch(self) -> Iterator[Hackathon]: ...

    def enrich(self, h: Hackathon) -> dict[str, Any] | None:
        return None


def parse_dt(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp (with offset or Z). Returns None if absent/bad."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else None


def clean(s: Any) -> str | None:
    if not isinstance(s, str):
        return None
    s = " ".join(s.split())
    return s or None


def rounds_json(rounds: list[Round]) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json") for r in rounds]


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None
