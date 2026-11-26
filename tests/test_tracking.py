import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from logr import tracking
from logr.models import Deadline, Hackathon, Round, Source
from logr.store import Store
from logr.tracking import (
    LOCAL_USER, PIPELINE, TERMINAL, ChecklistItem, Status, Tracked, Tracker, next_action, upcoming,
)

UTC = timezone.utc
T0 = datetime(2026, 11, 1, 12, tzinfo=UTC)
NOW = datetime(2026, 11, 25, 6, tzinfo=UTC)
UID = "unstop:1794479"


class Clock:
    """Deterministic replacement for ``utcnow``: each call advances a minute."""

    def __init__(self, start=T0):
        self.t = start - timedelta(minutes=1)

    def __call__(self):
        self.t += timedelta(minutes=1)
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(tracking, "utcnow", c)
    return c


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "logr.db")
    yield s
    s.close()


@pytest.fixture
def tr(store, clock):
    return Tracker(store.db)


def hk(**kw):
    return Hackathon(source=Source.UNSTOP, source_id="1794479", title="Hack", url="https://x.example", **kw)


# --- track / untrack / get / all --------------------------------------------


def test_track_creates_record_and_initial_history(tr):
    t = tr.track(UID)
    assert t.uid == UID and t.status is Status.INTERESTED
    assert t.added_at == t.updated_at == T0
    assert t.added_at.tzinfo is not None
    assert t.notes == "" and t.repo_url is None and t.team == [] and t.checklist == []
    [h] = tr.history(UID)
    assert h.old is None and h.new is Status.INTERESTED and h.changed_at == T0 and h.note is None


def test_track_is_idempotent(tr):
    first = tr.track(UID, Status.REGISTERED)
    again = tr.track(UID, Status.WON)  # status ignored when already tracked
    assert again == first and again.status is Status.REGISTERED
    assert len(tr.history(UID)) == 1
    assert len(tr.all()) == 1


def test_get_unknown_is_none(tr):
    assert tr.get("nope:1") is None
    assert tr.history("nope:1") == []


def test_untrack_cascades_and_retrack_starts_fresh(tr, store):
    tr.track(UID)
    tr.set_status(UID, Status.REGISTERED)
    tr.append_note(UID, "hi", now=NOW)
    tr.set_repo(UID, "https://github.com/a/b")
    tr.add_member(UID, "Asha")
    tr.add_todo(UID, "slides", NOW)
    assert tr.untrack(UID) is True
    assert tr.get(UID) is None and tr.history(UID) == []
    for table in ("tracked", "tracked_status_history", "tracked_team", "tracked_checklist"):
        assert store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert tr.untrack(UID) is False

    t = tr.track(UID)
    assert t.status is Status.INTERESTED and t.notes == "" and t.repo_url is None
    assert t.team == [] and t.checklist == []
    assert len(tr.history(UID)) == 1


def test_untrack_leaves_others_alone(tr):
    tr.track("a:1")
    tr.track("b:2")
    tr.add_member("b:2", "Ravi")
    tr.untrack("a:1")
    assert [t.uid for t in tr.all()] == ["b:2"]
    assert tr.get("b:2").team == ["Ravi"]


def test_all_ordering_and_status_filter(tr):
    tr.track("c:3")
    tr.track("a:1", Status.REGISTERED)
    tr.track("b:2")
    tr.set_status("b:2", Status.WON)
    assert [t.uid for t in tr.all()] == ["c:3", "a:1", "b:2"]  # oldest added first, not by uid
    assert [t.uid for t in tr.all([Status.INTERESTED])] == ["c:3"]
    assert [t.uid for t in tr.all(TERMINAL)] == ["b:2"]
    assert [t.uid for t in tr.all([Status.REGISTERED, Status.WON])] == ["a:1", "b:2"]
    assert tr.all([]) == []
    assert tr.all(None) == tr.all()


# --- status ---------------------------------------------------------------------


def test_set_status_records_history_in_order(tr):
    tr.track(UID)
    tr.set_status(UID, Status.REGISTERED, note="paid fee")
    t = tr.set_status(UID, Status.TEAM_FORMED)
    assert t.status is Status.TEAM_FORMED
    hist = tr.history(UID)
    assert [(h.old, h.new, h.note) for h in hist] == [
        (None, Status.INTERESTED, None),
        (Status.INTERESTED, Status.REGISTERED, "paid fee"),
        (Status.REGISTERED, Status.TEAM_FORMED, None),
    ]
    assert [h.changed_at for h in hist] == sorted(h.changed_at for h in hist)
    assert all(h.changed_at.tzinfo is not None for h in hist)


def test_set_status_any_direction(tr):
    tr.track(UID)
    tr.set_status(UID, Status.WITHDRAWN)
    t = tr.set_status(UID, Status.INTERESTED, note="oops")
    assert t.status is Status.INTERESTED and t.is_active
    assert tr.history(UID)[-1].old is Status.WITHDRAWN


def test_set_same_status_is_noop(tr):
    before = tr.track(UID)
    after = tr.set_status(UID, Status.INTERESTED, note="ignored")
    assert after == before  # updated_at not bumped either
    assert len(tr.history(UID)) == 1


def test_status_accepts_plain_strings(tr):
    tr.track(UID, "registered")
    assert tr.set_status(UID, "submitted").status is Status.SUBMITTED


def test_is_active():
    for s in PIPELINE:
        t = Tracked(uid=UID, status=s, added_at=T0, updated_at=T0)
        assert t.is_active is (s not in {Status.WON, Status.LOST, Status.WITHDRAWN})


# --- notes / repo / team ------------------------------------------------------------


def test_append_note_format(tr):
    tr.track(UID)
    t = tr.append_note(UID, "registered on site", now=datetime(2026, 11, 2, 9, 5, 59, tzinfo=UTC))
    assert t.notes == "[2026-11-02 09:05 UTC] registered on site"
    # non-UTC `now` is rendered in UTC
    ist = timezone(timedelta(hours=5, minutes=30))
    t = tr.append_note(UID, "team call", now=datetime(2026, 11, 3, 10, 0, tzinfo=ist))
    assert t.notes == "[2026-11-02 09:05 UTC] registered on site\n[2026-11-03 04:30 UTC] team call"


def test_append_note_defaults_to_utcnow(tr):
    tr.track(UID)  # clock reads 12:00
    t = tr.append_note(UID, "x")  # next clock read is 12:01
    assert t.notes == "[2026-11-01 12:01 UTC] x"


def test_set_repo_and_clear(tr):
    tr.track(UID)
    assert tr.set_repo(UID, "https://github.com/me/proj").repo_url == "https://github.com/me/proj"
    assert tr.set_repo(UID, None).repo_url is None


def test_team_add_dedupe_and_remove(tr):
    tr.track(UID)
    tr.add_member(UID, "  Asha ")
    tr.add_member(UID, "Ravi")
    t = tr.add_member(UID, "ASHA")  # case-insensitive duplicate ignored
    assert t.team == ["Asha", "Ravi"]
    with pytest.raises(ValueError):
        tr.add_member(UID, "   ")
    t = tr.remove_member(UID, "asha")
    assert t.team == ["Ravi"]
    with pytest.raises(ValueError):
        tr.remove_member(UID, "Asha")
    assert tr.get(UID).team == ["Ravi"]


# --- checklist -------------------------------------------------------------------


def test_todo_lifecycle(tr):
    tr.track(UID)
    a = tr.add_todo(UID, "  make slides ", datetime(2026, 12, 1, tzinfo=UTC))
    b = tr.add_todo(UID, "record demo")
    assert isinstance(a, ChecklistItem)
    assert a.text == "make slides" and a.done is False and a.due_at == datetime(2026, 12, 1, tzinfo=UTC)
    assert b.due_at is None and b.id != a.id
    assert [i.id for i in tr.get(UID).checklist] == [a.id, b.id]

    t = tr.set_todo_done(UID, a.id)
    assert [i.done for i in t.checklist] == [True, False]
    t = tr.set_todo_done(UID, a.id, done=False)
    assert [i.done for i in t.checklist] == [False, False]

    t = tr.remove_todo(UID, a.id)
    assert [i.id for i in t.checklist] == [b.id]
    with pytest.raises(KeyError):
        tr.remove_todo(UID, a.id)


def test_todo_due_converted_to_utc(tr):
    tr.track(UID)
    ist = timezone(timedelta(hours=5, minutes=30))
    item = tr.add_todo(UID, "x", datetime(2026, 12, 1, 5, 30, tzinfo=ist))
    assert item.due_at == datetime(2026, 12, 1, tzinfo=UTC) and item.due_at.tzinfo == UTC
    assert tr.get(UID).checklist[0].due_at.utcoffset() == timedelta(0)


def test_todo_validation(tr):
    tr.track(UID)
    with pytest.raises(ValueError):
        tr.add_todo(UID, "  ")
    with pytest.raises(ValueError):
        tr.add_todo(UID, "naive", datetime(2026, 12, 1))
    assert tr.get(UID).checklist == []


def test_todo_wrong_uid_is_keyerror(tr):
    tr.track("a:1")
    tr.track("b:2")
    item = tr.add_todo("a:1", "only a's")
    with pytest.raises(KeyError):
        tr.set_todo_done("b:2", item.id)
    with pytest.raises(KeyError):
        tr.remove_todo("b:2", item.id)
    with pytest.raises(KeyError):
        tr.set_todo_done("a:1", item.id + 999)
    assert tr.get("a:1").checklist[0].done is False


@pytest.mark.parametrize("call", [
    lambda tr: tr.set_status(UID, Status.REGISTERED),
    lambda tr: tr.append_note(UID, "x", now=NOW),
    lambda tr: tr.set_repo(UID, "https://x"),
    lambda tr: tr.add_member(UID, "Asha"),
    lambda tr: tr.remove_member(UID, "Asha"),
    lambda tr: tr.add_todo(UID, "x"),
    lambda tr: tr.set_todo_done(UID, 1),
    lambda tr: tr.remove_todo(UID, 1),
])
def test_mutations_on_untracked_raise_keyerror(tr, call):
    with pytest.raises(KeyError):
        call(tr)
    assert tr.get(UID) is None


# --- updated_at ------------------------------------------------------------------


def test_every_mutation_bumps_updated_at(tr):
    t = tr.track(UID)
    seen = [t.updated_at]

    def bumped(t):
        assert t.updated_at > seen[-1]
        assert t.added_at == T0
        seen.append(t.updated_at)

    bumped(tr.set_status(UID, Status.REGISTERED))
    bumped(tr.append_note(UID, "n", now=NOW))
    bumped(tr.set_repo(UID, "https://r"))
    bumped(tr.add_member(UID, "Asha"))
    bumped(tr.remove_member(UID, "Asha"))
    item = tr.add_todo(UID, "t")
    bumped(tr.get(UID))
    bumped(tr.set_todo_done(UID, item.id))
    bumped(tr.set_todo_done(UID, item.id, False))
    bumped(tr.remove_todo(UID, item.id))


# --- upcoming / next_action --------------------------------------------------------


@pytest.fixture
def listing():
    return hk(
        registration_closes_at=datetime(2026, 11, 28, tzinfo=UTC),
        submission_deadline=datetime(2026, 12, 10, tzinfo=UTC),
        rounds=[Round(name="Idea Submission", ends_at=datetime(2026, 11, 20, tzinfo=UTC)),  # past
                Round(name="Prototype Round", ends_at=datetime(2026, 12, 3, tzinfo=UTC))],
        approx_fields=["submission_deadline"],
    )


def _t(status, checklist=()):
    return Tracked(uid=UID, status=status, added_at=T0, updated_at=T0, checklist=list(checklist))


def test_upcoming_interested_includes_registration(listing):
    assert upcoming(listing, _t(Status.INTERESTED), NOW) == [
        Deadline("registration closes", datetime(2026, 11, 28, tzinfo=UTC), False),
        Deadline("Prototype Round ends", datetime(2026, 12, 3, tzinfo=UTC), False),
        Deadline("submission due", datetime(2026, 12, 10, tzinfo=UTC), True),
    ]


@pytest.mark.parametrize("status", [Status.REGISTERED, Status.TEAM_FORMED, Status.SUBMITTED, Status.SHORTLISTED])
def test_upcoming_registered_drops_registration(listing, status):
    assert [d.label for d in upcoming(listing, _t(status), NOW)] == ["Prototype Round ends", "submission due"]


@pytest.mark.parametrize("status", sorted(TERMINAL))
def test_upcoming_terminal_is_empty(listing, status):
    t = _t(status, [ChecklistItem(1, "x", False, datetime(2026, 12, 1, tzinfo=UTC))])
    assert upcoming(listing, t, NOW) == []
    assert next_action(listing, t, NOW) is None


def test_upcoming_includes_open_todos_sorted(listing):
    t = _t(Status.REGISTERED, [
        ChecklistItem(1, "book train", False, datetime(2026, 11, 30, tzinfo=UTC)),
        ChecklistItem(2, "done already", True, datetime(2026, 11, 29, tzinfo=UTC)),
        ChecklistItem(3, "no date", False, None),
        ChecklistItem(4, "overdue", False, datetime(2026, 11, 24, tzinfo=UTC)),
    ])
    assert upcoming(listing, t, NOW) == [
        Deadline("todo: book train", datetime(2026, 11, 30, tzinfo=UTC), False),
        Deadline("Prototype Round ends", datetime(2026, 12, 3, tzinfo=UTC), False),
        Deadline("submission due", datetime(2026, 12, 10, tzinfo=UTC), True),
    ]
    assert next_action(listing, t, NOW).label == "todo: book train"


def test_upcoming_excludes_past_but_keeps_exactly_now(listing):
    at = datetime(2026, 11, 28, tzinfo=UTC)
    assert upcoming(listing, _t(Status.INTERESTED), at)[0].at == at
    assert upcoming(listing, _t(Status.INTERESTED), datetime(2027, 1, 1, tzinfo=UTC)) == []


def test_upcoming_without_listing_only_todos():
    t = _t(Status.INTERESTED, [ChecklistItem(1, "a", False, datetime(2026, 12, 1, tzinfo=UTC))])
    assert upcoming(None, t, NOW) == [Deadline("todo: a", datetime(2026, 12, 1, tzinfo=UTC), False)]
    assert upcoming(None, _t(Status.INTERESTED), NOW) == []
    assert next_action(None, _t(Status.INTERESTED), NOW) is None


def test_upcoming_with_tracker_todos(tr, listing):
    tr.track(UID, Status.REGISTERED)
    item = tr.add_todo(UID, "pitch deck", datetime(2026, 12, 5, tzinfo=UTC))
    t = tr.get(UID)
    assert "todo: pitch deck" in [d.label for d in upcoming(listing, t, NOW)]
    t = tr.set_todo_done(UID, item.id)
    assert "todo: pitch deck" not in [d.label for d in upcoming(listing, t, NOW)]


# --- storage --------------------------------------------------------------------------


def test_persists_across_reopen(tmp_path, clock):
    path = tmp_path / "logr.db"
    s = Store(path)
    tr = Tracker(s.db)
    tr.track(UID)
    tr.set_status(UID, Status.REGISTERED, note="n1")
    tr.append_note(UID, "hello", now=NOW)
    tr.set_repo(UID, "https://github.com/a/b")
    tr.add_member(UID, "Asha")
    tr.add_todo(UID, "slides", datetime(2026, 12, 1, tzinfo=UTC))
    before, hist = tr.get(UID), tr.history(UID)
    s.close()

    s2 = Store(path)
    tr2 = Tracker(s2.db)  # CREATE TABLE IF NOT EXISTS on an existing db
    assert tr2.get(UID) == before
    assert tr2.history(UID) == hist
    assert [r[0] for r in s2.db.execute("SELECT user_id FROM tracked")] == [LOCAL_USER]
    s2.close()


def test_works_without_row_factory(tmp_path, clock):
    db = sqlite3.connect(str(tmp_path / "plain.db"))  # tuples, not sqlite3.Row
    tr = Tracker(db)
    tr.track(UID)
    tr.add_member(UID, "Asha")
    tr.add_todo(UID, "x")
    tr.set_status(UID, Status.SUBMITTED)
    t = tr.get(UID)
    assert t.status is Status.SUBMITTED and t.team == ["Asha"] and len(t.checklist) == 1
    assert len(tr.history(UID)) == 2 and len(tr.all()) == 1
    db.close()


def test_timestamps_stored_as_utc_z(tr, store):
    tr.track(UID)
    tr.add_todo(UID, "x", datetime(2026, 12, 1, tzinfo=UTC))
    added, = store.db.execute("SELECT added_at FROM tracked").fetchone()
    due, = store.db.execute("SELECT due_at FROM tracked_checklist").fetchone()
    assert added.endswith("Z") and due == "2026-12-01T00:00:00Z"
