"""Filters, saved searches and preferences.

CONTRACT (Phase 3). Signatures and semantics here are fixed; other modules
(cli.py) are written against them.

Storage lives in the same SQLite file as the listings (pass ``Store.db``), with a
``user_id`` column (always ``"local"`` for now) so Phase 4 can add accounts.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from logr.models import Hackathon, Mode, Source

LOCAL_USER = "local"

# Approximate conversion to USD for the --min-prize filter. Deliberately static
# (no network); good enough to compare "₹5 lakh" with "$5,000". Keys are ISO codes.
RATES_TO_USD: dict[str, float] = {
    "USD": 1.0, "INR": 0.012, "EUR": 1.08, "GBP": 1.27, "CAD": 0.73, "AUD": 0.66,
    "SGD": 0.74, "AED": 0.27, "JPY": 0.0067,
}

# Spelling variants of the same city, so --region bengaluru matches "Bangalore, Karnataka".
CITY_ALIASES: list[set[str]] = [
    {"bengaluru", "bangalore"}, {"mumbai", "bombay"}, {"chennai", "madras"},
    {"kolkata", "calcutta"}, {"gurugram", "gurgaon"}, {"thiruvananthapuram", "trivandrum"},
    {"kochi", "cochin"}, {"mysuru", "mysore"}, {"puducherry", "pondicherry"},
    {"varanasi", "banaras", "benares"}, {"vadodara", "baroda"}, {"pune", "poona"},
    {"new delhi", "delhi"},
]


def parse_prize(text: str | None) -> list[tuple[str, float]]:
    """Extract (ISO currency, amount) pairs from a prize string, in order.

    Must handle what the connectors produce: "$100,000", "₹500,000",
    "₹51,000 + $1,000", "€2,500", "£1,000", "INR 50,000", "50,000 INR",
    "$10K", "$1.5M", "₹5 lakh", "₹1.2 crore", "Rs. 25,000". "$" means USD.
    Text with no amount (Devfolio's "Prize Pool, Swags") → [].
    """
    raise NotImplementedError


def prize_usd(text: str | None) -> float | None:
    """Sum of ``parse_prize`` converted with RATES_TO_USD. None if nothing parsed
    or a currency has no rate."""
    raise NotImplementedError


def region_matches(region: str, location: str | None) -> bool:
    """Case-insensitive substring match of ``region`` in ``location``, also trying
    every alias from CITY_ALIASES. ``location`` None → False."""
    raise NotImplementedError


@dataclass
class Filter:
    """Every criterion is optional; unset criteria match everything.

    Semantics (all must hold):
    - include_closed False → ``h.is_open(now)`` must be True
    - within_days → ``h.next_deadline(now)`` exists and is ``<= now + within_days``
    - sources / modes → membership
    - search → case-insensitive substring of title + organizer + location + themes
    - themes → ANY of them is a case-insensitive substring of ANY of ``h.themes``
    - region → ``region_matches(region, h.location)`` (online listings have no
      location, so they don't match a region)
    - min_prize_usd → ``prize_usd(h.prize)`` is not None and >= value
    - team_size → fits ``[h.team_size_min or 1, h.team_size_max or ∞]``; unknown
      bounds don't exclude
    """

    sources: list[Source] | None = None
    modes: list[Mode] | None = None
    within_days: float | None = None
    search: str | None = None
    include_closed: bool = False
    themes: list[str] | None = None
    region: str | None = None
    min_prize_usd: float | None = None
    team_size: int | None = None

    def matches(self, h: Hackathon, now: datetime) -> bool:
        raise NotImplementedError

    def merged(self, override: Filter) -> Filter:
        """A copy where every criterion set in ``override`` (not None, and
        include_closed only if True) replaces this one's."""
        raise NotImplementedError

    def is_empty(self) -> bool:
        """True when no criterion is set (include_closed False counts as unset)."""
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe; omits unset criteria; enums as their values."""
        raise NotImplementedError

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Filter:
        """Inverse of to_dict. Unknown keys raise ValueError."""
        raise NotImplementedError

    def describe(self) -> str:
        """One line for humans, e.g. "online · themes AI, Web3 · ≥ $1,000 · within 14d".
        "(everything)" when empty."""
        raise NotImplementedError


class SavedSearches:
    def __init__(self, db: sqlite3.Connection) -> None:
        """Create table if missing."""
        raise NotImplementedError

    def save(self, name: str, f: Filter) -> None:
        """Insert or replace. Name is stripped and lower-cased; must match
        ``[a-z0-9][a-z0-9_-]{0,39}`` else ValueError."""
        raise NotImplementedError

    def get(self, name: str) -> Filter | None:
        raise NotImplementedError

    def all(self) -> list[tuple[str, Filter]]:
        """Sorted by name."""
        raise NotImplementedError

    def delete(self, name: str) -> bool:
        raise NotImplementedError


# Preference keys the CLI understands. Values are strings.
PREF_KEYS: dict[str, str] = {
    "tz": "IANA timezone for displayed dates (used when --tz and $LOGR_TZ are absent)",
    "luma_near": "';'-separated Luma places for sync (used when --luma-near and $LOGR_LUMA_NEAR are absent)",
    "default_search": "saved search applied by `logr list` unless --saved or --no-prefs is given",
}


class Preferences:
    def __init__(self, db: sqlite3.Connection) -> None:
        """Create table if missing."""
        raise NotImplementedError

    def get(self, key: str) -> str | None:
        raise NotImplementedError

    def set(self, key: str, value: str) -> None:
        """Unknown key (not in PREF_KEYS) raises KeyError."""
        raise NotImplementedError

    def unset(self, key: str) -> bool:
        raise NotImplementedError

    def all(self) -> dict[str, str]:
        raise NotImplementedError
