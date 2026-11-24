# Logr

Logr finds hackathons on Devpost, Devfolio, Unstop, Hack2skill and Luma, and tracks their registration and submission deadlines in one place. LinkedIn support comes later; see the roadmap.

The source research and the six-phase plan are in [`docs/ROADMAP.md`](docs/ROADMAP.md). This is **Phase 1: the aggregation core**.

## Quick start

```bash
pip install -e '.[dev]'
logr sync                                  # fetch from all sources (about 30 s)
logr --tz Asia/Kolkata list --within 7     # deadlines in the next 7 days
```

```
NEXT DEADLINE                IN  SOURCE     MODE    TITLE
24 Nov 2026 23:59        1h 27m  unstop     offline Rajasthan Police Hackathon 2026
~25 Nov 2026 05:30       6h 58m  devpost    online  HackWithAditya
25 Nov 2026 23:59         1d 1h  devfolio   offline MakerSpark
...
```

A `~` in front of a date means it is approximate: the source didn't publish an exact time. See the roadmap for how each source is handled.

## Commands

| Command | What it does |
|---|---|
| `logr sync [-s devpost -s unstop ...]` | Fetch listings and upsert them. Prints any deadline changes. |
| `logr list` | Open hackathons sorted by next deadline. Flags: `-s SOURCE`, `-m online\|offline\|hybrid`, `-w DAYS`, `-q TEXT`, `-a` (include closed), `-n N`, `-f table\|json\|csv`, `-v` (show uid and url) |
| `logr show <uid>` | Full details plus the history of date changes |
| `logr status` | Result of the last sync for each source |

Global options:
- `--db PATH` (or `$LOGR_DB`). The default is `~/.local/share/logr/logr.db`.
- `--tz ZONE` (or `$LOGR_TZ`) sets the timezone for displayed dates.

## Layout

```
src/logr/
  models.py        normalized Hackathon model and deadline logic
  http.py          shared client: user agent, throttle, retries
  sources/         one connector per platform (fetch = network, parse = pure)
  store.py         SQLite: hackathons, date_changes, sync_runs
  sync.py          runs connectors; failures are isolated per source
  cli.py           command-line interface
tests/             offline tests against recorded API fixtures
```

## Tests

```bash
pytest
```
