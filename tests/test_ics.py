"""iCalendar feed (logr.ics): RFC 5545 output, tracked events, feed tokens."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from logr.ics import PRODID, FeedTokens, IcsEvent, build_calendar, tracked_events
from logr.models import Hackathon, Mode, Round, Source
from logr.store import Store
from logr.tracking import Status, Tracker

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)


def d(days: float) -> datetime:
    return NOW + timedelta(days=days)


# --- a tiny RFC 5545 reader, used to check what we write -------------------------


def unescape(v: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def parse(text: str) -> dict:
    """Unfold and parse into nested components:
    {"name": "VCALENDAR", "props": [(name, params, value)], "children": [...]}.
    Asserts the structural rules we rely on along the way."""
    assert text.endswith("\r\n")
    raw = text[:-2].split("\r\n")
    for line in raw:
        assert "\n" not in line and "\r" not in line
        assert len(line.encode("utf-8")) <= 75, line
    unfolded: list[str] = []
    for line in raw:
        if line.startswith((" ", "\t")):
            assert unfolded, "continuation line first"
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    root: dict | None = None
    stack: list[dict] = []
    for line in unfolded:
        m = re.match(r"^([A-Z0-9-]+)((?:;[^:]*)?):(.*)$", line)
        assert m, f"bad content line {line!r}"
        name, params, value = m.groups()
        if name == "BEGIN":
            comp = {"name": value, "props": [], "children": []}
            if stack:
                stack[-1]["children"].append(comp)
            else:
                assert root is None, "two top-level components"
                root = comp
            stack.append(comp)
        elif name == "END":
            assert stack and stack[-1]["name"] == value, f"END:{value} mismatched"
            stack.pop()
        else:
            assert stack, f"property outside a component: {line!r}"
            stack[-1]["props"].append((name, params.lstrip(";"), value))
    assert not stack and root is not None
    return root


def prop(comp: dict, name: str) -> str | None:
    vals = [v for n, _, v in comp["props"] if n == name]
    assert len(vals) <= 1, f"{name} repeated"
    return vals[0] if vals else None


def events_of(cal: dict) -> list[dict]:
    return [c for c in cal["children"] if c["name"] == "VEVENT"]


def check_rfc(cal: dict) -> None:
    assert cal["name"] == "VCALENDAR"
    assert prop(cal, "VERSION") == "2.0"
    assert prop(cal, "PRODID") == PRODID
    assert prop(cal, "CALSCALE") == "GREGORIAN"
    assert prop(cal, "METHOD") == "PUBLISH"
    utc = re.compile(r"^\d{8}T\d{6}Z$")
    uids = set()
    for ev in events_of(cal):
        for required in ("UID", "DTSTAMP", "DTSTART", "SUMMARY"):
            assert prop(ev, required) is not None, required
        for p in ("DTSTAMP", "DTSTART", "DTEND"):
            if prop(ev, p) is not None:
                assert utc.match(prop(ev, p)), prop(ev, p)
        # RFC 5545: DTEND, when present, MUST be later than DTSTART
        if prop(ev, "DTEND") is not None:
            assert prop(ev, "DTEND") > prop(ev, "DTSTART")
        assert prop(ev, "UID") not in uids
        uids.add(prop(ev, "UID"))
        for al in ev["children"]:
            assert al["name"] == "VALARM"
            assert prop(al, "ACTION") == "DISPLAY"
            assert prop(al, "DESCRIPTION")
            assert re.match(r"^-PT\d+M$", prop(al, "TRIGGER"))


# --- build_calendar ------------------------------------------------------------------


def test_calendar_skeleton_crlf_and_utc():
    ist = datetime(2026, 12, 4, 23, 59, tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Kolkata"))
    text = build_calendar([IcsEvent(uid="a@logr", summary="Deadline", start=ist)], name="Mine", now=NOW)
    assert text.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n")
    assert text.endswith("END:VCALENDAR\r\n")
    assert "\n" not in text.replace("\r\n", "")
    cal = parse(text)
    check_rfc(cal)
    assert prop(cal, "X-WR-CALNAME") == "Mine"
    assert "REFRESH-INTERVAL;VALUE=DURATION:PT1H" in text.split("\r\n")
    assert prop(cal, "X-PUBLISHED-TTL") == "PT1H"
    [ev] = events_of(cal)
    assert prop(ev, "DTSTART") == "20261204T182900Z"  # 23:59 IST
    assert prop(ev, "DTEND") is None  # an instant: no DTEND (RFC 5545 §3.6.1)
    assert prop(ev, "DTSTAMP") == "20261128T120000Z"
    assert prop(ev, "DESCRIPTION") is None and prop(ev, "URL") is None
    assert ev["children"] == []


def test_default_dtstamp_is_now():
    text = build_calendar([IcsEvent(uid="a", summary="x", start=NOW)])
    stamp = datetime.strptime(prop(events_of(parse(text))[0], "DTSTAMP"), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    assert abs(stamp - datetime.now(UTC)) < timedelta(minutes=1)


def test_empty_calendar_is_valid():
    cal = parse(build_calendar([], now=NOW))
    check_rfc(cal)
    assert events_of(cal) == []


def test_escaping_text_values():
    nasty = "a\\b; c, d\ne\r\nf"
    text = build_calendar([IcsEvent(uid="u;1,x@logr", summary=nasty, start=NOW, description=nasty,
                                    alarm_before=[10])], name="My, cal; x", now=NOW)
    assert r"SUMMARY:a\\b\; c\, d\ne\nf" in text
    cal = parse(text)
    [ev] = events_of(cal)
    assert unescape(prop(ev, "SUMMARY")) == "a\\b; c, d\ne\nf"
    assert unescape(prop(ev, "DESCRIPTION")) == "a\\b; c, d\ne\nf"
    assert unescape(prop(ev, "UID")) == "u;1,x@logr"
    assert unescape(prop(cal, "X-WR-CALNAME")) == "My, cal; x"
    assert unescape(prop(ev["children"][0], "DESCRIPTION")) == "a\\b; c, d\ne\nf"


def test_url_is_not_text_escaped_but_stripped_of_breaks():
    text = build_calendar([IcsEvent(uid="u", summary="s", start=NOW, url="https://x.io/a,b;c\r\nX-EVIL:1")], now=NOW)
    [ev] = events_of(parse(text))
    assert prop(ev, "URL") == "https://x.io/a,b;cX-EVIL:1"
    assert prop(ev, "X-EVIL") is None


def test_folding_ascii():
    summary = "S" * 200
    text = build_calendar([IcsEvent(uid="u", summary=summary, start=NOW)], now=NOW)
    lines = text.split("\r\n")
    i = next(i for i, ln in enumerate(lines) if ln.startswith("SUMMARY:"))
    assert len(lines[i]) == 75 and lines[i + 1].startswith(" ") and len(lines[i + 1]) == 75
    assert all(len(ln.encode()) <= 75 for ln in lines)
    assert unescape(prop(events_of(parse(text))[0], "SUMMARY")) == summary


def test_exactly_75_octets_is_not_folded():
    summary = "x" * (75 - len("SUMMARY:"))
    text = build_calendar([IcsEvent(uid="u", summary=summary, start=NOW)], now=NOW)
    assert f"SUMMARY:{summary}\r\nDTSTART" not in text  # order: SUMMARY comes after DTSTART
    assert f"\r\nSUMMARY:{summary}\r\n" in text


@pytest.mark.parametrize("chunk", ["⏰", "é", "₹", "👩‍💻", "日本語"])
def test_folding_never_splits_multibyte(chunk):
    for pad in range(4):  # shift the boundary through every byte offset
        summary = "a" * pad + chunk * 60
        text = build_calendar([IcsEvent(uid="u", summary=summary, start=NOW, description=summary)], now=NOW)
        raw = text.encode("utf-8")
        for line in raw.split(b"\r\n"):
            assert len(line) <= 75
            line.decode("utf-8")  # each physical line is valid UTF-8 on its own
        [ev] = events_of(parse(text))
        assert unescape(prop(ev, "SUMMARY")) == summary
        assert unescape(prop(ev, "DESCRIPTION")) == summary


def test_alarms_and_explicit_end():
    text = build_calendar([
        IcsEvent(uid="dl", summary="Deadline", start=d(2), alarm_before=[180, 1440]),
        IcsEvent(uid="ev", summary="Event", start=d(1), end=d(3)),
    ], now=NOW)
    cal = parse(text)
    check_rfc(cal)
    ev, dl = events_of(cal)  # sorted by start
    assert prop(ev, "UID") == "ev" and prop(ev, "DTEND") == "20261201T120000Z"
    assert ev["children"] == []
    assert [prop(a, "TRIGGER") for a in dl["children"]] == ["-PT1440M", "-PT180M"]
    assert all(prop(a, "DESCRIPTION") == "Deadline" for a in dl["children"])


def test_end_before_start_is_clamped():
    [ev] = events_of(parse(build_calendar([IcsEvent(uid="u", summary="s", start=d(2), end=d(1))], now=NOW)))
    assert prop(ev, "DTEND") is None  # clamped to an instant at DTSTART


def test_sorted_by_start_then_uid():
    evs = [IcsEvent(uid="b", summary="s", start=d(1)), IcsEvent(uid="a", summary="s", start=d(1)),
           IcsEvent(uid="c", summary="s", start=d(0))]
    assert [prop(e, "UID") for e in events_of(parse(build_calendar(evs, now=NOW)))] == ["c", "a", "b"]


# --- tracked_events -------------------------------------------------------------------


def seed() -> list[Hackathon]:
    return [
        # merged group: Devfolio (precise) + Luma (approximate registration close)
        Hackathon(source=Source.DEVFOLIO, source_id="alpha", title="Alpha Quantum AI Hackathon",
                  url="https://devfolio.co/alpha", mode=Mode.ONLINE, registration_closes_at=d(5),
                  starts_at=d(10), ends_at=d(12),
                  rounds=[Round(name="Idea Submission", starts_at=d(1), ends_at=d(7)),
                          Round(name="Prototype Submission", starts_at=d(8), ends_at=d(11))]),
        Hackathon(source=Source.LUMA, source_id="evt-alpha", title="Alpha Quantum AI Hackathon",
                  url="https://lu.ma/alpha", mode=Mode.OFFLINE, location="Bengaluru",
                  registration_closes_at=d(10), starts_at=d(10), approx_fields=["registration_closes_at"]),
        Hackathon(source=Source.UNSTOP, source_id="1794479", title="Beta, Builders; Challenge",
                  url="https://unstop.com/beta", registration_closes_at=d(-10), submission_deadline=d(-40),
                  starts_at=d(3)),
        Hackathon(source=Source.DEVPOST, source_id="gamma", title="Gamma Web Jam", url="https://devpost.com/gamma",
                  submission_deadline=d(30), approx_fields=["submission_deadline"]),
        Hackathon(source=Source.DEVPOST, source_id="won", title="Won Already", url="https://devpost.com/won",
                  submission_deadline=d(3), starts_at=d(1)),
        Hackathon(source=Source.DEVPOST, source_id="old", title="Long Gone", url="https://devpost.com/old",
                  starts_at=d(-50), ends_at=d(-40)),
        Hackathon(source=Source.HACK2SKILL, source_id="h1", title="Delta Robotics Sprint",
                  url="https://hack2skill.com/h1", registration_closes_at=d(15)),
    ]


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    s.upsert_many(seed())
    yield s
    s.close()


def by_uid(events: list[IcsEvent]) -> dict[str, IcsEvent]:
    out = {e.uid: e for e in events}
    assert len(out) == len(events), "duplicate uids"
    return out


def test_untracked_user_has_no_events(store):
    assert tracked_events(store, "local", NOW) == []


def test_tracked_events_content(store):
    tr = Tracker(store.db, "local")
    tr.track("luma:evt-alpha", Status.INTERESTED)  # tracked via the Luma copy → Devfolio dates
    tr.track("unstop:1794479", Status.INTERESTED)
    tr.track("devpost:gamma", Status.REGISTERED)
    tr.track("devpost:won", Status.WON)
    tr.track("devpost:old", Status.INTERESTED)
    tr.add_todo("devpost:gamma", "record demo, upload", due_at=d(4))
    done = tr.add_todo("devpost:gamma", "write README", due_at=d(2))
    tr.set_todo_done("devpost:gamma", done.id)
    tr.add_todo("devpost:gamma", "no due date")
    tr.add_todo("devpost:gamma", "ancient todo", due_at=d(-31))
    events = tracked_events(store, "local", NOW)
    ev = by_uid(events)

    # best record of the merged group: Devfolio's exact registration close, not Luma's approx one
    reg = ev["luma:evt-alpha/registration-closes@logr"]
    assert reg.start == d(5) and reg.end is None
    assert reg.summary == "⏰ Alpha Quantum AI Hackathon — registration closes"
    assert reg.alarm_before == [1440, 180]
    assert "Status: interested" in reg.description and "https://devfolio.co/alpha" in reg.description
    assert reg.url == "https://devfolio.co/alpha"
    assert ev["luma:evt-alpha/idea-submission-ends@logr"].start == d(7)
    start = ev["luma:evt-alpha/event@logr"]
    assert (start.summary, start.start, start.end, start.alarm_before) == (
        "🏁 Alpha Quantum AI Hackathon", d(10), d(12), None)

    # 30-day lookback: registration closed 10 days ago is still there, 40 days ago isn't
    assert ev["unstop:1794479/registration-closes@logr"].start == d(-10)
    assert "unstop:1794479/submission-due@logr" not in ev
    beta = ev["unstop:1794479/event@logr"]
    assert beta.end == d(3) + timedelta(hours=1)  # no ends_at → 1-hour event

    # status-aware: registered → no "registration closes"; approximate marker
    gamma = [e for e in events if e.uid.startswith("devpost:gamma/")]
    sub = ev["devpost:gamma/submission-due@logr"]
    assert sub.summary == "⏰ Gamma Web Jam — submission due (approx.)"
    assert "Status: registered" in sub.description and "approximate" in sub.description
    todos = [e for e in gamma if "/todo-" in e.uid]
    assert [t.summary for t in todos] == ["✅ Gamma Web Jam: record demo, upload"]
    assert todos[0].start == d(4) and todos[0].alarm_before == [1440, 180]

    # terminal statuses contribute nothing; ancient events fall outside the window
    assert not [e for e in events if e.uid.startswith(("devpost:won/", "devpost:old/"))]
    assert not [e for e in events if "delta" in e.summary.lower()]  # not tracked

    # the whole thing is a valid calendar
    cal = parse(build_calendar(events, now=NOW))
    check_rfc(cal)
    assert len(events_of(cal)) == len(events)


def test_submitted_only_later_rounds(store):
    tr = Tracker(store.db, "local")
    tr.track("devfolio:alpha", Status.SUBMITTED)
    # two days in, the idea round has started (and was submitted): only the prototype round
    # is left, even though the 30-day lookback reaches back before the idea round started
    labels = sorted(e.uid for e in tracked_events(store, "local", d(2)))
    assert labels == ["devfolio:alpha/event@logr", "devfolio:alpha/prototype-submission-ends@logr"]


def test_uids_stable_across_regenerations(store):
    tr = Tracker(store.db, "local")
    tr.track("devfolio:alpha")
    tr.add_todo("devfolio:alpha", "pitch", due_at=d(3))
    first = [e.uid for e in tracked_events(store, "local", NOW)]
    tr.add_todo("devfolio:alpha", "another", due_at=d(1))  # adding an item doesn't renumber others
    second = [e.uid for e in tracked_events(store, "local", NOW + timedelta(hours=2))]
    assert set(first) <= set(second) and len(second) == len(first) + 1
    a = build_calendar(tracked_events(store, "local", NOW), now=NOW)
    b = build_calendar(tracked_events(store, "local", NOW), now=NOW)
    assert a == b


def test_tracked_hackathon_missing_from_store_keeps_todos(store):
    tr = Tracker(store.db, "local")
    tr.track("devpost:gamma")
    tr.add_todo("devpost:gamma", "ship", due_at=d(1))
    store.db.execute("DELETE FROM hackathons WHERE uid = 'devpost:gamma'")
    store.db.commit()
    [ev] = tracked_events(store, "local", NOW)
    assert ev.summary == "✅ devpost:gamma: ship" and ev.url is None


def test_user_isolation(store):
    Tracker(store.db, "github:1").track("devpost:gamma")
    Tracker(store.db, "github:2").track("hack2skill:h1")
    a = tracked_events(store, "github:1", NOW)
    b = tracked_events(store, "github:2", NOW)
    assert {e.uid.split("/")[0] for e in a} == {"devpost:gamma"}
    assert {e.uid.split("/")[0] for e in b} == {"hack2skill:h1"}
    assert tracked_events(store, "local", NOW) == []


# --- FeedTokens ------------------------------------------------------------------------


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    yield conn
    conn.close()


def test_feed_tokens_create_lookup_rotate(db):
    ft = FeedTokens(db)
    t1 = ft.get_or_create("alice")
    assert len(t1) >= 43 and re.fullmatch(r"[A-Za-z0-9_-]+", t1)
    assert ft.get_or_create("alice") == t1  # stable
    assert ft.user_for(t1) == "alice"
    t2 = ft.rotate("alice")
    assert t2 != t1
    assert ft.user_for(t1) is None and ft.user_for(t2) == "alice"
    assert ft.get_or_create("alice") == t2
    assert FeedTokens(db).user_for(t2) == "alice"  # re-opening keeps the table


def test_feed_tokens_rotate_without_existing(db):
    ft = FeedTokens(db)
    t = ft.rotate("bob")
    assert ft.user_for(t) == "bob" and ft.get_or_create("bob") == t


def test_feed_tokens_isolated_per_user(db):
    ft = FeedTokens(db)
    a, b = ft.get_or_create("alice"), ft.get_or_create("bob")
    assert a != b
    assert (ft.user_for(a), ft.user_for(b)) == ("alice", "bob")
    ft.rotate("alice")
    assert ft.user_for(b) == "bob"


def test_feed_tokens_reject_short_or_empty(db):
    ft = FeedTokens(db)
    ft.get_or_create("alice")
    db.execute("INSERT INTO feed_tokens (user_id, token, created_at) VALUES ('eve', 'short', 'x')")
    db.commit()
    assert ft.user_for("short") is None  # rejected before querying
    assert ft.user_for("") is None
    assert ft.user_for(None) is None  # type: ignore[arg-type]
    assert ft.user_for("x" * 43) is None
