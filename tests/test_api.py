"""HTTP API (docs/API.md), excluding the auth routes (tests/test_auth.py)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import logr.auth
import logr.sync
from logr.api import create_app
from logr.api import sync as api_sync
from logr.auth import AuthConfig, OAuthProvider, User
from logr.models import Hackathon, Mode, Round, Source
from logr.store import Store

H = {"X-Requested-With": "logr"}
NOW = datetime.now(UTC).replace(microsecond=0)


def d(days: float) -> datetime:
    return NOW + timedelta(days=days)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def seed() -> list[Hackathon]:
    return [
        Hackathon(source=Source.DEVFOLIO, source_id="alpha", title="Alpha Quantum AI Hackathon",
                  url="https://devfolio.co/alpha", mode=Mode.ONLINE, themes=["AI", "Quantum"], prize="$5,000",
                  registration_closes_at=d(5), starts_at=d(10), ends_at=d(12), team_size_min=1, team_size_max=4,
                  rounds=[Round(name="Idea Submission", starts_at=d(1), ends_at=d(7)),
                          Round(name="Prototype Submission", starts_at=d(8), ends_at=d(11))]),
        Hackathon(source=Source.LUMA, source_id="evt-alpha", title="Alpha Quantum AI Hackathon",
                  url="https://lu.ma/alpha", mode=Mode.OFFLINE, location="Bengaluru",
                  registration_closes_at=d(10), starts_at=d(10), approx_fields=["registration_closes_at"]),
        Hackathon(source=Source.UNSTOP, source_id="1794479", title="Beta Builders Challenge",
                  url="https://unstop.com/beta", mode=Mode.OFFLINE, location="Bangalore, Karnataka",
                  prize="₹250,000", themes=["Fintech"], registration_closes_at=d(2), submission_deadline=d(20),
                  team_size_min=2, team_size_max=4),
        Hackathon(source=Source.DEVPOST, source_id="gamma", title="Gamma Web Jam", url="https://devpost.com/gamma",
                  mode=Mode.ONLINE, prize="$10,000", themes=["Web3"], submission_deadline=d(30),
                  starts_at=d(-1), ends_at=d(31)),
        Hackathon(source=Source.DEVPOST, source_id="old", title="Old Closed Event", url="https://devpost.com/old",
                  mode=Mode.ONLINE, submission_deadline=d(-5)),
        Hackathon(source=Source.HACK2SKILL, source_id="h1", title="Delta Robotics Sprint",
                  url="https://hack2skill.com/h1", mode=Mode.ONLINE, registration_closes_at=d(15)),
    ]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    s = Store(path)
    s.upsert_many(seed())
    s.close()
    return path


@pytest.fixture
def client(db):
    return TestClient(create_app(db, auth=AuthConfig(mode="local")))


def oauth_config() -> AuthConfig:
    gh = OAuthProvider(name="github", client_id="id", client_secret="secret",
                       authorize_url="https://github.com/login/oauth/authorize",
                       token_url="https://github.com/login/oauth/access_token",
                       userinfo_url="https://api.github.com/user", scope="read:user")
    return AuthConfig(mode="oauth", session_secret="x" * 32, providers={"github": gh},
                      admins=frozenset({"github:1"}))


def wait_idle() -> None:
    for _ in range(200):
        if not api_sync.is_running():
            return
        threading.Event().wait(0.025)
    raise AssertionError("background sync did not finish")


def uids(resp) -> list[str]:
    return [i["uid"] for i in resp.json()["items"]]


# --- conventions --------------------------------------------------------------


def test_csrf_header_required_on_non_get(client):
    r = client.post("/api/tracked", json={"uid": "devpost:gamma"})
    assert r.status_code == 403 and "detail" in r.json()
    assert client.put("/api/prefs/tz", json={"value": "UTC"}).status_code == 403
    assert client.delete("/api/searches/x").status_code == 403
    assert client.post("/api/auth/logout").status_code == 403  # the auth router is covered too
    assert client.post("/api/auth/logout", headers=H).status_code == 204
    assert client.post("/api/tracked", json={"uid": "devpost:gamma"}, headers=H).status_code == 201


def test_unknown_api_path_is_json_404(client):
    r = client.get("/api/unknown")
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}


def test_no_cors_headers(client):
    r = client.get("/api/hackathons", headers={"Origin": "https://evil.example"})
    assert not any(k.lower().startswith("access-control-") for k in r.headers)


def test_static_dir_served_after_api(db, tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html>dashboard</html>")
    (web / "404.html").write_text("<html>spa 404</html>")
    c = TestClient(create_app(db, auth=AuthConfig(mode="local"), static_dir=web))
    r = c.get("/")
    assert r.status_code == 200 and "dashboard" in r.text
    assert c.get("/api/hackathons").json()["total"] == 4
    r = c.get("/api/unknown")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
    assert r.json() == {"detail": "Not Found"}
    assert c.get("/nope").status_code == 404  # non-API paths get the static 404
    # a missing static dir is simply not served
    c2 = TestClient(create_app(db, auth=AuthConfig(mode="local"), static_dir=tmp_path / "missing"))
    assert c2.get("/").status_code == 404


# --- hackathons -----------------------------------------------------------------


def test_list_default_merges_and_sorts_by_deadline(client):
    r = client.get("/api/hackathons")
    assert r.status_code == 200
    body = r.json()
    assert uids(r) == ["unstop:1794479", "devfolio:alpha", "hack2skill:h1", "devpost:gamma"]
    assert body["total"] == 4 and body["filter"] == {} and body["saved"] is None
    assert body["now"].endswith("Z")
    alpha = body["items"][1]
    assert alpha["sources"] == ["devfolio", "luma"]
    assert alpha["also_listed"] == [{"uid": "luma:evt-alpha", "source": "luma", "url": "https://lu.ma/alpha"}]
    assert alpha["registration_closes_at"] == iso(d(5))
    assert alpha["next_deadline"] == {"label": "registration closes", "at": iso(d(5)), "approximate": False}
    assert alpha["prize_usd"] == 5000.0 and alpha["tracked_status"] is None
    beta = body["items"][0]
    assert beta["prize"] == "₹250,000" and beta["prize_usd"] == pytest.approx(3000.0)
    assert set(beta) == {
        "uid", "title", "url", "organizer", "mode", "location", "themes", "prize", "prize_usd", "participants",
        "team_size_min", "team_size_max", "image_url", "source", "sources", "also_listed",
        "registration_opens_at", "registration_closes_at", "submission_deadline", "starts_at", "ends_at",
        "approx_fields", "next_deadline", "tracked_status"}


def test_list_no_merge_and_include_closed(client):
    assert len(uids(client.get("/api/hackathons?merge=false"))) == 5
    r = client.get("/api/hackathons?include_closed=true")
    assert "devpost:old" in uids(r) and r.json()["filter"] == {"include_closed": True}


def test_list_filters(client):
    assert uids(client.get("/api/hackathons?source=devpost&source=hack2skill")) == ["hack2skill:h1", "devpost:gamma"]
    assert uids(client.get("/api/hackathons?mode=offline")) == ["unstop:1794479"]
    assert uids(client.get("/api/hackathons?theme=web")) == ["devpost:gamma"]
    assert uids(client.get("/api/hackathons?within=6")) == ["unstop:1794479", "devfolio:alpha"]
    assert uids(client.get("/api/hackathons?q=robotics")) == ["hack2skill:h1"]
    assert uids(client.get("/api/hackathons?region=bengaluru")) == ["unstop:1794479"]
    assert uids(client.get("/api/hackathons?min_prize=4000")) == ["devfolio:alpha", "devpost:gamma"]
    assert uids(client.get("/api/hackathons?team_size=1")) == ["devfolio:alpha", "hack2skill:h1", "devpost:gamma"]
    r = client.get("/api/hackathons?mode=online&theme=ai&within=14")
    assert r.json()["filter"] == {"modes": ["online"], "themes": ["ai"], "within_days": 14.0}
    assert client.get("/api/hackathons?mode=bogus").status_code == 422
    assert client.get("/api/hackathons?limit=501").status_code == 422


def test_list_sort_prize_and_pagination(client):
    r = client.get("/api/hackathons?sort=prize")
    assert uids(r) == ["devpost:gamma", "devfolio:alpha", "unstop:1794479", "hack2skill:h1"]
    r = client.get("/api/hackathons?limit=2&offset=1")
    assert uids(r) == ["devfolio:alpha", "hack2skill:h1"] and r.json()["total"] == 4
    assert client.get("/api/hackathons?sort=title").status_code == 422


def test_list_saved_and_default_search(client):
    assert client.put("/api/searches/online", json={"filter": {"modes": ["online"]}}, headers=H).status_code == 200
    r = client.get("/api/hackathons?saved=online")
    assert r.json()["saved"] == "online"
    assert uids(r) == ["devfolio:alpha", "hack2skill:h1", "devpost:gamma"]
    # other params override / add to the saved search
    r = client.get("/api/hackathons?saved=online&theme=web3")
    assert uids(r) == ["devpost:gamma"] and r.json()["filter"] == {"modes": ["online"], "themes": ["web3"]}
    assert client.get("/api/hackathons?saved=nope").status_code == 404

    assert client.put("/api/prefs/default_search", json={"value": "online"}, headers=H).status_code == 200
    r = client.get("/api/hackathons")
    assert r.json()["saved"] == "online" and len(uids(r)) == 3
    # any filter param, or no_prefs=1, skips the default
    r = client.get("/api/hackathons?mode=offline")
    assert r.json()["saved"] is None and uids(r) == ["unstop:1794479"]
    r = client.get("/api/hackathons?no_prefs=1")
    assert r.json()["saved"] is None and r.json()["total"] == 4
    # non-filter params (sort, limit) still get the default
    assert client.get("/api/hackathons?sort=prize").json()["saved"] == "online"


def test_tracked_status_on_listing_for_any_uid_in_group(client):
    # track the Luma copy; the merged Devfolio row shows it
    client.post("/api/tracked", json={"uid": "luma:evt-alpha", "status": "registered"}, headers=H)
    items = {i["uid"]: i for i in client.get("/api/hackathons").json()["items"]}
    assert items["devfolio:alpha"]["tracked_status"] == "registered"
    assert items["unstop:1794479"]["tracked_status"] is None


def test_detail(client, db):
    s = Store(db)
    moved = seed()[2].model_copy(update={"registration_closes_at": d(3)})
    s.upsert_many([moved])
    s.close()
    r = client.get("/api/hackathons/" + quote("unstop:1794479", safe=""))
    assert r.status_code == 200
    body = r.json()
    assert body["uid"] == "unstop:1794479" and body["registration_closes_at"] == iso(d(3))
    assert body["rounds"] == [] and body["tracking"] is None and body["delisted_at"] is None
    assert body["first_seen_at"].endswith("Z") and body["last_seen_at"].endswith("Z")
    [ch] = body["date_changes"]
    assert ch["field"] == "registration_closes_at" and ch["changed_at"].endswith("Z")
    assert set(ch) == {"field", "old_value", "new_value", "changed_at"}

    r = client.get("/api/hackathons/devfolio:alpha")  # raw colon accepted
    body = r.json()
    assert [x["name"] for x in body["rounds"]] == ["Idea Submission", "Prototype Submission"]
    assert body["rounds"][0] == {"name": "Idea Submission", "kind": "submission",
                                 "starts_at": iso(d(1)), "ends_at": iso(d(7))}
    assert body["also_listed"][0]["uid"] == "luma:evt-alpha"

    client.post("/api/tracked", json={"uid": "luma:evt-alpha"}, headers=H)
    body = client.get("/api/hackathons/devfolio:alpha").json()
    assert body["tracked_status"] == "interested" and body["tracking"]["uid"] == "luma:evt-alpha"
    body = client.get("/api/hackathons/luma:evt-alpha").json()
    assert body["sources"] == ["luma", "devfolio"] and body["tracking"]["status"] == "interested"

    r = client.get("/api/hackathons/devpost%3Anope")
    assert r.status_code == 404 and "detail" in r.json()


# --- calendar -------------------------------------------------------------------


def cal(client, start, end, **kw):
    return client.get("/api/calendar", params={"from": iso(start), "to": iso(end), **kw})


def test_calendar_validation(client):
    assert client.get("/api/calendar").status_code == 400
    assert client.get("/api/calendar", params={"from": iso(d(0))}).status_code == 400
    assert client.get("/api/calendar", params={"from": "garbage", "to": iso(d(1))}).status_code == 400
    assert cal(client, d(5), d(1)).status_code == 400
    assert cal(client, d(0), d(101)).status_code == 400
    assert cal(client, d(0), d(100)).status_code == 200
    assert cal(client, d(0), d(1), scope="mine").status_code == 400


def test_calendar_scope_all(client):
    r = cal(client, d(0), d(14), scope="all")
    assert r.status_code == 200
    ev = r.json()["events"]
    assert [e["at"] for e in ev] == sorted(e["at"] for e in ev)
    kinds = {(e["uid"], e["kind"], e["label"]) for e in ev}
    assert ("unstop:1794479", "deadline", "registration closes") in kinds
    assert ("devfolio:alpha", "round_end", "Idea Submission ends") in kinds
    assert ("devfolio:alpha", "start", "starts") in kinds
    assert ("devfolio:alpha", "end", "ends") in kinds
    assert not any(e["uid"].startswith("luma:") for e in ev)  # merged into devfolio
    assert not any(e["uid"] == "devpost:gamma" for e in ev)  # its events are outside the range
    assert all(iso(d(0)) <= e["at"] < iso(d(14)) for e in ev)
    assert set(ev[0]) == {"uid", "title", "label", "at", "approximate", "kind", "tracked_status"}
    # [from, to): an event exactly at `to` is excluded, at `from` included
    ats = [e["at"] for e in cal(client, d(2), d(5), scope="all").json()["events"]]
    assert iso(d(2)) in ats and iso(d(5)) not in ats


def test_calendar_scope_tracked(client):
    # default scope for a signed-in user is tracked: nothing tracked yet
    assert cal(client, d(0), d(40)).json()["events"] == []
    client.post("/api/tracked", json={"uid": "luma:evt-alpha", "status": "registered"}, headers=H)
    client.post("/api/tracked/luma:evt-alpha/todos", json={"text": "deck", "due_at": iso(d(4))}, headers=H)
    ev = cal(client, d(0), d(40)).json()["events"]
    # deadlines come from the best record (devfolio), minus registration (already registered)
    assert [(e["kind"], e["label"]) for e in ev] == [
        ("todo", "todo: deck"), ("round_end", "Idea Submission ends"), ("round_end", "Prototype Submission ends")]
    assert all(e["uid"] == "luma:evt-alpha" and e["tracked_status"] == "registered" for e in ev)
    assert ev[0]["title"] == "Alpha Quantum AI Hackathon"


def test_calendar_anonymous(db):
    c = TestClient(create_app(db, auth=oauth_config()))
    assert cal(c, d(0), d(14), scope="tracked").status_code == 401
    r = cal(c, d(0), d(14))  # default scope is all when anonymous
    assert r.status_code == 200 and r.json()["events"]
    assert all(e["tracked_status"] is None for e in r.json()["events"])


# --- tracking -------------------------------------------------------------------


def test_tracking_lifecycle(client):
    assert client.get("/api/tracked").json() == []
    r = client.post("/api/tracked", json={"uid": "unstop:1794479"}, headers=H)
    assert r.status_code == 201
    t = r.json()
    assert t["status"] == "interested" and t["team"] == [] and t["checklist"] == [] and t["repo_url"] is None
    assert t["next_action"] == {"label": "registration closes", "at": iso(d(2)), "approximate": False}
    assert t["history"][0]["old"] is None and t["history"][0]["new"] == "interested"
    assert t["hackathon"]["uid"] == "unstop:1794479" and t["hackathon"]["tracked_status"] == "interested"
    assert t["added_at"].endswith("Z")
    assert set(t) == {"uid", "status", "added_at", "updated_at", "notes", "repo_url", "team", "checklist",
                      "next_action", "history", "hackathon"}

    r = client.post("/api/tracked", json={"uid": "unstop:1794479", "status": "won"}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "interested"  # already tracked: unchanged
    assert client.post("/api/tracked", json={"uid": "devpost:nope"}, headers=H).status_code == 404
    assert client.post("/api/tracked", json={"uid": "devpost:gamma", "status": "bogus"}, headers=H).status_code == 422

    u = "/api/tracked/unstop%3A1794479"
    r = client.patch(u, json={"status": "registered", "status_note": "paid fee", "repo_url": "https://gh/x"},
                     headers=H)
    t = r.json()
    assert r.status_code == 200 and t["status"] == "registered" and t["repo_url"] == "https://gh/x"
    assert t["history"][-1] == {"old": "interested", "new": "registered",
                                "changed_at": t["history"][-1]["changed_at"], "note": "paid fee"}
    assert t["next_action"]["label"] == "submission due"  # registration no longer applies
    t = client.patch(u, json={"status": "registered"}, headers=H).json()
    assert t["repo_url"] == "https://gh/x"  # absent field untouched
    assert client.patch(u, json={"repo_url": None}, headers=H).json()["repo_url"] is None

    t = client.post(u + "/notes", json={"text": "met the team"}, headers=H).json()
    assert "] met the team" in t["notes"] and t["notes"].startswith("[")
    assert client.post(u + "/notes", json={"text": "  "}, headers=H).status_code == 400

    t = client.post(u + "/team", json={"name": "Asha"}, headers=H).json()
    t = client.post(u + "/team", json={"name": "Ravi Kumar"}, headers=H).json()
    assert t["team"] == ["Asha", "Ravi Kumar"]
    assert client.post(u + "/team", json={"name": " "}, headers=H).status_code == 400
    r = client.delete(u + "/team/" + quote("ravi kumar"), headers=H)
    assert r.status_code == 200 and r.json()["team"] == ["Asha"]
    r = client.delete(u + "/team/Nobody", headers=H)
    assert r.status_code == 404 and "detail" in r.json()

    r = client.post(u + "/todos", json={"text": "slides", "due_at": iso(d(1))}, headers=H)
    assert r.status_code == 201
    [item] = r.json()["checklist"]
    assert item == {"id": item["id"], "text": "slides", "done": False, "due_at": iso(d(1))}
    assert r.json()["next_action"]["label"] == "todo: slides"
    r = client.post(u + "/todos", json={"text": "video"}, headers=H)
    assert r.status_code == 201 and r.json()["checklist"][1]["due_at"] is None
    assert client.post(u + "/todos", json={"text": ""}, headers=H).status_code == 400
    t = client.patch(f"{u}/todos/{item['id']}", json={"done": True}, headers=H).json()
    assert t["checklist"][0]["done"] is True and t["next_action"]["label"] == "submission due"
    assert client.patch(f"{u}/todos/99999", json={"done": True}, headers=H).status_code == 404
    t = client.delete(f"{u}/todos/{item['id']}", headers=H).json()
    assert [i["text"] for i in t["checklist"]] == ["video"]
    assert client.delete(f"{u}/todos/{item['id']}", headers=H).status_code == 404

    assert client.delete(u, headers=H).status_code == 204
    assert client.get("/api/tracked").json() == []
    assert client.delete(u, headers=H).status_code == 404


def test_tracking_404_when_not_tracked(client):
    u = "/api/tracked/devpost:gamma"
    for method, path, body in [
        ("PATCH", u, {"status": "registered"}),
        ("DELETE", u, None),
        ("POST", u + "/notes", {"text": "x"}),
        ("POST", u + "/team", {"name": "x"}),
        ("DELETE", u + "/team/x", None),
        ("POST", u + "/todos", {"text": "x"}),
        ("PATCH", u + "/todos/1", {"done": True}),
        ("DELETE", u + "/todos/1", None),
    ]:
        r = client.request(method, path, json=body, headers=H)
        assert r.status_code == 404, (method, path)
        assert "detail" in r.json()


def test_tracked_list_sorted_and_filtered(client):
    for uid, status in [("devpost:gamma", "interested"), ("unstop:1794479", "interested"),
                        ("hack2skill:h1", "won"), ("devpost:old", "submitted")]:
        assert client.post("/api/tracked", json={"uid": uid, "status": status}, headers=H).status_code == 201
    r = client.get("/api/tracked")
    # by next action, nulls (terminal / passed) last; terminal statuses included
    assert [t["uid"] for t in r.json()][:2] == ["unstop:1794479", "devpost:gamma"]
    assert {t["uid"] for t in r.json()[2:]} == {"hack2skill:h1", "devpost:old"}
    assert all(t["next_action"] is None for t in r.json()[2:])
    r = client.get("/api/tracked?status=won&status=submitted")
    assert {t["uid"] for t in r.json()} == {"hack2skill:h1", "devpost:old"}
    assert client.get("/api/tracked?status=bogus").status_code == 422


def test_tracked_next_action_uses_best_record_and_survives_listing_loss(client, db):
    t = client.post("/api/tracked", json={"uid": "luma:evt-alpha"}, headers=H).json()
    # Luma's own (approximate) registration close is day 10; Devfolio's exact one is day 5
    assert t["next_action"] == {"label": "registration closes", "at": iso(d(5)), "approximate": False}
    assert t["hackathon"]["uid"] == "luma:evt-alpha"
    s = Store(db)
    s.db.execute("DELETE FROM hackathons WHERE uid = 'luma:evt-alpha'")
    s.db.commit()
    s.close()
    [t] = client.get("/api/tracked").json()
    assert t["hackathon"] is None and t["next_action"] is None


# --- searches & prefs -------------------------------------------------------------


def test_searches_crud(client):
    assert client.get("/api/searches").json() == []
    r = client.put("/api/searches/AI-Online", json={"filter": {"modes": ["online"], "themes": ["AI"]}}, headers=H)
    assert r.status_code == 200
    assert r.json() == {"name": "ai-online", "filter": {"modes": ["online"], "themes": ["AI"]},
                        "description": "online · themes AI", "is_default": False}
    assert client.put("/api/searches/bad name!", json={"filter": {"modes": ["online"]}}, headers=H).status_code == 400
    assert client.put("/api/searches/empty", json={"filter": {}}, headers=H).status_code == 400
    assert client.put("/api/searches/x", json={"filter": {"colour": "red"}}, headers=H).status_code == 400
    assert client.put("/api/searches/x", json={"filter": {"modes": ["moon"]}}, headers=H).status_code == 400
    client.put("/api/searches/big", json={"filter": {"min_prize_usd": 1000}}, headers=H)
    client.put("/api/prefs/default_search", json={"value": "big"}, headers=H)
    lst = client.get("/api/searches").json()
    assert [(s["name"], s["is_default"]) for s in lst] == [("ai-online", False), ("big", True)]

    assert client.delete("/api/searches/big", headers=H).status_code == 204
    assert client.get("/api/prefs").json()["default_search"]["value"] is None  # default unset too
    assert client.delete("/api/searches/big", headers=H).status_code == 404
    assert client.delete("/api/searches/ai-online", headers=H).status_code == 204
    assert client.get("/api/searches").json() == []


def test_prefs(client):
    r = client.get("/api/prefs")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"tz", "luma_near", "default_search"}
    assert body["tz"]["value"] is None and body["tz"]["description"]

    assert client.put("/api/prefs/tz", json={"value": "Asia/Kolkata"}, headers=H).status_code == 200
    assert client.put("/api/prefs/tz", json={"value": "Mars/Base"}, headers=H).status_code == 400
    assert client.put("/api/prefs/luma_near", json={"value": "bengaluru; atlantis"}, headers=H).status_code == 400
    r = client.put("/api/prefs/luma_near", json={"value": " bengaluru ;  12.9,77.6 "}, headers=H)
    assert r.status_code == 200 and r.json()["value"] == "bengaluru;12.9,77.6"
    assert client.put("/api/prefs/default_search", json={"value": "nope"}, headers=H).status_code == 400
    assert client.put("/api/prefs/colour", json={"value": "red"}, headers=H).status_code == 404
    body = client.get("/api/prefs").json()
    assert body["tz"]["value"] == "Asia/Kolkata" and body["luma_near"]["value"] == "bengaluru;12.9,77.6"

    assert client.delete("/api/prefs/tz", headers=H).status_code == 204
    assert client.delete("/api/prefs/tz", headers=H).status_code == 204
    assert client.delete("/api/prefs/colour", headers=H).status_code == 404
    assert client.get("/api/prefs").json()["tz"]["value"] is None


# --- sync -----------------------------------------------------------------------


def test_sync_start_conflict_and_status(client, monkeypatch, db):
    started, release = threading.Event(), threading.Event()
    calls = []

    def fake_sync(store, sources=None, *, http=None, options=None):
        calls.append(options)
        run = store.start_run(Source.DEVPOST)
        started.set()
        release.wait(5)
        store.finish_run(run, fetched=3, result=None, error=None, enriched=1)
        return []

    monkeypatch.setattr(logr.sync, "sync", fake_sync)
    client.put("/api/prefs/luma_near", json={"value": "bengaluru;delhi"}, headers=H)
    assert client.get("/api/sync/status").json() == {"runs": [], "running": False}

    assert client.post("/api/sync").status_code == 403  # CSRF
    r = client.post("/api/sync", headers=H)
    assert r.status_code == 202 and r.json() == {"started": True}
    assert started.wait(5)
    assert client.get("/api/sync/status").json()["running"] is True
    r = client.post("/api/sync", headers=H)
    assert r.status_code == 409 and "detail" in r.json()
    release.set()
    wait_idle()
    status = client.get("/api/sync/status").json()
    assert status["running"] is False
    [run] = status["runs"]
    assert run["source"] == "devpost" and run["fetched"] == 3 and run["enriched"] == 1 and run["error"] is None
    assert run["started_at"].endswith("Z") and run["finished_at"].endswith("Z")
    assert set(run) == {"source", "started_at", "finished_at", "fetched", "created", "updated", "enriched",
                        "enrich_errors", "delisted", "error"}
    assert calls[0].connector_opts[Source.LUMA] == {"places": ["bengaluru", "delhi"]}


def test_sync_survives_a_crashing_sync(client, monkeypatch):
    done = threading.Event()

    def boom(store, sources=None, **kw):
        done.set()
        raise RuntimeError("network down")

    monkeypatch.setattr(logr.sync, "sync", boom)
    assert client.post("/api/sync", headers=H).status_code == 202
    assert done.wait(5)
    wait_idle()
    assert client.get("/api/sync/status").json()["running"] is False


# --- oauth mode -------------------------------------------------------------------


def test_oauth_anonymous(db, monkeypatch):
    monkeypatch.setattr(logr.sync, "sync", lambda *a, **kw: pytest.fail("sync must not run"))
    c = TestClient(create_app(db, auth=oauth_config()))
    assert c.get("/api/hackathons").status_code == 200  # browsing is public
    body = c.get("/api/hackathons/devfolio:alpha").json()
    assert body["tracking"] is None and body["tracked_status"] is None
    assert c.get("/api/sync/status").status_code == 200
    for method, path, body in [
        ("GET", "/api/tracked", None),
        ("POST", "/api/tracked", {"uid": "devpost:gamma"}),
        ("PATCH", "/api/tracked/devpost:gamma", {"status": "won"}),
        ("DELETE", "/api/tracked/devpost:gamma", None),
        ("POST", "/api/tracked/devpost:gamma/todos", {"text": "x"}),
        ("GET", "/api/searches", None),
        ("PUT", "/api/searches/x", {"filter": {"modes": ["online"]}}),
        ("DELETE", "/api/searches/x", None),
        ("GET", "/api/prefs", None),
        ("PUT", "/api/prefs/tz", {"value": "UTC"}),
        ("DELETE", "/api/prefs/tz", None),
        ("POST", "/api/sync", None),
    ]:
        r = c.request(method, path, json=body, headers=H)
        assert r.status_code == 401, (method, path)
        assert r.json() == {"detail": "login required"}
    assert c.get("/api/hackathons?saved=x").status_code == 401


def test_oauth_users_are_isolated_and_sync_needs_admin(db, monkeypatch):
    """Signed-in users (session resolution stubbed): own data, admin-only sync."""
    users = {"alice": User(id="github:1", provider="github", login="alice", name="Alice"),
             "bob": User(id="github:2", provider="github", login="bob", name="Bob")}
    monkeypatch.setattr(logr.auth, "_resolve_user", lambda request: users.get(request.headers.get("X-Test-User")))
    monkeypatch.setattr(logr.sync, "sync", lambda *a, **kw: [])
    c = TestClient(create_app(db, auth=oauth_config()))
    alice, bob = {**H, "X-Test-User": "alice"}, {**H, "X-Test-User": "bob"}

    assert c.post("/api/tracked", json={"uid": "devpost:gamma", "status": "registered"}, headers=alice).status_code == 201
    assert c.get("/api/tracked", headers=bob).json() == []
    assert c.get("/api/hackathons/devpost:gamma", headers=bob).json()["tracked_status"] is None
    assert c.get("/api/hackathons/devpost:gamma", headers=alice).json()["tracked_status"] == "registered"
    c.put("/api/searches/mine", json={"filter": {"modes": ["online"]}}, headers=alice)
    assert c.get("/api/searches", headers=bob).json() == []
    assert c.get("/api/hackathons?saved=mine", headers=bob).status_code == 404

    assert c.post("/api/sync", headers=bob).status_code == 403
    assert c.post("/api/sync", headers=alice).status_code == 202
    wait_idle()
