import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from logr import cli
from logr.dedupe import group, normalize, titles_match
from logr.http import Http
from logr.models import Hackathon, Mode, Round, RoundKind, Source, classify_round
from logr.sources.devpost import DevpostConnector, dates_patch, parse_dates_page
from logr.sources.luma import LumaConnector, resolve_place
from logr.sources.unstop import UnstopConnector, parse_rounds, rounds_patch
from logr.store import Store
from logr.sync import SyncOptions, sync

FIX = Path(__file__).parent / "fixtures"
UTC = timezone.utc
NOW = datetime(2026, 11, 25, 6, tzinfo=UTC)


def load(name):
    return json.loads((FIX / f"{name}.json").read_text())


def hk(source, sid, title, **kw):
    return Hackathon(source=source, source_id=sid, title=title, url=f"https://{sid}.example", **kw)


# --- rounds & detail parsers ------------------------------------------------


@pytest.mark.parametrize("name,kind", [
    ("Submissions", RoundKind.SUBMISSION),
    ("Idea Submission (PPT)", RoundKind.SUBMISSION),
    ("Screening Round", RoundKind.SUBMISSION),
    ("Judging", RoundKind.JUDGING),
    ("Public Voting", RoundKind.JUDGING),
    ("Prototype Evaluation", RoundKind.JUDGING),
    ("Winners Announced", RoundKind.RESULTS),
    ("Registrations", RoundKind.REGISTRATION),
    ("Offline Hackathon", RoundKind.EVENT),
    ("Grand Finale", RoundKind.EVENT),
    ("On-Ground Final Round", RoundKind.EVENT),
    ("Offline Round", RoundKind.EVENT),
    ("Round 1", RoundKind.SUBMISSION),
    ("Round 1: Idea Submission", RoundKind.SUBMISSION),
    ("Final Round Registration", RoundKind.REGISTRATION),
    ("Networking dinner", RoundKind.OTHER),
])
def test_classify_round(name, kind):
    assert classify_round(name) is kind


def test_devpost_dates_page():
    rounds = parse_dates_page((FIX / "devpost_dates.html").read_text())
    assert [(r.name, r.kind) for r in rounds] == [
        ("Submissions", RoundKind.SUBMISSION), ("Judging", RoundKind.JUDGING),
        ("Winners Announced", RoundKind.RESULTS)]
    assert rounds[0].ends_at == datetime(2026, 12, 4, 22, tzinfo=UTC)  # 5pm EST
    assert rounds[2].starts_at is None  # empty "Begins" cell
    patch = dates_patch(rounds)
    assert patch["submission_deadline"] == "2026-12-04T22:00:00+00:00"
    assert patch["starts_at"] == "2026-10-30T16:00:00+00:00"


def test_devpost_patch_makes_dates_exact():
    listing = next(DevpostConnector.parse(load("devpost")))
    assert listing.is_approx("submission_deadline")
    enriched = listing.with_patch(dates_patch(parse_dates_page((FIX / "devpost_dates.html").read_text())))
    assert enriched.submission_deadline == datetime(2026, 12, 4, 22, tzinfo=UTC)
    assert enriched.approx_fields == []
    assert len(enriched.rounds) == 3
    nd = enriched.next_deadline(NOW)
    assert nd.label == "submission due" and not nd.approximate  # generic label wins the tie


def test_unstop_rounds_skip_hidden():
    rounds = parse_rounds(load("unstop_competition"))
    assert [r.name for r in rounds] == ["Screening Round", "Offline Hackathon"]
    patch = rounds_patch(rounds)
    assert patch["submission_deadline"] == "2026-11-24T18:29:00+00:00"  # 23:59 IST
    assert patch["starts_at"] == "2026-11-28T04:30:00+00:00"


def test_round_deadlines_count_in_next_deadline():
    h = hk(Source.UNSTOP, "1", "Multi-stage", registration_closes_at=NOW - timedelta(days=1),
           rounds=[Round(name="Idea Submission", kind=RoundKind.SUBMISSION, ends_at=NOW + timedelta(days=2)),
                   Round(name="Prototype Submission", kind=RoundKind.SUBMISSION, ends_at=NOW + timedelta(days=9)),
                   Round(name="Finale", kind=RoundKind.EVENT, starts_at=NOW + timedelta(days=12))])
    assert h.next_deadline(NOW).label == "Idea Submission ends"
    assert h.next_deadline(NOW + timedelta(days=3)).label == "Prototype Submission ends"
    assert not h.is_open(NOW + timedelta(days=10))


def test_v1_records_migrate():
    old = {"source": "luma", "source_id": "e", "title": "Hack", "url": "u",
           "starts_at": "2026-12-01T00:00:00Z", "registration_closes_at": "2026-12-01T00:00:00Z",
           "dates_approximate": True}
    h = Hackathon.model_validate(old)
    assert h.approx_fields == ["registration_closes_at", "starts_at"]


# --- sync: enrichment cache, refinement, delisting -------------------------


def _router(listing_hackathons, dates_html, *, fail_detail=False):
    calls = []

    def handler(req: httpx.Request):
        calls.append(str(req.url))
        if req.url.host == "devpost.com":
            return httpx.Response(200, json={"hackathons": listing_hackathons(),
                                             "meta": {"total_count": len(listing_hackathons())}})
        if req.url.path.endswith("/details/dates"):
            return httpx.Response(500) if fail_detail else httpx.Response(200, text=dates_html)
        return httpx.Response(404)

    return handler, calls


def _open_devpost(n=1):
    """Fixture listings with a submission period that is always in the future,
    so the sync treats them as open (only open listings get detail requests)."""
    year = datetime.now(UTC).year + 1
    return [dict(h, submission_period_dates=f"Jan 01 - Dec 31, {year}")
            for h in load("devpost")["hackathons"][:n]]


def test_sync_enriches_once_then_uses_cache():
    base = _open_devpost()
    handler, calls = _router(lambda: base, (FIX / "devpost_dates.html").read_text())
    http = Http(httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0, retries=0)
    s = Store(":memory:")
    r1 = sync(s, [Source.DEVPOST], http=http)[0]
    assert r1.error is None and r1.enriched == 1
    h = s.get("devpost:31667")
    assert h.submission_deadline == datetime(2026, 12, 4, 22, tzinfo=UTC) and not h.approx_fields
    # approx → exact is refinement, not a "deadline moved" event
    assert r1.result.date_changes == [] and s.history(h.uid) == []

    detail_calls = sum("details/dates" in c for c in calls)
    r2 = sync(s, [Source.DEVPOST], http=http)[0]
    assert r2.enriched == 0 and sum("details/dates" in c for c in calls) == detail_calls
    assert r2.result.unchanged == 1  # cached patch still applied, so nothing flips back


def test_enrichment_failure_keeps_previous_patch():
    s = Store(":memory:")
    s.save_enrichment("devpost:1", {"submission_deadline": "2026-12-04T22:00:00+00:00"})
    s.save_enrichment("devpost:1", None, "HTTPStatusError: 500")
    e = s.get_enrichment("devpost:1")
    assert e.patch == {"submission_deadline": "2026-12-04T22:00:00+00:00"} and e.error
    # errors are retried after at most an hour even with a long TTL
    assert not s.enrichment_is_fresh(e, timedelta(hours=12), e.fetched_at + timedelta(minutes=61))
    assert s.enrichment_is_fresh(e, timedelta(hours=12), e.fetched_at + timedelta(minutes=30))


def test_sync_enrich_limit_and_no_enrich():
    many = [dict(h, id=1000 + i) for i, h in enumerate(_open_devpost(3) * 3)]
    handler, calls = _router(lambda: many, (FIX / "devpost_dates.html").read_text())
    http = Http(httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0, retries=0)
    s = Store(":memory:")
    sync(s, [Source.DEVPOST], http=http, options=SyncOptions(enrich=False))
    assert not any("details/dates" in c for c in calls)
    r = sync(s, [Source.DEVPOST], http=http, options=SyncOptions(enrich_limit=2))[0]
    assert r.enriched == 2


def test_real_deadline_move_is_logged():
    s = Store(":memory:")
    s.upsert_many([hk(Source.DEVFOLIO, "a", "X", registration_closes_at=NOW)])
    r = s.upsert_many([hk(Source.DEVFOLIO, "a", "X", registration_closes_at=NOW + timedelta(days=2))])
    assert [c[1] for c in r.date_changes] == ["registration_closes_at"]


def test_round_moves_are_logged():
    s = Store(":memory:")
    r0 = Round(name="Idea Submission", kind=RoundKind.SUBMISSION, ends_at=NOW)
    s.upsert_many([hk(Source.UNSTOP, "a", "X", rounds=[r0])])
    moved = r0.model_copy(update={"ends_at": NOW + timedelta(days=3)})
    r = s.upsert_many([hk(Source.UNSTOP, "a", "X", rounds=[moved])])
    assert [c[1] for c in r.date_changes] == ["round: Idea Submission"]


def test_delisting_after_consecutive_misses():
    s = Store(":memory:")
    a, b = hk(Source.UNSTOP, "a", "A"), hk(Source.UNSTOP, "b", "B")
    s.upsert_many([a, b])
    assert s.mark_seen(Source.UNSTOP, {a.uid, b.uid}, delist_after=2) == 0
    s.mark_seen(Source.UNSTOP, {a.uid}, delist_after=2)
    assert s.mark_seen(Source.UNSTOP, {a.uid}, delist_after=2) == 1
    assert [h.uid for h in s.all()] == [a.uid]
    assert len(s.all(include_delisted=True)) == 2
    # reappearing un-delists
    s.mark_seen(Source.UNSTOP, {a.uid, b.uid}, delist_after=2)
    assert len(s.all()) == 2


def test_empty_fetch_does_not_delist():
    s = Store(":memory:")
    s.upsert_many([hk(Source.HACK2SKILL, "a", "A")])
    http = Http(httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"data": {"flagshipEvents": [], "communityEvents": []}}))),
        min_interval=0)
    for _ in range(5):
        sync(s, [Source.HACK2SKILL], http=http, options=SyncOptions(delist_after=1))
    assert len(s.all()) == 1


def test_phase1_database_upgrades(tmp_path):
    db = tmp_path / "v1.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE hackathons (uid TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
          title TEXT NOT NULL, url TEXT NOT NULL, registration_closes_at TEXT, submission_deadline TEXT,
          starts_at TEXT, data TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL);
        CREATE TABLE sync_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
          started_at TEXT NOT NULL, finished_at TEXT, fetched INTEGER DEFAULT 0, created INTEGER DEFAULT 0,
          updated INTEGER DEFAULT 0, error TEXT);
    """)
    data = {"source": "luma", "source_id": "e", "title": "Old Hack", "url": "u",
            "starts_at": "2026-12-01T00:00:00Z", "dates_approximate": True}
    con.execute("INSERT INTO hackathons VALUES ('luma:e','luma','e','Old Hack','u',NULL,NULL,NULL,?,'t','t')",
                (json.dumps(data),))
    con.commit()
    con.close()
    s = Store(db)
    assert s.get("luma:e").approx_fields == ["starts_at"]
    assert s.mark_seen(Source.LUMA, set(), delist_after=1) == 1  # new columns exist


# --- dedupe -----------------------------------------------------------------


def test_normalize_drops_noise():
    assert normalize("HackKumbh 2026 : Tech For Humanity") == ("hackkumbh", "tech", "humanity")
    assert normalize("The Global AI Hackathon '26") == ("ai",)


@pytest.mark.parametrize("a,b,same", [
    ("Health Hackathon 3.0", "Health Hackathon 3.0 | Bengaluru", False),  # extra token → jaccard 0.67, ratio low
    ("ETHIndia 2026", "ETHIndia", True),
    ("MakerSpark", "Maker Spark", False),  # different tokens; deliberately strict
    ("Build with AI: PromptWars", "PromptWars – Build with AI", True),
    ("AI Hackathon", "AI Hackathon", True),
    ("Agentic AI Hackathon 2.0 - SF", "Agentic AI Hackathon - NYC", False),
])
def test_titles_match(a, b, same):
    assert titles_match(normalize(a), normalize(b)) is same


def test_group_merges_across_sources_only():
    start = NOW + timedelta(days=10)
    devfolio = hk(Source.DEVFOLIO, "d", "ETHIndia 2026", starts_at=start,
                  registration_closes_at=start - timedelta(days=5))
    luma = hk(Source.LUMA, "l", "ETHIndia", starts_at=start + timedelta(hours=3),
              registration_closes_at=start, approx_fields=["registration_closes_at"])
    other_year = hk(Source.LUMA, "l2", "ETHIndia", starts_at=start + timedelta(days=365))
    same_source = hk(Source.DEVFOLIO, "d2", "ETHIndia 2026", starts_at=start)
    unrelated = hk(Source.UNSTOP, "u", "Smart Bharat", starts_at=start)
    out = group([luma, devfolio, other_year, same_source, unrelated])
    merged = [lst for lst in out if lst.others]
    assert len(merged) == 1
    assert merged[0].primary.uid == devfolio.uid or merged[0].primary.uid == same_source.uid
    assert {o.source for o in merged[0].others} == {Source.LUMA}
    assert len(out) == 4  # 5 listings, one merge


def test_group_needs_dates():
    a = hk(Source.DEVPOST, "a", "Open Hack")
    b = hk(Source.LUMA, "b", "Open Hack")
    assert len(group([a, b])) == 2


def test_list_merges_and_shows_sources(tmp_path, capsys):
    db = tmp_path / "t.db"
    s = Store(db)
    start = datetime.now(UTC) + timedelta(days=10)
    s.upsert_many([
        hk(Source.DEVFOLIO, "d", "ETHIndia 2026", starts_at=start, registration_closes_at=start - timedelta(days=5)),
        hk(Source.LUMA, "l", "ETHIndia", starts_at=start, registration_closes_at=start,
           approx_fields=["registration_closes_at"]),
    ])
    s.close()
    assert cli.main(["--db", str(db), "list"]) == 0
    out = capsys.readouterr().out
    assert "devfolio+luma" in out and "1 duplicate" in out
    assert cli.main(["--db", str(db), "list", "--no-merge", "-f", "json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2
    assert cli.main(["--db", str(db), "show", "luma:l"]) == 0
    assert "Also listed on" in capsys.readouterr().out


# --- luma places ------------------------------------------------------------


def test_resolve_place():
    assert resolve_place("Bengaluru") == (12.97, 77.59)
    assert resolve_place("new york") == (40.71, -74.01)
    assert resolve_place("12.5,77.1") == (12.5, 77.1)
    with pytest.raises(ValueError):
        resolve_place("atlantis")
    with pytest.raises(ValueError):
        resolve_place("100,0")


def test_luma_searches_each_place():
    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        return httpx.Response(200, json=load("luma"))

    http = Http(httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0)
    items = list(LumaConnector(http, places=["bengaluru"]).fetch())
    assert len({h.uid for h in items}) == len(items)  # deduped across queries
    assert sum("latitude" in p for p in seen) == 2  # both queries, once each, near Bengaluru
    assert sum("latitude" not in p for p in seen) == 2  # plus the global feed


def test_cli_rejects_unknown_place(tmp_path, capsys):
    assert cli.main(["--db", str(tmp_path / "x.db"), "sync", "--luma-near", "atlantis"]) == 2
    assert "unknown place" in capsys.readouterr().err


def test_unstop_enrich_uses_competition_endpoint():
    def handler(req):
        assert req.url.path == "/api/public/competition/1818639"
        return httpx.Response(200, json=load("unstop_competition"))

    http = Http(httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0)
    patch = UnstopConnector(http).enrich(hk(Source.UNSTOP, "1818639", "Rajasthan Police Hackathon"))
    assert [r["name"] for r in patch["rounds"]] == ["Screening Round", "Offline Hackathon"]


# Regression cases taken from live data (25 Nov 2026).

def test_normalize_keeps_versions_whole():
    assert normalize("Health Hackathon 3.0") == ("health", "3_0")
    # unrelated "x 2.0" titles no longer share two tokens ("2", "0")
    assert not titles_match(normalize("Code4Cause 2.0"), normalize("Hackday 2.0"))


def test_title_subset_match():
    assert titles_match(normalize("Agents that act. Systems that answer."),
                        normalize("Agents that act. Systems that answer. | Free Virtual Agentic-AI Hackathon"))
    # subset with too little distinctive text is not enough
    assert not titles_match(normalize("Health Hackathon 3.0"),
                            normalize("VIT Bhopal University to Host Health Hackathon 3.0 in January 2027"))


def _t(s):
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def test_dates_match_despite_different_field_meanings():
    # Devfolio's ends_at = end of the hack; Unstop's end_date = registration close.
    devfolio = hk(Source.DEVFOLIO, "e8", "Health Hackathon 3.0",
                  registration_opens_at=_t("2026-10-29T18:30"), registration_closes_at=_t("2026-11-30T18:29"),
                  starts_at=_t("2027-01-28T03:30"), ends_at=_t("2027-01-29T10:30"))
    unstop = hk(Source.UNSTOP, "1784631", "Health Hackathon 3.0",
                registration_opens_at=_t("2026-10-29T18:30"), registration_closes_at=_t("2026-11-30T18:29"),
                ends_at=_t("2026-11-30T18:29"))
    assert len(group([devfolio, unstop])) == 1


def test_dates_match_via_round_boundary():
    devpost = hk(Source.DEVPOST, "32017", "Inceptrix 3.0",
                 starts_at=_t("2026-11-05T08:45"), submission_deadline=_t("2026-12-24T11:30"))
    unstop = hk(Source.UNSTOP, "1794479", "Inceptrix 3.0",
                registration_closes_at=_t("2026-12-24T18:29"), submission_deadline=_t("2027-02-20T14:30"),
                rounds=[Round(name="Round 1: Idea Submission", kind=RoundKind.SUBMISSION,
                              ends_at=_t("2026-12-24T18:29"))])
    assert len(group([devpost, unstop])) == 1
    # same title, same kind of date, nothing lines up → kept apart
    later = hk(Source.DEVPOST, "x", "Inceptrix 3.0", submission_deadline=_t("2027-06-01T00:00"))
    assert len(group([later, unstop])) == 2
