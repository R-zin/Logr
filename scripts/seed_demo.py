"""Build a demo database offline from the recorded test fixtures.

Used by CI to give `logr serve` something to show, and handy for trying the
dashboard without syncing. Dates are shifted so the listings are always open
relative to today; one hackathon is tracked so the board and calendar have data.

    python scripts/seed_demo.py demo.db
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from logr.models import DATE_FIELDS, Hackathon
from logr.sources.devfolio import DevfolioConnector
from logr.sources.devpost import DevpostConnector, dates_patch, parse_dates_page
from logr.sources.hack2skill import Hack2skillConnector
from logr.sources.luma import LumaConnector
from logr.sources.unstop import UnstopConnector, parse_rounds, rounds_patch
from logr.store import Store
from logr.tracking import Status, Tracker

FIX = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
ANCHOR = datetime(2026, 11, 24, tzinfo=timezone.utc)  # when the fixtures were recorded


def _load(name: str):
    return json.loads((FIX / name).read_text())


def _shift(h: Hackathon, delta: timedelta) -> Hackathon:
    data = h.model_dump(mode="python")
    for f in DATE_FIELDS:
        if data[f]:
            data[f] += delta
    for r in data["rounds"]:
        for f in ("starts_at", "ends_at"):
            if r[f]:
                r[f] += delta
    return Hackathon.model_validate(data)


def main(path: str) -> None:
    items: list[Hackathon] = []
    devpost = list(DevpostConnector.parse(_load("devpost.json")))
    devpost[0] = devpost[0].with_patch(dates_patch(parse_dates_page((FIX / "devpost_dates.html").read_text())))
    items += devpost
    items += DevfolioConnector.parse(_load("devfolio.json"))
    unstop = list(UnstopConnector.parse(_load("unstop.json")))
    unstop[0] = unstop[0].with_patch(rounds_patch(parse_rounds(_load("unstop_competition.json"))))
    items += unstop
    items += Hack2skillConnector.parse(_load("hack2skill.json"))
    items += LumaConnector.parse(_load("luma.json"))

    delta = datetime.now(timezone.utc) + timedelta(days=3) - ANCHOR
    items = [_shift(h, delta) for h in items]

    store = Store(path)
    store.upsert_many(items)
    Tracker(store.db).track(items[0].uid, Status.REGISTERED)
    store.close()
    print(f"seeded {len(items)} hackathons into {path} (tracking {items[0].uid})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "demo.db")
