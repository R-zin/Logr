# Logr

Logr finds hackathons on Devpost, Devfolio, Unstop, Hack2skill and Luma, and tracks their registration, round and submission deadlines in one place. LinkedIn support comes later; see the roadmap.

The source research and the six-phase plan are in [`docs/ROADMAP.md`](docs/ROADMAP.md). Phases 1 (aggregation) and 2 (exact deadlines, round timelines and cross-source dedupe) are done.

## Quick start

```bash
pip install -e '.[dev]'
logr sync --luma-near bengaluru            # listings + detail pages (first run ~3 min)
logr --tz Asia/Kolkata list --within 7     # deadlines in the next 7 days
```

```
NEXT DEADLINE                IN  SOURCE           MODE    TITLE
25 Nov 2026 14:00        1h 40m  unstop           hybrid  Exora 2.0: Unveil The Build
25 Nov 2026 23:59       11h 39m  devfolio         offline MakerSpark
~26 Nov 2026 01:00      12h 40m  luma             offline Lovable Buildathon @ Buenos Aires
28 Dec 2026 23:59       33d 11h  unstop+devfolio  hybrid  Hacksprint 3.0
...
```

- **NEXT DEADLINE** is the next thing you must act on: registration closing, a round's submission (for example "Idea Submission ends"), or the final submission.
- A `~` means the source doesn't publish that exact time. For example, Luma has no registration cutoff, so the event start stands in for it.
- `unstop+devfolio` means the same hackathon is listed on both platforms and is shown once.

```
$ logr --tz Asia/Kolkata show unstop:1794479
Inceptrix 3.0
  ...
  Next deadline        registration closes, 24 Dec 2026 23:59 (in 29d 11h)
  Timeline:
    Round 1: Idea Submission     submission   01 Nov 2026 00:00 → 24 Dec 2026 23:59
    Evaluation Period            judging      01 Jan 2027 00:00 → 10 Jan 2027 23:59
    Finalist Announcement        results      11 Jan 2027 00:00 → 11 Jan 2027 23:59
    Final Round Registration     registration 12 Jan 2027 00:00 → 20 Jan 2027 23:59
    On-Ground Final Round        event        19 Feb 2027 08:00 → 20 Feb 2027 20:00
  Also listed on:
    devpost     https://inceptrix-hackathon.devpost.com/  (devpost:32017)
```

## Commands

| Command | What it does |
|---|---|
| `logr sync` | Fetch listings and the detail pages of open hackathons, then upsert them. Prints real deadline moves. Flags: `-s SOURCE` (repeatable), `--no-enrich`, `--enrich-ttl HOURS` (12), `--enrich-limit N` (150 per source), `--luma-near PLACE` (city slug or `lat,lng`, repeatable) |
| `logr list` | Open hackathons sorted by next deadline, with duplicates merged. Flags: `-s SOURCE`, `-m online\|offline\|hybrid`, `-w DAYS`, `-q TEXT`, `-a` (include closed and delisted), `--no-merge`, `-n N`, `-f table\|json\|csv`, `-v` (show uid, url and other listings) |
| `logr show <uid>` | Full details, the round timeline, other listings and the history of date changes |
| `logr status` | Result of the last sync for each source, including detail pages fetched and listings delisted |

Global options and environment variables:
- `--db PATH` or `$LOGR_DB`. The default is `~/.local/share/logr/logr.db`; Phase 1 databases upgrade in place.
- `--tz ZONE` or `$LOGR_TZ` sets the timezone for displayed dates.
- `$LOGR_LUMA_NEAR` is a `;`-separated list of default Luma places.

## Layout

```
src/logr/
  models.py        Hackathon, Round, Deadline: normalized model and deadline logic
  http.py          shared client: user agent, throttle, retries
  sources/         one connector per platform: fetch (network), parse (pure), enrich (detail page)
  dedupe.py        cross-source duplicate detection
  store.py         SQLite: hackathons, enrichments cache, date_changes, sync_runs
  sync.py          runs connectors and enrichment; failures are isolated per source
  cli.py           command-line interface
tests/             offline tests against recorded API fixtures
```

## Tests

```bash
pytest
```
