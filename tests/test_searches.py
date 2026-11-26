from datetime import datetime, timedelta, timezone

import pytest

from logr.models import Hackathon, Mode, Source
from logr.searches import (
    PREF_KEYS,
    Filter,
    Preferences,
    SavedSearches,
    parse_prize,
    prize_usd,
    region_matches,
)
from logr.store import Store

UTC = timezone.utc
NOW = datetime(2026, 11, 25, 6, tzinfo=UTC)


def hk(sid="x", title="Hack", **kw):
    kw.setdefault("source", Source.DEVPOST)
    kw.setdefault("submission_deadline", NOW + timedelta(days=5))
    return Hackathon(source_id=sid, title=title, url=f"https://{sid}.example", **kw)


# --- parse_prize / prize_usd ------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    # Devpost
    ("$100,000", [("USD", 100_000)]),
    ("$5,000", [("USD", 5_000)]),
    ("$1,500", [("USD", 1_500)]),
    ("$100,000.50", [("USD", 100_000.5)]),
    # Unstop
    ("₹500,000", [("INR", 500_000)]),
    ("₹51,000", [("INR", 51_000)]),
    ("₹250,000 + $1,000", [("INR", 250_000), ("USD", 1_000)]),
    ("SGD 2,000", [("SGD", 2_000)]),
    ("€2,500", [("EUR", 2_500)]),
    ("£1,000", [("GBP", 1_000)]),
    # other formats
    ("$10K", [("USD", 10_000)]),
    ("$10k in prizes", [("USD", 10_000)]),
    ("$1.5M", [("USD", 1_500_000)]),
    ("₹5 lakh", [("INR", 500_000)]),
    ("₹5 Lakhs", [("INR", 500_000)]),
    ("₹1.2 crore", [("INR", 12_000_000)]),
    ("₹ 5,00,000", [("INR", 500_000)]),
    ("Rs. 25,000", [("INR", 25_000)]),
    ("Rs 25,000/-", [("INR", 25_000)]),
    ("INR 50,000", [("INR", 50_000)]),
    ("INR50000", [("INR", 50_000)]),
    ("50,000 INR", [("INR", 50_000)]),
    ("2,500 €", [("EUR", 2_500)]),
    ("10,000 USD for the top 3 teams", [("USD", 10_000)]),
    ("US$ 1,000", [("USD", 1_000)]),
    ("CHF 1,000", [("CHF", 1_000)]),
])
def test_parse_prize(text, expected):
    assert parse_prize(text) == [(c, pytest.approx(a)) for c, a in expected]


@pytest.mark.parametrize("text", [
    None, "", "Prize Pool", "Best Hack, Swags", "Top 3 teams", "2026",
    "Top 3 teams win swag in 2026", "Winners 3", "5 lakh", "Round 2 of 3",
    "Try 3 times", "100 teams, 48 hours",
])
def test_parse_prize_no_amount(text):
    assert parse_prize(text) == []


def test_parse_prize_ignores_number_noise_next_to_amounts():
    assert parse_prize("Top 3 $500") == [("USD", 500)]
    assert parse_prize("2026 edition: $5,000 for 3 winners") == [("USD", 5_000)]
    assert parse_prize("Winners 1st: ₹10,000, 2nd: ₹5,000") == [("INR", 10_000), ("INR", 5_000)]


def test_prize_usd():
    assert prize_usd("$5,000") == pytest.approx(5_000)
    assert prize_usd("₹500,000") == pytest.approx(6_000)
    assert prize_usd("₹5 lakh") == pytest.approx(6_000)
    assert prize_usd("₹250,000 + $1,000") == pytest.approx(4_000)
    assert prize_usd("€2,500 + £1,000") == pytest.approx(2_500 * 1.08 + 1_270)
    assert prize_usd("SGD 2,000") == pytest.approx(1_480)


@pytest.mark.parametrize("text", [None, "", "Prize Pool", "Top 3 teams", "CHF 1,000", "$1,000 + CHF 500"])
def test_prize_usd_none(text):
    assert prize_usd(text) is None


# --- region -------------------------------------------------------------------


@pytest.mark.parametrize("region,location,ok", [
    ("bengaluru", "Bangalore, Karnataka", True),
    ("Bangalore", "Bengaluru, India", True),
    ("BENGALURU", "bengaluru", True),
    ("delhi", "New Delhi, India", True),
    ("new delhi", "Delhi NCR", True),
    ("New  Delhi", "new delhi", True),
    ("mumbai", "Bombay", True),
    ("benares", "Varanasi, UP", True),
    ("bengaluru", "Mumbai, Maharashtra", False),
    ("india", "Pune, India", True),
    ("in", "Chennai, Tamil Nadu", False),  # not mid-word
    ("pune", None, False),
])
def test_region_matches(region, location, ok):
    assert region_matches(region, location) is ok


# --- Filter.matches -----------------------------------------------------------


def test_empty_filter_matches_open_only():
    f = Filter()
    assert f.matches(hk(), NOW)
    closed = hk(submission_deadline=NOW - timedelta(days=1))
    assert not f.matches(closed, NOW)
    assert Filter(include_closed=True).matches(closed, NOW)


def test_within_days():
    soon, later = hk(submission_deadline=NOW + timedelta(days=3)), hk(submission_deadline=NOW + timedelta(days=30))
    undated = hk(submission_deadline=None)
    f = Filter(within_days=7)
    assert f.matches(soon, NOW)
    assert not f.matches(later, NOW)
    assert not f.matches(undated, NOW)
    assert Filter(within_days=30).matches(later, NOW)  # boundary is inclusive


def test_sources_and_modes():
    h = hk(source=Source.UNSTOP, mode=Mode.ONLINE)
    assert Filter(sources=[Source.UNSTOP, Source.DEVPOST]).matches(h, NOW)
    assert not Filter(sources=[Source.DEVFOLIO]).matches(h, NOW)
    assert Filter(modes=[Mode.ONLINE]).matches(h, NOW)
    assert not Filter(modes=[Mode.OFFLINE, Mode.HYBRID]).matches(h, NOW)


def test_search():
    h = hk(title="Smart Hack", organizer="ACME", location="Pune", themes=["Fintech"])
    for q in ("smart", "acme", "PUNE", "fintech"):
        assert Filter(search=q).matches(h, NOW)
    assert not Filter(search="blockchain").matches(h, NOW)


def test_themes():
    h = hk(themes=["Machine Learning/AI", "Web3"])
    assert Filter(themes=["ai"]).matches(h, NOW)
    assert Filter(themes=["health", "web3"]).matches(h, NOW)  # any
    assert not Filter(themes=["health"]).matches(h, NOW)
    assert not Filter(themes=["ai"]).matches(hk(themes=[]), NOW)


def test_region():
    f = Filter(region="bengaluru")
    assert f.matches(hk(location="Bangalore, Karnataka", mode=Mode.OFFLINE), NOW)
    assert not f.matches(hk(location="Chennai", mode=Mode.OFFLINE), NOW)
    assert not f.matches(hk(location=None, mode=Mode.ONLINE), NOW)


def test_min_prize():
    f = Filter(min_prize_usd=5_000)
    assert f.matches(hk(prize="$5,000"), NOW)
    assert f.matches(hk(prize="₹3 lakh + $1,500"), NOW)  # 3600 + 1500 summed
    assert not f.matches(hk(prize="₹51,000"), NOW)
    assert not f.matches(hk(prize="CHF 100,000"), NOW)  # no rate → unknown
    assert not f.matches(hk(prize="Prize Pool"), NOW)
    assert not f.matches(hk(prize=None), NOW)
    assert Filter(min_prize_usd=5_000).matches(hk(prize="₹5 lakh"), NOW)


def test_team_size():
    bounded = hk(team_size_min=2, team_size_max=4)
    assert Filter(team_size=2).matches(bounded, NOW)
    assert Filter(team_size=4).matches(bounded, NOW)
    assert not Filter(team_size=1).matches(bounded, NOW)
    assert not Filter(team_size=5).matches(bounded, NOW)
    unknown = hk()
    assert Filter(team_size=1).matches(unknown, NOW)
    assert Filter(team_size=50).matches(unknown, NOW)
    only_max = hk(team_size_max=3)
    assert Filter(team_size=1).matches(only_max, NOW)
    assert not Filter(team_size=4).matches(only_max, NOW)
    only_min = hk(team_size_min=3)
    assert not Filter(team_size=2).matches(only_min, NOW)
    assert Filter(team_size=10).matches(only_min, NOW)


def test_combined_criteria_all_must_hold():
    f = Filter(modes=[Mode.OFFLINE], themes=["ai"], region="bangalore", min_prize_usd=1_000,
               team_size=3, within_days=14, sources=[Source.UNSTOP], search="summit")
    good = hk(source=Source.UNSTOP, title="AI Summit Hack", mode=Mode.OFFLINE, location="Bengaluru",
              themes=["Generative AI"], prize="₹1.2 crore", team_size_min=1, team_size_max=4)
    assert f.matches(good, NOW)
    for change in [
        {"source": Source.DEVPOST}, {"mode": Mode.ONLINE}, {"location": "Mumbai"},
        {"themes": ["Health"]}, {"prize": "₹50,000"}, {"team_size_max": 2},
        {"submission_deadline": NOW + timedelta(days=20)}, {"title": "AI Hack"},
        {"submission_deadline": NOW - timedelta(days=1)},
    ]:
        bad = good.model_copy(update=change)
        assert not f.matches(bad, NOW), change


# --- merged / is_empty / dict / describe ----------------------------------------


def test_merged():
    base = Filter(modes=[Mode.ONLINE], within_days=14, themes=["ai"], include_closed=True)
    out = base.merged(Filter(within_days=3, region="pune"))
    assert out == Filter(modes=[Mode.ONLINE], within_days=3, themes=["ai"], include_closed=True, region="pune")
    assert base.within_days == 14 and base.region is None  # base untouched
    # include_closed False in the override doesn't clear it; True sets it
    assert Filter().merged(Filter(include_closed=True)).include_closed is True
    assert Filter(include_closed=True).merged(Filter()).include_closed is True
    assert Filter(modes=[Mode.ONLINE]).merged(Filter(modes=[Mode.OFFLINE])).modes == [Mode.OFFLINE]
    out.modes.append(Mode.HYBRID)
    assert base.modes == [Mode.ONLINE]  # lists not shared


def test_is_empty():
    assert Filter().is_empty()
    assert Filter(include_closed=False).is_empty()
    assert not Filter(include_closed=True).is_empty()
    assert not Filter(team_size=2).is_empty()
    assert not Filter(min_prize_usd=0).is_empty()
    assert not Filter(within_days=0).is_empty()


def test_dict_round_trip():
    f = Filter(sources=[Source.DEVPOST, Source.UNSTOP], modes=[Mode.ONLINE, Mode.HYBRID], within_days=7.5,
               search="ai", include_closed=True, themes=["AI", "Web3"], region="bengaluru",
               min_prize_usd=1000, team_size=3)
    d = f.to_dict()
    assert d["sources"] == ["devpost", "unstop"] and d["modes"] == ["online", "hybrid"]
    import json
    assert Filter.from_dict(json.loads(json.dumps(d))) == f
    assert Filter().to_dict() == {}
    assert Filter.from_dict({}) == Filter()
    assert Filter(within_days=3).to_dict() == {"within_days": 3}


@pytest.mark.parametrize("d", [
    {"color": "red"},
    {"sources": ["myspace"]},
    {"modes": ["telepathic"]},
    {"modes": "online"},
    {"team_size": "three"},
    {"within_days": True},
    {"include_closed": "yes"},
])
def test_from_dict_errors(d):
    with pytest.raises(ValueError):
        Filter.from_dict(d)


def test_describe():
    assert Filter().describe() == "(everything)"
    f = Filter(modes=[Mode.ONLINE], themes=["AI", "Web3"], min_prize_usd=1000, within_days=14)
    assert f.describe() == "online · themes AI, Web3 · ≥ $1,000 · within 14d"
    full = Filter(sources=[Source.DEVPOST], search="hack", region="pune", team_size=2, include_closed=True,
                  within_days=1.5).describe()
    for bit in ("devpost", '"hack"', "pune", "team of 2", "within 1.5d", "closed"):
        assert bit in full
    assert "\n" not in full


# --- saved searches & preferences --------------------------------------------------


def test_saved_searches(tmp_path):
    store = Store(tmp_path / "logr.db")
    ss = SavedSearches(store.db)
    assert ss.all() == [] and ss.get("nope") is None
    f = Filter(modes=[Mode.ONLINE], min_prize_usd=1000)
    ss.save("  Online-Big ", f)
    assert ss.get("online-big") == f
    assert ss.get(" ONLINE-BIG") == f
    ss.save("b_2", Filter(region="pune"))
    ss.save("a1", Filter())
    assert [n for n, _ in ss.all()] == ["a1", "b_2", "online-big"]
    ss.save("a1", Filter(team_size=4))  # replace
    assert ss.get("a1") == Filter(team_size=4)
    assert len(ss.all()) == 3
    assert ss.delete("A1") is True
    assert ss.delete("a1") is False
    assert ss.get("a1") is None
    store.close()

    reopened = Store(tmp_path / "logr.db")
    ss2 = SavedSearches(reopened.db)
    assert ss2.all() == [("b_2", Filter(region="pune")), ("online-big", f)]
    reopened.close()


@pytest.mark.parametrize("name", ["", "   ", "-lead", "_lead", "has space", "dot.name", "x" * 41, "émoji"])
def test_saved_search_bad_names(tmp_path, name):
    ss = SavedSearches(Store(tmp_path / "logr.db").db)
    with pytest.raises(ValueError):
        ss.save(name, Filter())


def test_saved_search_name_max_length(tmp_path):
    ss = SavedSearches(Store(tmp_path / "logr.db").db)
    ss.save("x" * 40, Filter())
    assert ss.get("x" * 40) == Filter()


def test_preferences(tmp_path):
    store = Store(tmp_path / "logr.db")
    p = Preferences(store.db)
    assert p.all() == {} and p.get("tz") is None
    p.set("tz", "Asia/Kolkata")
    p.set("luma_near", "bengaluru;mumbai")
    p.set("tz", "Europe/London")
    assert p.get("tz") == "Europe/London"
    assert p.all() == {"tz": "Europe/London", "luma_near": "bengaluru;mumbai"}
    assert p.unset("luma_near") is True
    assert p.unset("luma_near") is False
    with pytest.raises(KeyError):
        p.set("colour", "blue")
    assert set(PREF_KEYS) >= {"tz", "luma_near", "default_search"}
    store.close()

    reopened = Store(tmp_path / "logr.db")
    assert Preferences(reopened.db).all() == {"tz": "Europe/London"}
    # both tables coexist and re-creating them is harmless
    SavedSearches(reopened.db)
    Preferences(reopened.db)
    reopened.close()
