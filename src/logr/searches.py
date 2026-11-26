"""Filters, saved searches and preferences.

CONTRACT (Phase 3). Signatures and semantics here are fixed; other modules
(cli.py) are written against them.

Storage lives in the same SQLite file as the listings (pass ``Store.db``), with a
``user_id`` column (always ``"local"`` for now) so Phase 4 can add accounts.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta, timezone
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


# --- prize parsing ----------------------------------------------------------

# Currency markers that may stand before or after an amount. Symbols map to ISO
# codes; bare ISO codes are recognised for the rated currencies plus a set of
# other common ones (those parse, but ``prize_usd`` returns None for them).
# Codes are matched upper-case only, so ordinary words ("try", "top") never count.
_SYMBOLS: dict[str, str] = {
    "US$": "USD", "S$": "SGD", "A$": "AUD", "C$": "CAD",
    "$": "USD", "₹": "INR", "€": "EUR", "£": "GBP", "¥": "JPY",
}
_EXTRA_CODES = (
    "CHF", "CNY", "HKD", "NZD", "SEK", "NOK", "DKK", "ZAR", "BRL", "MXN", "KRW",
    "IDR", "MYR", "THB", "VND", "NGN", "KES", "PKR", "BDT", "LKR", "NPR", "SAR",
    "QAR", "EGP", "PLN", "ILS", "TWD", "GHS", "UAH", "CZK", "HUF", "RON",
)
_CODES = tuple(dict.fromkeys([*RATES_TO_USD, *_EXTRA_CODES]))

_CUR = (
    r"(?P<{g}>"
    + "|".join(
        # Letter-bearing symbols must not be the tail of a longer word ("BUS$").
        (r"(?<![A-Za-z])" if s[0].isalpha() else "") + re.escape(s)
        for s in sorted(_SYMBOLS, key=len, reverse=True)
    )
    + r"|(?<![A-Za-z])[Rr][Ss]\.?(?![A-Za-z])"
    + r"|(?<![A-Za-z])(?:" + "|".join(_CODES) + r")(?![A-Za-z])"
    + r")"
)
# 1,00,000 / 100,000 / 1.5 / 2500 — commas are grouping separators.
_NUM = r"(?P<{g}>\d+(?:,\d+)*(?:\.\d+)?)"
# Word multipliers may be separated by a space; single letters must be attached.
_MULT = (
    r"(?P<{g}>\s*(?i:thousand|million|billion|lakhs?|lacs?|crores?|mn|bn|cr)(?![A-Za-z])"
    r"|[kKmMbB](?![A-Za-z]))?"
)
_PRIZE_RE = re.compile(
    # "$10K", "₹ 5 lakh", "Rs. 25,000", "INR 50,000"
    _CUR.format(g="pcur") + r"\s*" + _NUM.format(g="pnum") + _MULT.format(g="pmult")
    # "50,000 INR", "2,500€". The marker must not itself lead another amount, so
    # in "Top 3 $500" the 3 is not taken as dollars.
    + r"|(?<![\d.,])" + _NUM.format(g="snum") + _MULT.format(g="smult") + r"\s*"
    + _CUR.format(g="scur") + r"(?!\s*\d)",
)
_MULTIPLIERS: dict[str, float] = {
    "k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
    "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
    "crore": 1e7, "crores": 1e7, "cr": 1e7,
}


def _currency_code(marker: str) -> str:
    if marker in _SYMBOLS:
        return _SYMBOLS[marker]
    if marker.rstrip(".").lower() == "rs":
        return "INR"
    return marker


def parse_prize(text: str | None) -> list[tuple[str, float]]:
    """Extract (ISO currency, amount) pairs from a prize string, in order.

    Must handle what the connectors produce: "$100,000", "₹500,000",
    "₹51,000 + $1,000", "€2,500", "£1,000", "INR 50,000", "50,000 INR",
    "$10K", "$1.5M", "₹5 lakh", "₹1.2 crore", "Rs. 25,000". "$" means USD.
    Text with no amount (Devfolio's "Prize Pool, Swags") → [].
    """
    if not text:
        return []
    out: list[tuple[str, float]] = []
    for m in _PRIZE_RE.finditer(text):
        if m.group("pcur") is not None:
            cur, num, mult = m.group("pcur"), m.group("pnum"), m.group("pmult")
        else:
            cur, num, mult = m.group("scur"), m.group("snum"), m.group("smult")
        amount = float(num.replace(",", ""))
        if mult:
            amount *= _MULTIPLIERS[mult.strip().lower()]
        out.append((_currency_code(cur), amount))
    return out


def prize_usd(text: str | None) -> float | None:
    """Sum of ``parse_prize`` converted with RATES_TO_USD. None if nothing parsed
    or a currency has no rate."""
    parts = parse_prize(text)
    if not parts:
        return None
    total = 0.0
    for cur, amount in parts:
        rate = RATES_TO_USD.get(cur)
        if rate is None:
            return None
        total += amount * rate
    return total


# --- region -----------------------------------------------------------------


def _norm_place(s: str) -> str:
    return " ".join(s.lower().split())


def _region_variants(region: str) -> set[str]:
    """``region`` plus every spelling obtained by swapping a known city alias."""
    variants = {region}
    for aliases in CITY_ALIASES:
        for name in aliases:
            pat = re.compile(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])")
            if pat.search(region):
                for other in aliases - {name}:
                    variants.add(pat.sub(other, region))
    return variants


def region_matches(region: str, location: str | None) -> bool:
    """Case-insensitive substring match of ``region`` in ``location``, also trying
    every alias from CITY_ALIASES. ``location`` None → False."""
    if location is None:
        return False
    loc = _norm_place(location)
    reg = _norm_place(region)
    if not reg:
        return True
    # A substring that starts at a word boundary, so "bengaluru" matches
    # "Bengaluru, Karnataka" but "in" doesn't match "Chennai".
    return any(re.search(rf"(?<![a-z0-9]){re.escape(v)}", loc) for v in _region_variants(reg))


# --- filter -----------------------------------------------------------------


def _fmt_num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


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
        if not self.include_closed and not h.is_open(now):
            return False
        if self.within_days is not None:
            nd = h.next_deadline(now)
            if nd is None or nd.at > now + timedelta(days=self.within_days):
                return False
        if self.sources and h.source not in self.sources:
            return False
        if self.modes and h.mode not in self.modes:
            return False
        if self.search:
            hay = " ".join([h.title, h.organizer or "", h.location or "", *h.themes]).lower()
            if self.search.lower() not in hay:
                return False
        if self.themes:
            have = [t.lower() for t in h.themes]
            if not any(want.lower() in t for want in self.themes for t in have):
                return False
        if self.region and self.region.strip() and not region_matches(self.region, h.location):
            return False
        if self.min_prize_usd is not None:
            usd = prize_usd(h.prize)
            if usd is None or usd < self.min_prize_usd:
                return False
        if self.team_size is not None:
            lo = h.team_size_min or 1
            hi = h.team_size_max or math.inf
            if not lo <= self.team_size <= hi:
                return False
        return True

    def merged(self, override: Filter) -> Filter:
        """A copy where every criterion set in ``override`` (not None, and
        include_closed only if True) replaces this one's."""
        changes: dict[str, Any] = {}
        for f in fields(self):
            v = getattr(override, f.name)
            if f.name == "include_closed":
                if v:
                    changes[f.name] = True
            elif v is not None:
                changes[f.name] = list(v) if isinstance(v, list) else v
        out = replace(self, **changes)
        for name in ("sources", "modes", "themes"):  # don't share lists with self
            if getattr(out, name) is not None and name not in changes:
                setattr(out, name, list(getattr(out, name)))
        return out

    def is_empty(self) -> bool:
        """True when no criterion is set (include_closed False counts as unset)."""
        return not self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe; omits unset criteria; enums as their values."""
        d: dict[str, Any] = {}
        if self.sources:
            d["sources"] = [Source(s).value for s in self.sources]
        if self.modes:
            d["modes"] = [Mode(m).value for m in self.modes]
        if self.within_days is not None:
            d["within_days"] = self.within_days
        if self.search:
            d["search"] = self.search
        if self.include_closed:
            d["include_closed"] = True
        if self.themes:
            d["themes"] = list(self.themes)
        if self.region is not None and self.region.strip():
            d["region"] = self.region
        if self.min_prize_usd is not None:
            d["min_prize_usd"] = self.min_prize_usd
        if self.team_size is not None:
            d["team_size"] = self.team_size
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Filter:
        """Inverse of to_dict. Unknown keys raise ValueError."""
        if not isinstance(d, dict):
            raise ValueError(f"filter must be an object, not {type(d).__name__}")
        known = {f.name for f in fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown filter keys: {', '.join(sorted(unknown))}")
        kw: dict[str, Any] = {}
        for key, v in d.items():
            if v is None:
                continue
            if key in ("sources", "modes", "themes"):
                if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                    raise ValueError(f"{key} must be a list of strings")
                if key == "sources":
                    v = [Source(x) for x in v]  # ValueError on a bad value
                elif key == "modes":
                    v = [Mode(x) for x in v]
                else:
                    v = list(v)
            elif key in ("within_days", "min_prize_usd"):
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    raise ValueError(f"{key} must be a number")
            elif key == "team_size":
                if isinstance(v, bool) or not isinstance(v, int):
                    raise ValueError("team_size must be an integer")
            elif key in ("search", "region"):
                if not isinstance(v, str):
                    raise ValueError(f"{key} must be a string")
            elif key == "include_closed":
                if not isinstance(v, bool):
                    raise ValueError("include_closed must be true or false")
            kw[key] = v
        return cls(**kw)

    def describe(self) -> str:
        """One line for humans, e.g. "online · themes AI, Web3 · ≥ $1,000 · within 14d".
        "(everything)" when empty."""
        parts: list[str] = []
        if self.sources:
            parts.append("from " + ", ".join(Source(s).value for s in self.sources))
        if self.modes:
            parts.append(", ".join(Mode(m).value for m in self.modes))
        if self.search:
            parts.append(f'matching "{self.search}"')
        if self.themes:
            parts.append("themes " + ", ".join(self.themes))
        if self.region is not None and self.region.strip():
            parts.append(f"in {self.region.strip()}")
        if self.min_prize_usd is not None:
            parts.append(f"≥ ${self.min_prize_usd:,.0f}")
        if self.team_size is not None:
            parts.append(f"team of {self.team_size}")
        if self.within_days is not None:
            parts.append(f"within {_fmt_num(self.within_days)}d")
        if self.include_closed:
            parts.append("incl. closed")
        return " · ".join(parts) if parts else "(everything)"


# --- persistence ------------------------------------------------------------

_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _norm_name(name: str) -> str:
    return name.strip().lower()


class SavedSearches:
    def __init__(self, db: sqlite3.Connection) -> None:
        """Create table if missing."""
        self.db = db
        with self.db:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS saved_searches ("
                " user_id    TEXT NOT NULL DEFAULT 'local',"
                " name       TEXT NOT NULL,"
                " filter     TEXT NOT NULL,"
                " updated_at TEXT NOT NULL,"
                " PRIMARY KEY (user_id, name))"
            )

    def save(self, name: str, f: Filter) -> None:
        """Insert or replace. Name is stripped and lower-cased; must match
        ``[a-z0-9][a-z0-9_-]{0,39}`` else ValueError."""
        key = _norm_name(name)
        if not _NAME_RE.fullmatch(key):
            raise ValueError(
                f"invalid search name {name!r}: use 1-40 letters, digits, '-' or '_', "
                "starting with a letter or digit")
        blob = json.dumps(f.to_dict(), sort_keys=True, ensure_ascii=False)
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO saved_searches (user_id, name, filter, updated_at) VALUES (?,?,?,?)",
                (LOCAL_USER, key, blob, _now_iso()),
            )

    def get(self, name: str) -> Filter | None:
        row = self.db.execute(
            "SELECT filter FROM saved_searches WHERE user_id = ? AND name = ?",
            (LOCAL_USER, _norm_name(name)),
        ).fetchone()
        return Filter.from_dict(json.loads(row[0])) if row else None

    def all(self) -> list[tuple[str, Filter]]:
        """Sorted by name."""
        rows = self.db.execute(
            "SELECT name, filter FROM saved_searches WHERE user_id = ? ORDER BY name",
            (LOCAL_USER,),
        ).fetchall()
        return [(r[0], Filter.from_dict(json.loads(r[1]))) for r in rows]

    def delete(self, name: str) -> bool:
        with self.db:
            cur = self.db.execute(
                "DELETE FROM saved_searches WHERE user_id = ? AND name = ?",
                (LOCAL_USER, _norm_name(name)),
            )
        return cur.rowcount > 0


# Preference keys the CLI understands. Values are strings.
PREF_KEYS: dict[str, str] = {
    "tz": "IANA timezone for displayed dates (used when --tz and $LOGR_TZ are absent)",
    "luma_near": "';'-separated Luma places for sync (used when --luma-near and $LOGR_LUMA_NEAR are absent)",
    "default_search": "saved search applied by `logr list` unless --saved or --no-prefs is given",
}


class Preferences:
    def __init__(self, db: sqlite3.Connection) -> None:
        """Create table if missing."""
        self.db = db
        with self.db:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS preferences ("
                " user_id    TEXT NOT NULL DEFAULT 'local',"
                " key        TEXT NOT NULL,"
                " value      TEXT NOT NULL,"
                " updated_at TEXT NOT NULL,"
                " PRIMARY KEY (user_id, key))"
            )

    def get(self, key: str) -> str | None:
        row = self.db.execute(
            "SELECT value FROM preferences WHERE user_id = ? AND key = ?", (LOCAL_USER, key),
        ).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        """Unknown key (not in PREF_KEYS) raises KeyError."""
        if key not in PREF_KEYS:
            raise KeyError(key)
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO preferences (user_id, key, value, updated_at) VALUES (?,?,?,?)",
                (LOCAL_USER, key, str(value), _now_iso()),
            )

    def unset(self, key: str) -> bool:
        with self.db:
            cur = self.db.execute(
                "DELETE FROM preferences WHERE user_id = ? AND key = ?", (LOCAL_USER, key),
            )
        return cur.rowcount > 0

    def all(self) -> dict[str, str]:
        rows = self.db.execute(
            "SELECT key, value FROM preferences WHERE user_id = ? ORDER BY key", (LOCAL_USER,),
        ).fetchall()
        return {r[0]: r[1] for r in rows}
