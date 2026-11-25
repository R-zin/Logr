"""Luma — the discover search behind luma.com/discover.

Endpoint: GET https://api.lu.ma/discover/get-paginated-events
  ?query=hackathon&pagination_limit=50[&pagination_cursor=...]
This is the website's own (undocumented) endpoint; Luma's official API needs a
Luma Plus key and only covers calendars you manage. Luma events have no
separate registration deadline, so we treat the event start as the last moment
to register (flagged in ``approx_fields``). Full-text search is fuzzy, so results are
kept only when the title looks like a hackathon.

Without coordinates Luma returns a global mix. ``latitude``/``longitude`` scope
results to that area, so the connector can search several places
(``places=["bengaluru", "12.97,77.59", ...]``) plus the global feed.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from itertools import product
from typing import Any

from logr.models import Hackathon, Mode, Source
from logr.sources.base import Connector, clean, parse_dt

API = "https://api.lu.ma/discover/get-paginated-events"
QUERIES = ("hackathon", "buildathon")
PLACES: dict[str, tuple[float, float]] = {
    "bengaluru": (12.97, 77.59), "bangalore": (12.97, 77.59), "delhi": (28.61, 77.21),
    "mumbai": (19.08, 72.88), "hyderabad": (17.39, 78.49), "chennai": (13.08, 80.27),
    "pune": (18.52, 73.86), "kolkata": (22.57, 88.36), "ahmedabad": (23.02, 72.57),
    "kochi": (9.93, 76.27), "san-francisco": (37.77, -122.42), "new-york": (40.71, -74.01),
    "london": (51.51, -0.13), "berlin": (52.52, 13.40), "paris": (48.86, 2.35),
    "singapore": (1.35, 103.82), "dubai": (25.20, 55.27), "toronto": (43.65, -79.38),
}


def resolve_place(place: str) -> tuple[float, float]:
    """A known city slug or a "lat,lng" pair."""
    key = place.strip().lower().replace(" ", "-")
    if key in PLACES:
        return PLACES[key]
    try:
        lat, lng = (float(x) for x in key.split(","))
    except ValueError:
        raise ValueError(f"unknown place {place!r}; use one of {', '.join(sorted(PLACES))} or 'lat,lng'") from None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError(f"coordinates out of range: {place!r}")
    return lat, lng


HACKY = re.compile(r"hack|buildathon|build\s*day|ideathon|codefest|game\s*jam|datathon", re.I)


class LumaConnector(Connector):
    source = Source.LUMA

    def __init__(self, http: Any, *, places: list[str] | tuple[str, ...] = (), **kw: Any) -> None:
        super().__init__(http, **kw)
        self.coords: list[tuple[float, float] | None] = [None, *(resolve_place(p) for p in places)]

    def fetch(self) -> Iterator[Hackathon]:
        seen: set[str] = set()
        for coord, q in product(self.coords, QUERIES):
            cursor: str | None = None
            for _ in range(self.max_pages):
                params: dict[str, Any] = {"query": q, "pagination_limit": 50}
                if coord:
                    params["latitude"], params["longitude"] = coord
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
                approx_fields=["registration_closes_at"] if start else [],
            )
