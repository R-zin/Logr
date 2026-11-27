"""Unstop — public search API used by unstop.com/hackathons.

Endpoint: GET https://unstop.com/api/public/opportunity/search-result
  ?opportunity=hackathons&oppstatus=open&page=N&per_page=30
Laravel-style pagination (``data.last_page``). Registration window is in
``regnRequirements.start_regn_dt/end_regn_dt``; ``end_date`` is the
opportunity's overall end.

Enrichment: GET https://unstop.com/api/public/competition/<id> returns
``data.competition.rounds[].details[]`` with exact IST start/end per round
(screening, ideation, prototype, finale...). Rounds flagged ``is_hidden`` are
internal (e.g. auto-screening tied to registration) and are skipped.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from logr.models import Hackathon, Mode, Round, RoundKind, Source, classify_round
from logr.sources.base import Connector, clean, iso, parse_dt, rounds_json

API = "https://unstop.com/api/public/opportunity/search-result"
DETAIL = "https://unstop.com/api/public/competition/{id}"
_SYMBOL = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}


def _prize(prizes: list[dict[str, Any]] | None) -> str | None:
    if not prizes:
        return None
    by_ccy: dict[str, float] = {}
    for p in prizes:
        cash = p.get("cash")
        if isinstance(cash, (int, float)) and cash > 0:
            ccy = p.get("currencyCode") or "INR"
            by_ccy[ccy] = by_ccy.get(ccy, 0) + cash
    if not by_ccy:
        return None
    return " + ".join(f"{_SYMBOL.get(c, c + ' ')}{amt:,.0f}" for c, amt in by_ccy.items())


def parse_rounds(data: dict[str, Any]) -> list[Round]:
    comp = (data.get("data") or {}).get("competition") or {}
    out = []
    for r in sorted(comp.get("rounds") or [], key=lambda r: r.get("round_order") or 0):
        if r.get("is_hidden"):
            continue
        for d in r.get("details") or []:
            name = clean(d.get("title"))
            if not name:
                continue
            start, end = parse_dt(d.get("start_date")), parse_dt(d.get("end_date"))
            if start or end:
                out.append(Round(name=name, kind=classify_round(name), starts_at=start, ends_at=end))
    return out


def rounds_patch(rounds: list[Round]) -> dict[str, Any]:
    patch: dict[str, Any] = {"rounds": rounds_json(rounds)}
    subs = [r.ends_at for r in rounds if r.kind is RoundKind.SUBMISSION and r.ends_at]
    if subs:
        patch["submission_deadline"] = iso(max(subs))
    events = [r.starts_at for r in rounds if r.kind is RoundKind.EVENT and r.starts_at]
    if events:
        patch["starts_at"] = iso(min(events))
    return patch


class UnstopConnector(Connector):
    source = Source.UNSTOP
    can_enrich = True

    def enrich(self, h: Hackathon) -> dict[str, Any] | None:
        rounds = parse_rounds(self.http.get_json(DETAIL.format(id=h.source_id)))
        return rounds_patch(rounds) if rounds else None

    def fetch(self) -> Iterator[Hackathon]:
        page = 1
        while page <= self.max_pages:
            data = self.http.get_json(
                API,
                params={"opportunity": "hackathons", "oppstatus": "open", "page": page, "per_page": 30},
            )
            yield from self.parse(data)
            body = data.get("data") or {}
            if not body.get("data") or page >= (body.get("last_page") or 1):
                return
            page += 1

    @staticmethod
    def parse(data: dict[str, Any]) -> Iterator[Hackathon]:
        for x in (data.get("data") or {}).get("data") or []:
            regn = x.get("regnRequirements") or {}
            region = (x.get("region") or "").lower()
            mode = {"online": Mode.ONLINE, "offline": Mode.OFFLINE, "hybrid": Mode.HYBRID}.get(region, Mode.UNKNOWN)
            addr = x.get("address_with_country_logo") or {}
            place = ", ".join(p for p in (addr.get("city"), addr.get("state")) if p) or None
            url = x.get("seo_url") or f"https://unstop.com/{x.get('public_url', '')}"
            yield Hackathon(
                source=Source.UNSTOP,
                source_id=str(x["id"]),
                title=clean(x.get("title")) or "Untitled",
                url=url,
                organizer=clean((x.get("organisation") or {}).get("name")),
                mode=mode,
                location=None if mode is Mode.ONLINE else place,
                themes=[w["name"] for w in x.get("workfunction") or [] if w.get("name")][:6],
                prize=_prize(x.get("prizes")),
                participants=x.get("registerCount"),
                team_size_min=regn.get("min_team_size"),
                team_size_max=regn.get("max_team_size"),
                image_url=x.get("logoUrl2"),
                registration_opens_at=parse_dt(regn.get("start_regn_dt")),
                registration_closes_at=parse_dt(regn.get("end_regn_dt")),
                ends_at=parse_dt(x.get("end_date")),
            )
