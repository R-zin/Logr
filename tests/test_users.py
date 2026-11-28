"""Phase 4 groundwork: stores are scoped per user."""
from logr.searches import Filter, Preferences, SavedSearches
from logr.store import Store
from logr.tracking import Status, Tracker


def test_users_are_isolated(tmp_path):
    db = Store(tmp_path / "u.db").db
    alice, bob, local = Tracker(db, "gh:alice"), Tracker(db, "gh:bob"), Tracker(db)
    alice.track("devpost:1", Status.REGISTERED)
    alice.add_todo("devpost:1", "deck")
    bob.track("devpost:1")
    assert alice.get("devpost:1").status is Status.REGISTERED
    assert bob.get("devpost:1").status is Status.INTERESTED
    assert bob.get("devpost:1").checklist == []
    assert local.get("devpost:1") is None
    assert bob.untrack("devpost:1") and alice.get("devpost:1") is not None

    SavedSearches(db, "gh:alice").save("ai", Filter(themes=["ai"]))
    assert SavedSearches(db, "gh:bob").get("ai") is None
    Preferences(db, "gh:alice").set("tz", "Asia/Kolkata")
    assert Preferences(db, "gh:bob").get("tz") is None
    assert Preferences(db).all() == {}
