"""End-to-end CLI tests for Phase 3: tracking, saved searches, preferences."""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from logr import cli
from logr.models import Hackathon, Mode, Source
from logr.searches import Filter, Preferences
from logr.store import Store
from logr.tracking import Status, Tracker

NOW = datetime.now(timezone.utc)


def hk(source, sid, title, **kw):
    return Hackathon(source=source, source_id=sid, title=title, url=f"https://{sid}.example", **kw)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("LOGR_TZ", "LOGR_LUMA_NEAR"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "t.db"
    s = Store(path)
    s.upsert_many([
        hk(Source.DEVFOLIO, "ai", "AI Sprint", mode=Mode.OFFLINE, location="Bangalore, Karnataka",
           themes=["Artificial Intelligence", "Health"], prize="$5,000", team_size_min=2, team_size_max=4,
           registration_closes_at=NOW + timedelta(days=2), submission_deadline=NOW + timedelta(days=9)),
        hk(Source.UNSTOP, "web3", "Chain Jam", mode=Mode.ONLINE, themes=["Web3"], prize="₹50,000",
           team_size_max=1, registration_closes_at=NOW + timedelta(days=5)),
        hk(Source.DEVPOST, "big", "Mega Build", mode=Mode.HYBRID, location="Mumbai", prize="$100,000",
           submission_deadline=NOW + timedelta(days=20)),
        hk(Source.HACK2SKILL, "nodate", "Someday Hack", mode=Mode.ONLINE),
    ])
    s.close()
    return path


def run(db, *argv, tz=None):
    pre = ["--db", str(db)] + (["--tz", tz] if tz else [])
    return cli.main([*pre, *argv])


def tracker(db):
    return Tracker(Store(db).db)


# --- track / untrack / mark ---------------------------------------------------


def test_track_untrack_mark(db, capsys):
    assert run(db, "track", "devfolio:ai") == 0
    out = capsys.readouterr().out
    assert "AI Sprint" in out and "interested" in out

    assert run(db, "track", "devfolio:ai", "--status", "registered") == 0  # idempotent
    assert "Already tracking" in capsys.readouterr().out
    assert tracker(db).get("devfolio:ai").status is Status.INTERESTED

    assert run(db, "mark", "devfolio:ai", "registered", "--note", "paid fee") == 0
    assert "interested → registered" in capsys.readouterr().out
    hist = tracker(db).history("devfolio:ai")
    assert [(c.old, c.new, c.note) for c in hist] == [
        (None, Status.INTERESTED, None), (Status.INTERESTED, Status.REGISTERED, "paid fee")]

    assert run(db, "track", "unstop:web3", "--status", "team_formed") == 0
    assert tracker(db).get("unstop:web3").status is Status.TEAM_FORMED

    assert run(db, "untrack", "devfolio:ai") == 0
    assert tracker(db).get("devfolio:ai") is None


def test_tracking_errors(db, capsys):
    assert run(db, "track", "nope:1") == 1
    assert "nope:1" in capsys.readouterr().err
    assert run(db, "untrack", "devfolio:ai") == 1
    assert run(db, "mark", "devfolio:ai", "won") == 1
    assert "not tracking" in capsys.readouterr().err
    assert run(db, "note", "devfolio:ai", "hi") == 1
    assert run(db, "todo", "devfolio:ai", "add", "x") == 1
    with pytest.raises(SystemExit) as exc:
        run(db, "mark", "devfolio:ai", "bogus")
    assert exc.value.code == 2


# --- mine ---------------------------------------------------------------------


def test_mine_sorting_hiding_and_missing_listing(db, capsys):
    assert run(db, "mine") == 0
    assert "logr track" in capsys.readouterr().out  # empty hint

    run(db, "track", "devpost:big")      # submission in 20d
    run(db, "track", "hack2skill:nodate")  # no deadline → last
    run(db, "track", "devfolio:ai")      # registration in 2d → first
    run(db, "track", "unstop:web3")
    run(db, "mark", "unstop:web3", "lost")
    capsys.readouterr()

    assert run(db, "mine") == 0
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.startswith(("interested", "lost"))]
    assert [ln.split()[-2] for ln in lines] == ["AI", "Mega", "Someday"]
    assert "registration closes" in lines[0]
    assert "Chain Jam" not in out and "1 finished hidden" in out

    assert run(db, "mine", "--all") == 0
    assert "Chain Jam" in capsys.readouterr().out
    assert run(db, "mine", "--status", "lost") == 0
    out = capsys.readouterr().out
    assert "Chain Jam" in out and "AI Sprint" not in out

    # a registered hackathon's next action skips "registration closes"
    run(db, "mark", "devfolio:ai", "registered")
    capsys.readouterr()
    run(db, "mine")
    ai_line = next(ln for ln in capsys.readouterr().out.splitlines() if "AI Sprint" in ln)
    assert "submission due" in ai_line

    # listing vanished from the store: title falls back to the uid, todos still count
    s = Store(db)
    s.db.execute("DELETE FROM hackathons WHERE uid = 'devpost:big'")
    s.db.commit()
    s.close()
    run(db, "todo", "devpost:big", "add", "write", "post-mortem", "--due", (NOW + timedelta(days=1)).strftime("%Y-%m-%d"),
        tz="UTC")
    capsys.readouterr()
    assert run(db, "mine") == 0
    rows = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith(("interested", "registered"))]
    assert rows[0].endswith("devpost:big") and "todo: write post-mortem" in rows[0]


# --- notes, repo, team, todos -------------------------------------------------


def test_note_repo_team_todo(db, capsys):
    run(db, "track", "devfolio:ai")
    assert run(db, "note", "devfolio:ai", "met", "the", "mentors") == 0
    assert run(db, "repo", "devfolio:ai", "https://github.com/me/ai") == 0
    assert run(db, "team", "devfolio:ai", "add", "Asha", "Rao") == 0
    assert run(db, "team", "devfolio:ai", "add", "Ben") == 0
    assert run(db, "team", "devfolio:ai", "rm", "asha rao") == 0
    assert run(db, "todo", "devfolio:ai", "add", "Pitch", "deck") == 0
    out = capsys.readouterr().out
    assert "Added #" in out
    t = tracker(db).get("devfolio:ai")
    assert t.notes.endswith("] met the mentors")
    assert t.repo_url == "https://github.com/me/ai"
    assert t.team == ["Ben"]
    item = t.checklist[0]
    assert (item.text, item.done, item.due_at) == ("Pitch deck", False, None)

    assert run(db, "todo", "devfolio:ai", "done", str(item.id)) == 0
    assert tracker(db).get("devfolio:ai").checklist[0].done
    assert run(db, "todo", "devfolio:ai", "undo", str(item.id)) == 0
    assert not tracker(db).get("devfolio:ai").checklist[0].done
    assert run(db, "todo", "devfolio:ai", "rm", str(item.id)) == 0
    assert tracker(db).get("devfolio:ai").checklist == []

    assert run(db, "repo", "devfolio:ai", "--clear") == 0
    assert tracker(db).get("devfolio:ai").repo_url is None

    capsys.readouterr()
    assert run(db, "todo", "devfolio:ai", "done", "9999") == 1
    assert "#9999" in capsys.readouterr().err
    assert run(db, "team", "devfolio:ai", "rm", "Nobody") == 1
    assert "Nobody" in capsys.readouterr().err
    assert run(db, "team", "devfolio:ai", "add", " ") == 1
    assert run(db, "repo", "devfolio:ai") == 2  # neither URL nor --clear


def test_todo_due_parsing_in_display_tz(db, capsys):
    run(db, "track", "devfolio:ai")
    year = NOW.year + 1
    assert run(db, "todo", "devfolio:ai", "add", "a", "--due", f"{year}-01-15", tz="Asia/Kolkata") == 0
    assert run(db, "todo", "devfolio:ai", "add", "b", "--due", f"{year}-01-15 09:30", tz="Asia/Kolkata") == 0
    assert run(db, "todo", "devfolio:ai", "add", "c", "--due", f"{year}-01-15 09:30", tz="America/New_York") == 0
    dues = [it.due_at for it in tracker(db).get("devfolio:ai").checklist]
    assert dues == [
        datetime(year, 1, 15, 18, 29, tzinfo=timezone.utc),  # 23:59 IST
        datetime(year, 1, 15, 4, 0, tzinfo=timezone.utc),
        datetime(year, 1, 15, 14, 30, tzinfo=timezone.utc),
    ]
    capsys.readouterr()
    assert run(db, "todo", "devfolio:ai", "add", "d", "--due", "next tuesday") == 2
    assert "--due" in capsys.readouterr().err


# --- show ---------------------------------------------------------------------


def test_show_tracking_section(db, capsys):
    assert run(db, "show", "devfolio:ai") == 0
    assert "Tracking" not in capsys.readouterr().out

    run(db, "track", "devfolio:ai")
    run(db, "mark", "devfolio:ai", "registered", "--note", "confirmed")
    run(db, "team", "devfolio:ai", "add", "Ben")
    run(db, "repo", "devfolio:ai", "https://github.com/me/ai")
    run(db, "note", "devfolio:ai", "remember", "the", "API", "key")
    run(db, "todo", "devfolio:ai", "add", "Slides", "--due", f"{NOW.year + 1}-01-01", tz="UTC")
    run(db, "todo", "devfolio:ai", "add", "Video")
    item_id = tracker(db).get("devfolio:ai").checklist[1].id
    run(db, "todo", "devfolio:ai", "done", str(item_id))
    capsys.readouterr()

    assert run(db, "show", "devfolio:ai", tz="UTC") == 0
    out = capsys.readouterr().out
    section = out[out.index("Tracking:"):]
    assert "registered" in section
    assert "Next action" in section and "submission due" in section
    assert "Ben" in section and "https://github.com/me/ai" in section
    assert "[ ] Slides" in section and f"due 01 Jan {NOW.year + 1} 23:59" in section
    assert f"#{item_id}" in section and "[x] Video" in section
    assert "remember the API key" in section
    assert "— → interested" in section and "interested → registered  (confirmed)" in section


# --- list: tracked marker, new filters ----------------------------------------


def test_list_marks_tracked_and_json_status(db, tmp_path, capsys):
    run(db, "track", "devfolio:ai", "--status", "registered")
    capsys.readouterr()
    assert run(db, "list") == 0
    out = capsys.readouterr().out
    assert "★ AI Sprint" in out and "★ Chain Jam" not in out
    assert run(db, "list", "-f", "json") == 0
    by_uid = {r["uid"]: r["tracked_status"] for r in json.loads(capsys.readouterr().out)}
    assert by_uid["devfolio:ai"] == "registered" and by_uid["unstop:web3"] is None

    # tracking a secondary listing of a merged hackathon marks the merged row
    db2 = tmp_path / "m.db"
    s = Store(db2)
    start = NOW + timedelta(days=10)
    s.upsert_many([
        hk(Source.DEVFOLIO, "d", "ETHIndia 2026", starts_at=start, registration_closes_at=start - timedelta(days=5)),
        hk(Source.LUMA, "l", "ETHIndia", starts_at=start, registration_closes_at=start,
           approx_fields=["registration_closes_at"]),
    ])
    s.close()
    run(db2, "track", "luma:l")
    capsys.readouterr()
    assert run(db2, "list", "-f", "json") == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1 and rows[0]["uid"] == "devfolio:d" and rows[0]["tracked_status"] == "interested"


def _uids(db, capsys, *argv):
    assert run(db, "list", "-f", "json", *argv) == 0
    return {r["uid"] for r in json.loads(capsys.readouterr().out)}


def test_list_new_filters(db, capsys):
    assert _uids(db, capsys, "--theme", "intelligence") == {"devfolio:ai"}
    assert _uids(db, capsys, "--theme", "web3", "--theme", "health") == {"devfolio:ai", "unstop:web3"}
    assert _uids(db, capsys, "--region", "bengaluru") == {"devfolio:ai"}
    assert _uids(db, capsys, "--region", "bombay") == {"devpost:big"}
    assert _uids(db, capsys, "--min-prize", "1000") == {"devfolio:ai", "devpost:big"}
    assert _uids(db, capsys, "--min-prize", "50000") == {"devpost:big"}
    # solo: "ai" needs 2+, "web3" allows max 1, the rest have unknown bounds
    assert _uids(db, capsys, "--team-size", "1") == {"unstop:web3", "devpost:big", "hack2skill:nodate"}
    assert _uids(db, capsys, "--team-size", "3") == {"devfolio:ai", "devpost:big", "hack2skill:nodate"}


def test_select_accepts_filter():
    items = [hk(Source.DEVFOLIO, "a", "A", mode=Mode.ONLINE, registration_closes_at=NOW + timedelta(days=1)),
             hk(Source.DEVFOLIO, "b", "B", mode=Mode.OFFLINE, registration_closes_at=NOW + timedelta(days=30))]
    base = Filter(modes=[Mode.OFFLINE])
    assert [lst.primary.source_id for lst in cli.select(items, now=NOW, filter=base)] == ["b"]
    # explicit kwargs override the filter
    assert [lst.primary.source_id for lst in cli.select(items, now=NOW, filter=base, modes=[Mode.ONLINE])] == ["a"]


# --- saved searches -----------------------------------------------------------


def test_search_save_ls_rm_and_list_saved(db, capsys):
    assert run(db, "search", "save", "Online", "-m", "online", "-m", "hybrid") == 0
    assert "Saved search 'online'" in capsys.readouterr().out
    assert run(db, "search", "save", "rich", "--min-prize", "1000", "-w", "30") == 0
    capsys.readouterr()
    assert run(db, "search", "ls") == 0
    out = capsys.readouterr().out
    assert out.index("online") < out.index("rich") and "online" in out and "hybrid" in out

    assert _uids(db, capsys, "--saved", "online") == {"unstop:web3", "devpost:big", "hack2skill:nodate"}
    # explicit flags override the saved criteria
    assert _uids(db, capsys, "--saved", "online", "-m", "offline") == {"devfolio:ai"}
    assert _uids(db, capsys, "--saved", "rich") == {"devfolio:ai", "devpost:big"}
    assert _uids(db, capsys, "--saved", "rich", "-w", "7") == {"devfolio:ai"}

    assert run(db, "list", "--saved", "nope") == 2
    assert "nope" in capsys.readouterr().err
    assert run(db, "search", "save", "empty") == 2
    assert run(db, "search", "save", "bad name!", "-m", "online") == 2
    assert run(db, "search", "rm", "rich") == 0
    assert run(db, "search", "rm", "rich") == 1
    capsys.readouterr()
    run(db, "search", "ls")
    assert "rich" not in capsys.readouterr().out


def test_default_search_pref(db, capsys):
    run(db, "search", "save", "offline", "-m", "offline")
    assert run(db, "prefs", "set", "default_search", "offline") == 0
    capsys.readouterr()
    assert _uids(db, capsys) == {"devfolio:ai"}
    assert _uids(db, capsys, "--no-prefs") == {"devfolio:ai", "unstop:web3", "devpost:big", "hack2skill:nodate"}
    assert _uids(db, capsys, "-m", "online") == {"unstop:web3", "hack2skill:nodate"}  # flags override the default
    run(db, "search", "save", "solo", "--team-size", "1")
    capsys.readouterr()
    assert _uids(db, capsys, "--saved", "solo") == {"unstop:web3", "devpost:big", "hack2skill:nodate"}

    assert run(db, "list") == 0
    assert "Saved search 'offline'" in capsys.readouterr().out

    # deleting the default search also clears the pref
    assert run(db, "search", "rm", "offline") == 0
    assert "default_search" in capsys.readouterr().out
    assert Preferences(Store(db).db).get("default_search") is None

    # a default pointing at a missing search is an error
    s = Store(db)
    Preferences(s.db).set("default_search", "gone")
    s.close()
    assert run(db, "list") == 2
    assert "gone" in capsys.readouterr().err
    assert run(db, "list", "--no-prefs") == 0


# --- preferences --------------------------------------------------------------


def test_prefs_set_unset_and_validation(db, capsys):
    assert run(db, "prefs") == 0
    out = capsys.readouterr().out
    for key in ("tz", "luma_near", "default_search"):
        assert key in out
    assert "(unset)" in out

    assert run(db, "prefs", "set", "tz", "Mars/Olympus") == 2
    assert "Mars/Olympus" in capsys.readouterr().err
    assert run(db, "prefs", "set", "default_search", "missing") == 2
    assert run(db, "prefs", "set", "luma_near", "bengaluru;atlantis") == 2
    assert "atlantis" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        run(db, "prefs", "set", "colour", "blue")

    assert run(db, "prefs", "set", "tz", "Asia/Kolkata") == 0
    assert run(db, "prefs", "set", "luma_near", "bengaluru; london") == 0
    capsys.readouterr()
    assert run(db, "prefs") == 0
    out = capsys.readouterr().out
    assert "Asia/Kolkata" in out and "bengaluru;london" in out

    assert run(db, "prefs", "unset", "tz") == 0
    assert Preferences(Store(db).db).get("tz") is None


def test_tz_precedence(db, capsys, monkeypatch):
    at = Store(db).get("devfolio:ai").registration_closes_at

    def shown(*pre):
        assert cli.main(["--db", str(db), *pre, "show", "devfolio:ai"]) == 0
        return capsys.readouterr().out

    def fmt(name):
        return at.astimezone(ZoneInfo(name)).strftime("%d %b %Y %H:%M")

    run(db, "prefs", "set", "tz", "Asia/Kolkata")
    assert fmt("Asia/Kolkata") in shown()                    # pref
    monkeypatch.setenv("LOGR_TZ", "America/New_York")
    assert fmt("America/New_York") in shown()                # env beats pref
    assert fmt("Europe/London") in shown("--tz", "Europe/London")  # flag beats env
    monkeypatch.delenv("LOGR_TZ")
    capsys.readouterr()
    assert run(db, "list") == 0
    assert "Times in Asia/Kolkata" in capsys.readouterr().out

    assert cli.main(["--db", str(db), "--tz", "Not/AZone", "list"]) == 2
    assert "Not/AZone" in capsys.readouterr().err


def test_sync_luma_places_precedence(db, monkeypatch):
    seen = []

    def fake_sync(store, sources, options):
        seen.append(options.connector_opts[Source.LUMA]["places"])
        return []

    monkeypatch.setattr(cli, "sync", fake_sync)
    run(db, "prefs", "set", "luma_near", "london;berlin")
    run(db, "sync")
    monkeypatch.setenv("LOGR_LUMA_NEAR", "delhi")
    run(db, "sync")
    run(db, "sync", "--luma-near", "mumbai")
    assert seen == [["london", "berlin"], ["delhi"], ["mumbai"]]


def test_mine_uses_best_record_of_merged_group(tmp_path, capsys):
    """Tracking the Luma copy of a hackathon also on Devfolio: deadlines come
    from Devfolio's exact record, not Luma's approximation."""
    from datetime import datetime, timedelta, timezone

    from logr.models import Hackathon, Source
    from logr.store import Store

    db = tmp_path / "m.db"
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=20)
    s = Store(db)
    s.upsert_many([
        Hackathon(source=Source.DEVFOLIO, source_id="d", title="ETHIndia 2027", url="https://d",
                  starts_at=start, registration_closes_at=start - timedelta(days=6)),
        Hackathon(source=Source.LUMA, source_id="l", title="ETHIndia", url="https://l",
                  starts_at=start, registration_closes_at=start, approx_fields=["registration_closes_at"]),
    ])
    s.close()
    from logr import cli

    assert cli.main(["--db", str(db), "--tz", "UTC", "track", "luma:l"]) == 0
    capsys.readouterr()
    assert cli.main(["--db", str(db), "--tz", "UTC", "mine"]) == 0
    out = capsys.readouterr().out
    expected = (start - timedelta(days=6)).strftime("%d %b %Y %H:%M")
    assert expected in out and "~" not in out.split("\n")[1]
