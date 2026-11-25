import json
from datetime import datetime, timedelta, timezone

import httpx

from logr import cli
from logr.http import Http
from logr.models import Hackathon, Mode, Source
from logr.store import Store
from logr.sync import sync

NOW = datetime(2026, 11, 24, 12, tzinfo=timezone.utc)


def hk(sid, **kw):
    base = dict(source=Source.DEVFOLIO, source_id=sid, title=f"Hack {sid}", url=f"https://{sid}.example")
    return Hackathon(**(base | kw))


def test_naive_datetimes_rejected():
    import pytest

    with pytest.raises(ValueError):
        hk("x", starts_at=datetime(2026, 1, 1))


def test_next_deadline_and_open():
    h = hk("a", registration_closes_at=NOW - timedelta(days=1), submission_deadline=NOW + timedelta(days=2))
    assert h.next_deadline(NOW) == ("submission due", NOW + timedelta(days=2), False)
    assert h.is_open(NOW)
    closed = hk("b", registration_closes_at=NOW - timedelta(hours=1))
    assert closed.next_deadline(NOW) is None and not closed.is_open(NOW)
    no_dates = hk("c", ends_at=NOW + timedelta(days=1))
    assert no_dates.is_open(NOW)


def test_upsert_tracks_date_changes():
    s = Store(":memory:")
    r1 = s.upsert_many([hk("a", registration_closes_at=NOW)])
    assert (r1.created, r1.updated) == (1, 0)
    r2 = s.upsert_many([hk("a", registration_closes_at=NOW)])
    assert (r2.created, r2.updated, r2.unchanged) == (0, 0, 1)
    later = NOW + timedelta(days=3)
    r3 = s.upsert_many([hk("a", registration_closes_at=later, title="Renamed")])
    assert r3.updated == 1
    assert r3.date_changes == [("devfolio:a", "registration_closes_at", "2026-11-24T12:00:00Z", "2026-11-27T12:00:00Z")]
    assert s.get("devfolio:a").title == "Renamed"
    assert [r["field"] for r in s.history("devfolio:a")] == ["registration_closes_at"]


def test_select_filters_and_sorts():
    items = [
        hk("far", registration_closes_at=NOW + timedelta(days=20)),
        hk("soon", registration_closes_at=NOW + timedelta(days=1), mode=Mode.ONLINE, themes=["AI"]),
        hk("past", registration_closes_at=NOW - timedelta(days=1)),
        hk("nodate"),
    ]
    assert [lst.primary.source_id for lst in cli.select(items, now=NOW)] == ["soon", "far", "nodate"]
    assert [lst.primary.source_id for lst in cli.select(items, now=NOW, within_days=7)] == ["soon"]
    assert [lst.primary.source_id for lst in cli.select(items, now=NOW, modes=[Mode.ONLINE])] == ["soon"]
    assert [lst.primary.source_id for lst in cli.select(items, now=NOW, search="ai")] == ["soon"]
    assert len(cli.select(items, now=NOW, include_closed=True)) == 4


def test_sync_isolates_failing_source():
    def handler(req):
        if req.url.host == "unstop.com":
            return httpx.Response(500)
        return httpx.Response(200, json={"data": {"flagshipEvents": [], "communityEvents": []}})

    http = Http(httpx.Client(transport=httpx.MockTransport(handler)), retries=0, min_interval=0)
    s = Store(":memory:")
    reports = sync(s, [Source.UNSTOP, Source.HACK2SKILL], http=http)
    by = {r.source: r for r in reports}
    assert by[Source.UNSTOP].error and "500" in by[Source.UNSTOP].error
    assert by[Source.HACK2SKILL].error is None
    assert {r["source"] for r in s.last_runs()} == {"unstop", "hack2skill"}


def test_cli_list_json_and_show(tmp_path, capsys):
    db = tmp_path / "t.db"
    s = Store(db)
    soon = datetime.now(timezone.utc) + timedelta(days=2)
    s.upsert_many([hk("a", registration_closes_at=soon, organizer="Org")])
    s.close()

    assert cli.main(["--db", str(db), "list", "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["uid"] == "devfolio:a"

    assert cli.main(["--db", str(db), "--tz", "Asia/Kolkata", "show", "devfolio:a"]) == 0
    text = capsys.readouterr().out
    assert "Registration closes" in text and "Org" in text

    assert cli.main(["--db", str(db), "show", "nope:1"]) == 1
