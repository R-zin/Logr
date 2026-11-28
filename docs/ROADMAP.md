# Logr: research and six-phase roadmap

Logr finds hackathons across platforms and tracks the dates you need to act on: when registration opens and closes, when submissions are due, and when the event starts and ends.

## 1. Source research (listings probed 24 Nov 2026, detail pages 25 Nov 2026)

Each source was tested from a server with plain HTTPS requests. Everything in this table is a public, unauthenticated endpoint that the platform's own website calls. None of these endpoints is a documented API, so their shapes can change without notice. That is why each connector keeps its parsing in a pure function with recorded fixtures (`tests/fixtures/`).

| Source | Listing endpoint | Dates in the listing | Detail source (Phase 2) | Result |
|---|---|---|---|---|
| **Devpost** | `GET devpost.com/api/hackathons?status[]=open&status[]=upcoming&page=N` (9 per page, ~150; 16 invite-only skipped) | Only a string like `"Oct 30 - Dec 04, 2026"`, with no time of day | `<hackathon>/details/dates`: a server-rendered table (Period, Begins, Ends) whose cells carry exact `data-iso-date` values for Submissions, Judging, Public Voting and Winners Announced | ✅ Exact. One quirk: this page answers **403** when a non-browser user agent sends `Accept: text/html`, and **200** with `Accept: */*`. Logr sends `*/*` and keeps its honest user agent. |
| **Devfolio** | `POST api.devfolio.co/api/search/hackathons` with body `{"type":"application_open"\|"upcoming","from","size"}` | Exact UTC: `reg_starts_at`, `reg_ends_at`, `starts_at`, `ends_at` | `GET api.devfolio.co/api/hackathons/<slug>` returns the same four dates and nothing more | ✅ Already exact. Projects are submitted during the hack, so `ends_at` is used as the submission deadline, marked approximate. No detail request is made. |
| **Unstop** | `GET unstop.com/api/public/opportunity/search-result?opportunity=hackathons&oppstatus=open&page=N&per_page=30` (~180) | `start_regn_dt`, `end_regn_dt` (IST offsets), `end_date` | `GET unstop.com/api/public/competition/<id>` → `rounds[].details[]`, each with exact start and end. Rounds flagged `is_hidden` (internal auto-screening) are skipped. | ✅ Full multi-stage timeline (idea submission, PPT, prototype, finale). Note that `end_date` often equals the registration close, not the end of the event. |
| **Hack2skill** | `GET hack2skill.com/api/v1/innovator/public/event/list?page=1&records=100` (all ~86 at once, past ones included) | `registrationStart`, `registrationEnd` | The event API (`/api/v1/event/<slug>/innovator/event-page`) returns **401 without a login**. Public event pages are a client-rendered app, and their pre-rendered HTML is free-form marketing copy that differs per event. | ⚠️ Registration window only. Parsing free-form copy per event would be brittle and guess-prone. Revisit in Phase 6 with the LLM extractor (on demand, for tracked events only). |
| **Luma** | `GET api.lu.ma/discover/get-paginated-events?query=hackathon&pagination_limit=50` | Exact `start_at` and `end_at` | Not needed | ✅ Start and end are exact. There's no registration deadline, so the start is used as the cutoff (marked approximate). Without coordinates the feed is a global mix; `latitude`/`longitude` scope it to a city (verified: Bengaluru coordinates returned only Bengaluru events). |
| **LinkedIn** | None usable | — | — | LinkedIn has no public API for searching posts or events. The Events and Community Management APIs are partner-only, and the User Agreement forbids scraping. So LinkedIn comes in through the user (Phase 6): paste a post URL or text, or forward LinkedIn notification emails, then an LLM extracts the fields. |

### Design decisions that came out of the research

1. **One normalized model** (`Hackathon`). All dates are stored as UTC, and a field is `None` when the source doesn't publish it. Logr never invents a date. Derived dates are named in `approx_fields` (per field since Phase 2), shown as `~` in the CLI.
2. **Conservative approximations.** Devpost's date-only end becomes 00:00 UTC on that day until the detail page has been read. That is never later than the real deadline, so a reminder can't be late.
3. **Listing first, detail pages second.** Listing endpoints are cheap (fewer than 40 requests for everything). Detail requests go only to *open* listings, soonest deadline first, capped per sync (`--enrich-limit`, default 150) and cached (`--enrich-ttl`, default 12 h). A cached result keeps applying to later listings, so dates never flip back to approximate. A failed lookup is retried after an hour and keeps the previous good result.
4. **Per-source isolation.** If one source fails, the others still sync, and every run is logged in `sync_runs`.
5. **Change history, minus noise.** Organizers move deadlines. Every real move of a tracked date or round is written to `date_changes`. An approximate date being replaced by the exact one is a refinement, so it isn't logged. On the first live run this caught real overnight extensions on Unstop, such as registration for `unstop:1817696` moving from 24 Nov to 26 Nov.
6. **Polite fetching.** Logr identifies itself with a user agent, spaces requests at least 0.5 s apart, retries 429 and 5xx responses with backoff and `Retry-After`, and syncs a few times a day rather than every minute.
7. **Cautious dedupe.** A wrong merge hides a hackathon; a missed merge only shows a duplicate row. So two listings merge only when they come from different sources, their titles match after normalization, and their timelines agree. If both publish a start date, it decides alone (±2 days). Otherwise any shared timestamp, including round boundaries, within a day. On live data this found 5 true duplicates and no false ones.

## 2. The six phases

### Phase 1: Aggregation core ✅
- Python package (`src/logr`), with httpx and pydantic as the only dependencies
- Normalized `Hackathon` model with `next_deadline()` and `is_open()`
- Connectors for Devpost, Devfolio, Unstop, Hack2skill and Luma, each with pagination and dedupe
- SQLite store: upsert, a `date_changes` history and a `sync_runs` log
- CLI: `logr sync`, `logr list` (filter by source, mode, days ahead or text; output as table, JSON or CSV), `logr show`, `logr status`
- Tests run offline against recorded fixtures, including the retry and per-source isolation paths

**Done when:** `logr sync && logr list --within 7` lists real hackathons from all five sources, sorted by next deadline. Met: 498 listings on the first live run.

### Phase 2: Precise deadlines and enrichment ✅
- **Detail fetchers.** Devpost `/details/dates` gives exact submission deadlines plus judging and results. Unstop's competition API gives full round timelines. Devfolio needs no detail request (`ends_at` is the submission deadline, marked approximate). Hack2skill is login-walled, so it stays registration-only (see the research table).
- **Rounds.** `Hackathon.rounds` holds name, kind (registration, submission, judging, results, event, other), start and end. Every submission-type round end counts as a deadline, so `next_deadline()` follows multi-stage hackathons through each stage. Moves to a round's dates are logged as `round: <name>`. Rounds are stored inside the hackathon record rather than in a separate table, because nothing queries them on their own yet. Phase 4's API will split them out.
- **Per-field approximation.** `approx_fields` replaces the single `dates_approximate` flag. Phase 1 records and databases migrate automatically.
- **Enrichment cache.** An `enrichments` table, with a TTL, a per-sync limit and keep-last-good-on-error. New flags: `logr sync --no-enrich`, `--enrich-ttl`, `--enrich-limit`.
- **Luma by city.** `--luma-near bengaluru` (or `lat,lng`, repeatable, or `$LOGR_LUMA_NEAR`) adds city searches alongside the global feed. Luma timestamps were already exact UTC with a zone, so they needed no further normalization.
- **Cross-source dedupe** (`logr/dedupe.py`). `list` shows each hackathon once, with sources shown as `unstop+devfolio`; `-v` gives the other links, `show` lists "Also listed on", and `--no-merge` turns merging off.
- **Delisting.** A listing missing from 3 consecutive successful syncs is marked `delisted` and hidden; it comes back if it reappears. An empty fetch never delists anything, since it usually means the endpoint broke.

**Done when:** Devpost and Unstop deadlines are exact after a sync, multi-stage timelines show in `logr show`, and duplicates merge. Met: 132/132 open Devpost and 150 Unstop detail pages enriched with 0 failures, and 5 duplicates merged on live data.

### Phase 3: Personal tracking ✅
- **Pipeline** (`logr/tracking.py`): `interested → registered → team_formed → submitted → shortlisted → won / lost / withdrawn`. Any move is allowed, since people fix mistakes, and every change is kept in a status history with an optional note.
- **Per-hackathon workspace:** timestamped notes, project repo URL, teammates, and a checklist with optional due dates.
- **Status-aware deadlines.** `mine` shows the *next thing you have to do*, not just the listing's next date:
  - *interested:* every deadline;
  - *registered / team formed:* registration drops off;
  - *submitted / shortlisted:* only later stages that haven't started yet (such as a prototype round after the idea round);
  - *won / lost / withdrawn:* nothing;
  - at every stage before that, to-dos with due dates count as deadlines.

  If you track one copy of a hackathon that's merged across sources, its deadlines come from the merged group's most precise record.
- **Filters** (`logr/searches.py`):
  - themes;
  - region, with city aliases so "bengaluru" also matches "Bangalore" (and Gurugram/Gurgaon, etc.);
  - minimum prize: amounts in $/₹/€/£ and ISO codes, including K/M/lakh/crore, are converted to USD at fixed approximate rates;
  - team size;
  - plus everything from Phases 1–2.

  On live data every Devpost and Unstop prize parses. Devfolio lists only prize *names*, so it can't match a minimum prize.
- **Saved searches** (`logr search save/ls/rm`, `logr list --saved NAME`). Explicit flags add to or override a saved search.
- **Preferences** (`logr prefs`): `tz`, `luma_near`, and `default_search`, which is applied by `list` unless you pass `--no-prefs`.
- **Accounts:** single local user for now. Every new table carries `user_id = 'local'`, so Phase 4 can add real accounts without migrating data.
- Built by three parallel agents (tracking, searches, CLI) against interface contracts written first. The status-change command is `logr mark`, because `logr status` already reports sync health.

**Done when:** you can track a hackathon, move it through the pipeline, and `logr mine` shows the next action per hackathon in your timezone. Met: 217 tests pass, and the commands were checked against live data.

### Phase 4: API and web dashboard ✅
- **JSON API** (`logr/api/`, FastAPI). The contract is [`docs/API.md`](API.md), and interactive docs are served at `/api/docs`. It covers:
  - listings with every CLI filter, cross-source merging, saved and default searches, sorting by prize, and pagination;
  - hackathon detail with rounds, date changes and your tracking;
  - a calendar feed, status-aware for tracked hackathons;
  - the full tracking lifecycle, saved searches and preferences;
  - an admin-only background sync.
- **Web dashboard** (`web/`, Next.js 15 static export + Tailwind, served by the same process at `/`):
  - **Discover:** filters kept in the URL; urgency and approximate dates marked.
  - **Hackathon detail:** round timeline, date-change history, tracking panel.
  - **My hackathons:** a Kanban board, with drag-and-drop or a status menu on each card for keyboard users.
  - **Calendar:** month view, or an agenda list on phones.
  - **Saved searches** and **Settings**, including sync status and "Sync now".

  Light and dark mode, and usable at 360px.
- **Auth** (`logr/auth.py`):
  - **`LOGR_AUTH=local`** (the default): no login, and you share data with the CLI.
  - **`LOGR_AUTH=oauth`:** sign in with GitHub and/or Google. Sessions use a signed HttpOnly cookie, the OAuth state is tied to the provider, `next` redirects are restricted to safe paths, and every non-GET request needs a CSRF header.

  Tracking, saved searches and preferences are per user. Phase 3 reserved the `user_id` column, so no data migration was needed.
- **One process:** `logr serve` runs the API and the dashboard together. `web/scripts/smoke.mjs` is an end-to-end browser check against a running server.
- **Storage stays SQLite** (WAL mode, one connection per request). Postgres, planned here originally, moves to Phase 6. For a personal or small-team deployment, one SQLite file is simpler to run and back up, and the store layer is SQLite-specific, so swapping it is a separate piece of work. Nothing the dashboard needs depends on it.
- Built by three parallel agents (API, auth, dashboard) against contracts written first. Next.js was upgraded from 15.1.6 to 15.5.29 and PostCSS pinned to 8.5.34, so `npm audit` reports 0 vulnerabilities.

**Done when:** you can find, track and manage hackathons in a browser, with the same data as the CLI. Met: 284 Python tests pass, the dashboard type-checks and builds, and a browser end-to-end run against the real server on live data passed (Discover, filters, detail, adding a to-do, moving a board card, calendar, settings) with no console errors or failed API calls.

### Phase 5: Scheduling and reminders ✅
- **Scheduler** (`logr/scheduler.py`):
  - Syncs every 6 hours and runs a reminder pass every 15 minutes, either inside the web server (`logr serve --schedule`) or on its own (`logr daemon`).
  - The two jobs never overlap, and an error in one doesn't stop the other.
  - The last successful run of each job is saved, so a restart doesn't re-sync straight away.
- **Reminders** (`logr/notify.py`) go out by email (SMTP), Telegram or Discord.
  - **Timing:** 7 days, 1 day and 3 hours before each deadline, adjustable with the `remind_offsets` pref.
  - **Status-aware:** registration reminders stop once you're registered, and finished hackathons go quiet. To-dos with due dates get reminders too.
  - **No burst:** if several reminder times are already due, only the nearest is sent, so tracking a hackathon 2 hours before its deadline gives one message, not three.
  - **Restart on move:** a moved deadline restarts its reminders.
  - **Countdowns round down,** so a reminder never overstates the time left. The first live run said "7 days" for 6 days 14 hours; that was fixed and now has a test.
- **"Deadline moved" alerts** come from the `date_changes` history, e.g. "submission due moved from 5 Dec to 10 Dec IST (extended by 5d)".
- **Sync-health alerts for admins:** a source that errors, a source that suddenly returns 0 results (its endpoint probably changed), or no successful sync in 24 hours.
- **Delivery is idempotent:**
  - A send log records each (message, channel) pair, so nothing is sent twice.
  - If one channel fails, only that channel retries.
  - Moved alerts are queued for up to 7 days until they get through.
- **Secrets stay hidden.** The Telegram bot token, the Discord webhook and the SMTP password never appear in errors, logs or the API. httpx's request logging is scrubbed, and the webhook is masked in preferences.
- **Calendar subscription** (`logr/ics.py`):
  - An RFC 5545 feed of your deadlines, to-dos and event spans, with alarms, at a private URL you can rotate (`/api/calendar.ics?token=…`, `logr feed-url`, or Settings in the dashboard).
  - Google Calendar, Apple Calendar and Outlook can all subscribe to it, which replaces the direct Google Calendar sync originally planned.
  - Deadlines are written as instants with no end time, as the standard requires.
- **Dashboard Settings:** set up your notification channels, send a test notification, and get or reset your calendar link.
- **Commands:** `logr notify [--dry-run|--test]`, `logr ics`, `logr feed-url`, `logr daemon`, `logr serve --schedule`.
- **Deployment guide:** [`docs/DEPLOY.md`](DEPLOY.md) covers systemd, cron, every `LOGR_*` variable, and how to get a Telegram chat id or a Discord webhook.
- Built by four parallel agents (notifications, calendar feed + API, scheduler + CLI, dashboard) against contracts written first.

**Done when:** a tracked deadline produces a reminder through a real channel, a moved deadline produces an alert, and the calendar subscribes. Met:
- **Real email path:** reminders, a moved alert, a health alert and a test sent from the dashboard all arrived at a real local SMTP server (aiosmtpd), and repeat passes sent nothing.
- **Calendar:** the feed, served and from the CLI, parses with the independent `icalendar` library, with every line within 75 octets.
- **Tests:** 466 pass on Python 3.11 and 3.13.

### Phase 6: LinkedIn ingestion and smart features
- LinkedIn through user input only: paste a post URL or text, use a browser-extension "save to Logr" button, or forward notification emails. An LLM extracts title, dates and URL into the `Hackathon` model, with a confidence score and a human confirmation step.
- The same extractor handles any other link (college club pages, Discord announcements and so on)
- Hack2skill event pages through the same extractor: on demand, for tracked events only, since their structured API needs a login
- Recommendations: rank open hackathons by your skills and past participation
- Hardening: per-source contract tests on a schedule, monitoring, rate-limit budgets, and a terms-of-service review before any public launch
- Postgres as an optional backend for multi-user hosting (deferred from Phase 4); server-side session revocation
