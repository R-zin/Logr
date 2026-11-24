"""Devfolio — Elasticsearch-backed search API used by devfolio.co/hackathons.

Endpoint: POST https://api.devfolio.co/api/search/hackathons
Body: {"type": "application_open" | "upcoming", "from": N, "size": N}
Gives exact UTC timestamps: ``hackathon_setting.reg_starts_at/reg_ends_at``
(applications) and ``starts_at/ends_at`` (the hack itself).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from logr.models import Hackathon, Mode, Source
from logr.sources.base import Connector, clean, parse_dt

API = "https://api.devfolio.co/api/search/hackathons"
TYPES = ("application_open", "upcoming")
PAGE = 50


class DevfolioConnector(Connector):
    source = Source.DEVFOLIO

    def fetch(self) -> Iterator[Hackathon]:
        seen: set[str] = set()
        for kind in TYPES:
            offset = 0
            for _ in range(self.max_pages):
                data = self.http.post_json(API, json={"type": kind, "from": offset, "size": PAGE})
                hits = (data.get("hits") or {}).get("hits") or []
                for h in self.parse(data):
                    if h.source_id not in seen:
                        seen.add(h.source_id)
                        yield h
                total = ((data.get("hits") or {}).get("total") or {}).get("value") or 0
                offset += len(hits)
                if not hits or offset >= total:
                    break

    @staticmethod
    def parse(data: dict[str, Any]) -> Iterator[Hackathon]:
        for hit in (data.get("hits") or {}).get("hits") or []:
            s = hit.get("_source") or {}
            if s.get("private") or not s.get("uuid"):
                continue
            setting = s.get("hackathon_setting") or {}
            sub = setting.get("subdomain") or s.get("slug")
            url = setting.get("external_apply_url") or (f"https://{sub}.devfolio.co/" if sub else "https://devfolio.co/hackathons")
            online = s.get("is_online")
            place = ", ".join(p for p in (s.get("city"), s.get("state"), s.get("country")) if p)
            prizes = [p.get("name") for p in s.get("prizes") or [] if p.get("name")]
            yield Hackathon(
                source=Source.DEVFOLIO,
                source_id=s["uuid"],
                title=clean(s.get("name")) or "Untitled",
                url=url,
                mode=Mode.ONLINE if online else Mode.OFFLINE if online is False else Mode.UNKNOWN,
                location=None if online else (place or clean(s.get("location"))),
                themes=[t["name"] for t in s.get("themes") or [] if t.get("name")],
                prize=", ".join(prizes[:3]) or None,
                participants=s.get("participants_count"),
                team_size_max=s.get("team_size"),
                image_url=s.get("cover_img"),
                registration_opens_at=parse_dt(setting.get("reg_starts_at")),
                registration_closes_at=parse_dt(setting.get("reg_ends_at")),
                starts_at=parse_dt(s.get("starts_at")),
                ends_at=parse_dt(s.get("ends_at")),
            )
