"""Hack2skill — public event list the hack2skill.com homepage loads.

Endpoint: GET https://hack2skill.com/api/v1/innovator/public/event/list?page=1&records=100
Returns every listed event at once in ``data.flagshipEvents`` and
``data.communityEvents`` (paging params are ignored), including past ones.
Only the registration window is published here; hackathon/submission dates are
on the event page — Phase 2.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from logr.models import Hackathon, Mode, Source
from logr.sources.base import Connector, clean, parse_dt

API = "https://hack2skill.com/api/v1/innovator/public/event/list"


class Hack2skillConnector(Connector):
    source = Source.HACK2SKILL

    def fetch(self) -> Iterator[Hackathon]:
        data = self.http.get_json(API, params={"page": 1, "records": 100})
        yield from self.parse(data)

    @staticmethod
    def parse(data: dict[str, Any]) -> Iterator[Hackathon]:
        body = data.get("data") or {}
        for bucket in ("flagshipEvents", "communityEvents"):
            for e in body.get(bucket) or []:
                if e.get("status") not in (None, "APPROVED"):
                    continue
                tags = e.get("tags") or {}
                mode_raw = ((tags.get("mode") or {}).get("value") or "").lower()
                mode = {"online": Mode.ONLINE, "virtual": Mode.ONLINE, "offline": Mode.OFFLINE,
                        "in_person": Mode.OFFLINE, "hybrid": Mode.HYBRID}.get(mode_raw, Mode.UNKNOWN)
                team = tags.get("teamSize") or {}
                tech = (tags.get("technology") or {}).get("value") or []
                url = e.get("customEventUrl") or f"https://hack2skill.com/event/{e.get('eventUrl', '')}"
                yield Hackathon(
                    source=Source.HACK2SKILL,
                    source_id=str(e["_id"]),
                    title=clean(e.get("title")) or "Untitled",
                    url=url,
                    organizer="Hack2skill" if bucket == "flagshipEvents" else None,
                    mode=mode,
                    themes=[str(t) for t in tech if isinstance(t, str)],
                    team_size_min=team.get("min"),
                    team_size_max=team.get("max"),
                    image_url=e.get("thumbnail"),
                    registration_opens_at=parse_dt(e.get("registrationStart")),
                    registration_closes_at=parse_dt(e.get("registrationEnd")),
                )
