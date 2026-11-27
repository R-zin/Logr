import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from logr.http import Http
from logr.models import Mode, Source
from logr.sources import CONNECTORS
from logr.sources.devfolio import DevfolioConnector
from logr.sources.devpost import DevpostConnector, parse_period
from logr.sources.hack2skill import Hack2skillConnector
from logr.sources.luma import LumaConnector
from logr.sources.unstop import UnstopConnector

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / f"{name}.json").read_text())


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Dec 05, 2026", (date(2026, 12, 5), date(2026, 12, 5))),
        ("Nov 11 - 30, 2026", (date(2026, 11, 11), date(2026, 11, 30))),
        ("Oct 30 - Dec 04, 2026", (date(2026, 10, 30), date(2026, 12, 4))),
        ("Dec 19, 2026 - Jan 06, 2027", (date(2026, 12, 19), date(2027, 1, 6))),
        # start month after end month with no start year → previous year
        ("Feb 27 - Dec 25, 2026", (date(2026, 2, 27), date(2026, 12, 25))),
        ("Nov 20 - Jan 05, 2027", (date(2026, 11, 20), date(2027, 1, 5))),
        ("", None),
        ("Coming soon", None),
    ],
)
def test_devpost_parse_period(text, expected):
    assert parse_period(text) == expected


def test_devpost_parse():
    items = list(DevpostConnector.parse(load("devpost")))
    assert len(items) == 3
    k = items[0]
    assert k.uid == "devpost:31667"
    assert k.title == "Kiroween 2026"
    assert k.mode is Mode.ONLINE and k.location is None
    assert k.prize == "$100,000"
    assert k.submission_deadline == datetime(2026, 12, 4, tzinfo=timezone.utc)
    assert k.approx_fields == ["submission_deadline", "starts_at"]
    assert "Machine Learning/AI" in k.themes
    assert k.image_url.startswith("https://")


def test_devfolio_parse():
    items = list(DevfolioConnector.parse(load("devfolio")))
    m = items[0]
    assert m.title == "MakerSpark"
    assert m.url == "https://makerspark.devfolio.co/"
    assert m.mode is Mode.OFFLINE
    assert "Thiruvananthapuram" in m.location
    assert m.registration_closes_at == datetime(2026, 11, 25, 18, 29, tzinfo=timezone.utc)
    assert m.starts_at == datetime(2026, 12, 9, 3, 30, tzinfo=timezone.utc)
    # Devfolio submissions close when the hack ends; exact dates otherwise
    assert m.submission_deadline == m.ends_at
    assert m.approx_fields == ["submission_deadline"]
    assert not m.is_approx("registration_closes_at")


def test_unstop_parse():
    items = list(UnstopConnector.parse(load("unstop")))
    b = items[0]
    assert b.uid == "unstop:1826250"
    assert b.organizer == "Bharat Academix"
    assert b.mode is Mode.ONLINE
    # 2026-12-08T00:00+05:30 → UTC
    assert b.registration_closes_at == datetime(2026, 12, 7, 18, 30, tzinfo=timezone.utc)
    assert b.team_size_min == 1 and b.team_size_max == 6
    assert b.prize and b.prize.startswith("₹")


def test_hack2skill_parse():
    items = list(Hack2skillConnector.parse(load("hack2skill")))
    assert len(items) == 3
    b = items[0]
    assert b.title == "Build 2036"
    assert b.url == "https://hack2skill.com/event/build2036"
    assert b.mode is Mode.HYBRID
    assert b.registration_closes_at == datetime(2026, 11, 30, 18, 29, tzinfo=timezone.utc)
    assert items[2].organizer is None  # community event


def test_luma_parse_filters_non_hackathons():
    items = list(LumaConnector.parse(load("luma")))
    titles = [h.title for h in items]
    assert "Founders Coffee Meetup" not in titles
    assert titles[0] == "HackwithBay 4.0"
    h = items[0]
    assert h.url.startswith("https://luma.com/")
    assert h.registration_closes_at == h.starts_at
    assert h.approx_fields == ["registration_closes_at"]  # start/end are exact
    assert h.location == "San Francisco, CA"


def _http_for(routes):
    """Http wrapper whose transport answers from fixtures; records requests."""
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        for host, body in routes.items():
            if request.url.host == host:
                return httpx.Response(200, json=body)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Http(client, min_interval=0, backoff=0), calls


def test_every_connector_fetches_via_transport():
    http, calls = _http_for({
        "devpost.com": load("devpost") | {"meta": {"total_count": 3}},
        "api.devfolio.co": load("devfolio"),
        "unstop.com": load("unstop"),
        "hack2skill.com": load("hack2skill"),
        "api.lu.ma": load("luma"),
    })
    for src, cls in CONNECTORS.items():
        items = list(cls(http).fetch())
        assert items, src
        assert all(h.source is src for h in items)
    # devfolio queries both "application_open" and "upcoming" but dedupes by uuid
    assert sum(1 for c in calls if c.url.host == "api.devfolio.co") == 2
    assert Source.LINKEDIN not in CONNECTORS


def test_http_retries_transient_errors():
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(503) if attempts["n"] < 3 else httpx.Response(200, json={"ok": True})

    http = Http(httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0, backoff=0)
    assert http.get_json("https://example.test/") == {"ok": True}
    assert attempts["n"] == 3
