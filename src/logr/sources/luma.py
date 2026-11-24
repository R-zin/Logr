"""Luma — the discover search behind luma.com/discover.

Endpoint: GET https://api.lu.ma/discover/get-paginated-events
  ?query=hackathon&pagination_limit=50[&pagination_cursor=...]
This is the website's own (undocumented) endpoint; Luma's official API needs a
Luma Plus key and only covers calendars you manage. Luma events have no
separate registration deadline, so we treat the event start as the last moment
to register (``dates_approximate``). Full-text search is fuzzy, so results are
kept only when the title looks like a hackathon.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from logr.models import Hackathon, Mode, Source
from logr.sources.base import Connector, clean, parse_dt

API = "https://api.lu.ma/discover/get-paginated-events"
QUERIES = ("hackathon", "buildathon")
HACKY = re.compile(r"hack|buildathon|build\s*day|ideathon|codefest|game\s*jam|datathon", re.I)


class LumaConnector(Connector):
    source = Source.LUMA

    def fetch(self) -> Iterator[Hackathon]:
        seen: set[str] = set()
        for q in QUERIES:
            cursor: str | None = None
            for _ in range(self.max_pages):
                params: dict[str, Any] = {"query": q, "pagination_limit": 50}
                if cursor:
                    params["pagination_cursor"] = cursor
                data = self.http.get_json(API, params=params)
                for h in self.parse(data):
                    if h.source_id not in seen:
                        seen.add(h.source_id)
                        yield h
                cursor = data.get("next_cursor")
                if not data.get("has_more") or not cursor:
                    break

    @staticmethod
    def parse(data: dict[str, Any]) -> Iterator[Hackathon]:
        for entry in data.get("entries") or []:
            ev = entry.get("event") or {}
            name = clean(ev.get("name"))
            if not name or not HACKY.search(name) or not ev.get("api_id"):
                continue
            loc_type = (ev.get("location_type") or "").lower()
            mode = Mode.ONLINE if loc_type == "online" else Mode.OFFLINE if loc_type == "offline" else Mode.UNKNOWN
            geo = ev.get("geo_address_info") or {}
            place = geo.get("city_state") or ", ".join(p for p in (geo.get("city"), geo.get("country")) if p) or None
            hosts = [h.get("name") for h in entry.get("hosts") or [] if h.get("name")]
            organizer = (entry.get("calendar") or {}).get("name") or (hosts[0] if hosts else None)
            start = parse_dt(ev.get("start_at"))
            yield Hackathon(
                source=Source.LUMA,
                source_id=ev["api_id"],
                title=name,
                url=f"https://luma.com/{ev.get('url')}" if ev.get("url") else "https://luma.com/discover",
                organizer=clean(organizer),
                mode=mode,
                location=None if mode is Mode.ONLINE else place,
                participants=entry.get("guest_count"),
                image_url=ev.get("cover_url"),
                registration_closes_at=start,
                starts_at=start,
                ends_at=parse_dt(ev.get("end_at")),
                dates_approximate=True,
            )
