"""Devpost — public JSON listing used by devpost.com/hackathons.

Endpoint: GET https://devpost.com/api/hackathons?status[]=open&status[]=upcoming&page=N
Gives 9 per page and ``meta.total_count``. Dates are only a human string,
``submission_period_dates`` ("Oct 30 - Dec 04, 2026"), with no time of day.
The exact deadline lives on each hackathon page (``data-iso-date``); fetching
that is Phase 2 enrichment. Until then we use 00:00 UTC on the final day, which
is never later than the real deadline, and flag the dates as approximate.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterator
from datetime import date, datetime, time, timezone
from typing import Any

from logr.models import Hackathon, Mode, Source
from logr.sources.base import Connector, clean

API = "https://devpost.com/api/hackathons"

_MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
_PART = re.compile(r"^(?:(?P<mon>[A-Za-z]{3})\s+)?(?P<day>\d{1,2})(?:,\s*(?P<year>\d{4}))?$")


def parse_period(text: str | None) -> tuple[date, date] | None:
    """Parse Devpost's period strings. Handles:

    "Dec 05, 2026" · "Nov 11 - 30, 2026" · "Oct 30 - Dec 04, 2026" ·
    "Dec 19, 2026 - Jan 06, 2027"
    """
    if not text:
        return None
    pieces = [p.strip() for p in text.split(" - ")]
    if len(pieces) == 1:
        pieces = pieces * 2
    if len(pieces) != 2:
        return None
    m1, m2 = (_PART.match(p) for p in pieces)
    if not (m1 and m2) or not m2["year"]:
        return None
    end_mon = m2["mon"] or m1["mon"]
    start_mon = m1["mon"]
    if not start_mon or start_mon[:3].title() not in _MON or end_mon[:3].title() not in _MON:
        return None
    end = date(int(m2["year"]), _MON.index(end_mon[:3].title()) + 1, int(m2["day"]))
    start_year = int(m1["year"]) if m1["year"] else end.year
    start = date(start_year, _MON.index(start_mon[:3].title()) + 1, int(m1["day"]))
    if start > end and not m1["year"]:
        start = start.replace(year=end.year - 1)
    return start, end


def _prize(raw: Any) -> str | None:
    if not raw:
        return None
    text = html.unescape(re.sub(r"<[^>]+>", "", str(raw)))
    return clean(text) if text.strip("$0 ,") else None


def _url(u: str | None) -> str | None:
    if not u:
        return None
    return "https:" + u if u.startswith("//") else u


class DevpostConnector(Connector):
    source = Source.DEVPOST

    def fetch(self) -> Iterator[Hackathon]:
        page = 1
        seen = 0
        while page <= self.max_pages:
            data = self.http.get_json(
                API, params=[("status[]", "open"), ("status[]", "upcoming"), ("page", page)]
            )
            items = list(self.parse(data))
            yield from items
            seen += len(data.get("hackathons", []))
            total = (data.get("meta") or {}).get("total_count") or 0
            if not data.get("hackathons") or seen >= total:
                return
            page += 1

    @staticmethod
    def parse(data: dict[str, Any]) -> Iterator[Hackathon]:
        for h in data.get("hackathons", []):
            if h.get("invite_only"):
                continue
            period = parse_period(h.get("submission_period_dates"))
            start = end = None
            if period:
                start = datetime.combine(period[0], time.min, timezone.utc)
                end = datetime.combine(period[1], time.min, timezone.utc)
            loc = (h.get("displayed_location") or {}).get("location")
            mode = Mode.ONLINE if (loc or "").lower() == "online" else Mode.OFFLINE if loc else Mode.UNKNOWN
            yield Hackathon(
                source=Source.DEVPOST,
                source_id=str(h["id"]),
                title=clean(h.get("title")) or "Untitled",
                url=h.get("url") or "https://devpost.com/hackathons",
                organizer=clean(h.get("organization_name")),
                mode=mode,
                location=None if mode is Mode.ONLINE else clean(loc),
                themes=[t["name"] for t in h.get("themes") or [] if t.get("name")],
                prize=_prize(h.get("prize_amount")),
                participants=h.get("registrations_count"),
                image_url=_url(h.get("thumbnail_url")),
                starts_at=start,
                submission_deadline=end,
                dates_approximate=True,
            )
