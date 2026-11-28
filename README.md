# Logr

Logr finds hackathons on Devpost, Devfolio, Unstop, Hack2skill and Luma, and tracks their registration, round and submission deadlines in one place. LinkedIn support comes later; see the roadmap.

The source research and the six-phase plan are in [`docs/ROADMAP.md`](docs/ROADMAP.md). Phases 1 (aggregation), 2 (exact deadlines, round timelines and cross-source dedupe) 3 (personal tracking, filters and saved searches) 4 (web dashboard and API) and 5 (scheduled syncs, reminders and a calendar feed) are done.

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

## Tracking your hackathons

```bash
logr prefs set tz Asia/Kolkata                 # show every time in IST from now on
logr track unstop:1794479                      # starts as "interested"
logr mark unstop:1794479 registered --note "team of 4"
logr team unstop:1794479 add Asha
logr todo unstop:1794479 add Finish idea deck --due 2026-12-20
logr mine
```

```
STATUS       NEXT ACTION                  WHEN                         IN  TITLE
interested   submission due               05 Dec 2026 03:30        8d 16h  Kiroween 2026
registered   todo: Finish idea deck       20 Dec 2026 23:59       24d 12h  Inceptrix 3.0
submitted    —                            —                                MakerSpark
```

NEXT ACTION depends on your status. Once you're registered, "registration closes" stops showing. Once you've submitted, only later stages that haven't started yet remain. Your to-dos count as deadlines too. Tracked hackathons get a ★ in `logr list`.

### Finding the right ones

```bash
logr list --theme ai --region bengaluru --min-prize 1000 --team-size 4
logr search save blr-ai --theme ai --region bengaluru
logr list --saved blr-ai -w 14                 # saved search plus a 14-day window
logr prefs set default_search blr-ai           # applied to every `logr list` (--no-prefs to skip)
```

## Web dashboard

```bash
pip install -e '.[web]'
(cd web && npm install && npm run build)    # builds the dashboard into web/out
logr serve                                  # http://127.0.0.1:8000, API docs at /api/docs
```

- **Pages:** Discover, hackathon detail with its round timeline, a board of your hackathons (drag cards between statuses), a calendar of your deadlines and to-dos, saved searches, and settings.
- **Shared data:** the CLI and the dashboard use the same database, so something you track in one shows up in the other.
- **Signing in:** by default there's no login (single user). For a shared deployment, set `LOGR_AUTH=oauth` with GitHub and/or Google OAuth credentials; see the Auth section of [`docs/API.md`](docs/API.md).
- **More:** frontend development and the mock API are covered in [`web/README.md`](web/README.md).

## Reminders and calendar

```bash
logr prefs set notify_email you@example.com     # or notify_telegram_chat / notify_discord_webhook
logr notify --test                              # check the channel works
logr serve --schedule                           # dashboard + sync every 6h + reminders every 15 min
logr feed-url                                   # subscribe in Google/Apple Calendar or Outlook
```

- **Reminders:** you get one 7 days, 1 day and 3 hours before each deadline you still have to act on, plus an alert when an organizer moves a date.
- **Server setup:** email needs SMTP settings on the server, and Telegram needs a bot token. See [`docs/DEPLOY.md`](docs/DEPLOY.md) for those and for running Logr unattended.
- **Dashboard:** both can also be set up under Settings in the dashboard.

## Commands

| Command | What it does |
|---|---|
| `logr sync` | Fetch listings and the detail pages of open hackathons, then upsert them. Prints real deadline moves. Flags: `-s SOURCE` (repeatable), `--no-enrich`, `--enrich-ttl HOURS` (12), `--enrich-limit N` (150 per source), `--luma-near PLACE` (city slug or `lat,lng`, repeatable) |
| `logr list` | Open hackathons sorted by next deadline, with duplicates merged. Flags: `-s SOURCE`, `-m online\|offline\|hybrid`, `-w DAYS`, `-q TEXT`, `--theme T`, `--region R`, `--min-prize USD`, `--team-size N`, `--saved NAME`, `--no-prefs`, `-a` (include closed and delisted), `--no-merge`, `-n N`, `-f table\|json\|csv`, `-v` (show uid, url and other listings) |
| `logr show <uid>` | Full details, the round timeline, other listings and the history of date changes |
| `logr status` | Result of the last sync for each source, including detail pages fetched and listings delisted |
| `logr track UID [--status S]` / `logr untrack UID` | Start or stop tracking a hackathon. Untracking deletes its notes, team and checklist. |
| `logr mark UID STATUS [--note TEXT]` | Move through `interested`, `registered`, `team_formed`, `submitted`, `shortlisted`, `won`, `lost`, `withdrawn` |
| `logr mine [--status S] [--all]` | Your tracked hackathons, sorted by next action. Won, lost and withdrawn are hidden unless you pass `--all`. |
| `logr note UID TEXT` · `logr repo UID URL\|--clear` · `logr team UID add\|rm NAME` | Notes (timestamped), project repo, teammates |
| `logr todo UID add TEXT [--due DATE]` · `done\|undo\|rm ID` | Checklist. `--due` takes `YYYY-MM-DD` (23:59 that day) or `YYYY-MM-DD HH:MM`, in your display timezone. |
| `logr search save NAME [filters]` · `ls` · `rm NAME` | Saved searches. Use one with `logr list --saved NAME`. |
| `logr prefs` · `prefs set KEY VALUE` · `prefs unset KEY` | Preferences: `tz`, `luma_near`, `default_search` |
| `logr serve [--host H] [--port P] [--static DIR] [--schedule]` | Run the web dashboard and JSON API. Serves `web/out` if it has been built. `--schedule` also runs syncs and reminders. |
| `logr daemon [--sync-every H] [--notify-every M]` | Run the scheduler (syncs and reminders) without the web server |
| `logr notify [--dry-run \| --test]` | Run one reminder pass now, preview it, or send a test message |
| `logr ics [-o FILE]` · `logr feed-url [--rotate]` | Your deadlines as an .ics file, or your private calendar subscription link |

Global options and environment variables:
- `--db PATH` or `$LOGR_DB`. The default is `~/.local/share/logr/logr.db`; Phase 1 databases upgrade in place.
- `--tz ZONE`, then `$LOGR_TZ`, then `logr prefs set tz ZONE` sets the timezone for displayed dates.
- `$LOGR_LUMA_NEAR` (or pref `luma_near`) is a `;`-separated list of default Luma places.

## Layout

```
src/logr/
  models.py        Hackathon, Round, Deadline: normalized model and deadline logic
  http.py          shared client: user agent, throttle, retries
  sources/         one connector per platform: fetch (network), parse (pure), enrich (detail page)
  dedupe.py        cross-source duplicate detection
  tracking.py      your pipeline: status + history, notes, team, checklist, status-aware deadlines
  searches.py      filters (themes, region, prize, team size), saved searches, preferences
  store.py         SQLite: hackathons, enrichments cache, date_changes, sync_runs
  sync.py          runs connectors and enrichment; failures are isolated per source
  cli.py           command-line interface
  api/             FastAPI JSON API (docs/API.md)
  auth.py          local mode, GitHub/Google OAuth, sessions, CSRF
  notify.py        reminders, moved/health alerts; email, Telegram, Discord
  ics.py           iCalendar feed and per-user feed tokens
  scheduler.py     periodic sync + reminder passes
web/               Next.js dashboard (static export served by `logr serve`)
tests/             offline tests against recorded API fixtures
```

## Tests

```bash
pytest
```
