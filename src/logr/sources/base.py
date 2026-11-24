from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from logr.http import Http
from logr.models import Hackathon, Source


class Connector(ABC):
    """A source connector fetches listings and normalizes them.

    ``fetch`` handles pagination/network; ``parse`` is a pure function over one
    raw payload so it can be unit-tested against recorded fixtures.
    """

    source: Source

    def __init__(self, http: Http, *, max_pages: int = 30) -> None:
        self.http = http
        self.max_pages = max_pages

    @abstractmethod
    def fetch(self) -> Iterator[Hackathon]: ...


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
