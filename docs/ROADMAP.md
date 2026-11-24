# Logr: research and six-phase roadmap

Logr finds hackathons across platforms and tracks the dates you need to act on: when registration opens and closes, when submissions are due, and when the event starts and ends.

## 1. Source research (probed 24 Nov 2026)

Each source was tested from a server with plain HTTPS requests. Everything in this table is a public, unauthenticated endpoint that the platform's own website calls. None of these endpoints is a documented API, so their shapes can change without notice. That is why each connector keeps its parsing in a pure function with recorded fixtures (`tests/fixtures/`).

| Source | Endpoint | Pagination | Dates you get from the listing | Gaps (to fill in Phase 2) |
|---|---|---|---|---|
| **Devpost** | `GET devpost.com/api/hackathons?status[]=open&status[]=upcoming&page=N` | 9 per page, `meta.total_count` (~150 open+upcoming) | Only a string like `"Oct 30 - Dec 04, 2026"`, with no time of day | Exact deadline is in `data-iso-date` on each hackathon page (for example `2026-12-04T17:00:00-05:00`). 16 of 150 are invite-only and skipped. |
| **Devfolio** | `POST api.devfolio.co/api/search/hackathons` with body `{"type":"application_open"\|"upcoming","from","size"}` | Elasticsearch `from`/`size`, `hits.total` | Exact UTC: `reg_starts_at`, `reg_ends_at`, `starts_at`, `ends_at` | No separate project-submission deadline. Treat `ends_at` as the default, then confirm on the hackathon page. |
| **Unstop** | `GET unstop.com/api/public/opportunity/search-result?opportunity=hackathons&oppstatus=open&page=N&per_page=30` | Laravel pagination, `last_page` (~180 open) | `start_regn_dt`, `end_regn_dt` (IST offsets), `end_date` | Round-by-round deadlines (ideation, prototype, finale) only appear on the detail endpoint. |
| **Hack2skill** | `GET hack2skill.com/api/v1/innovator/public/event/list?page=1&records=100` | None: returns all events at once (~86, including past ones) | `registrationStart`, `registrationEnd` | Event and submission dates are on the event page (`hack2skill.com/event/<slug>`). |
| **Luma** | `GET api.lu.ma/discover/get-paginated-events?query=hackathon&pagination_limit=50` | Cursor (`has_more`, `next_cursor`) | Exact `start_at`, `end_at`, plus the event's timezone | No registration deadline, so the event start is used as the cutoff (marked approximate). Search is fuzzy, so titles are filtered. Results may favour the requester's location; add a city or geo parameter later. Luma's official API needs a Luma Plus key and only covers calendars you manage. |
| **LinkedIn** | None usable | — | — | LinkedIn has no public API for searching posts or events. The Events and Community Management APIs are partner-only, and the User Agreement forbids scraping. So LinkedIn comes in through the user (Phase 6): paste a post URL or text, or forward LinkedIn notification emails, then an LLM extracts the fields. |

### Design decisions that came out of the research

1. **One normalized model** (`Hackathon`). All dates are stored as UTC, and a field is `None` when the source doesn't publish it. Logr never invents a date. Derived dates set `dates_approximate=True`, shown as `~` in the CLI.
2. **Conservative approximations.** Devpost's date-only end becomes 00:00 UTC on that day. That is never later than the real deadline, so a reminder can't be late.
3. **Listing first, detail pages second.** Listing endpoints are cheap (fewer than 40 requests for everything). Fetching detail pages costs one request per hackathon, so Phase 2 does it only for open listings whose dates are approximate or missing.
4. **Per-source isolation.** If one source fails, the others still sync, and every run is logged in `sync_runs`.
5. **Change history.** Organizers move deadlines. Every change to a tracked date is written to `date_changes`, so the tracker can say "deadline extended" instead of silently showing a new date.
6. **Polite fetching.** Logr identifies itself with a user agent, spaces requests at least 0.5 s apart, retries 429 and 5xx responses with backoff and `Retry-After`, and syncs a few times a day rather than every minute.

## 2. The six phases

### Phase 1: Aggregation core ✅ (this commit)
- Python package (`src/logr`), with httpx and pydantic as the only dependencies
- Normalized `Hackathon` model with `next_deadline()` and `is_open()`
- Connectors for Devpost, Devfolio, Unstop, Hack2skill and Luma, each with pagination and dedupe
- SQLite store: upsert, a `date_changes` history and a `sync_runs` log
- CLI: `logr sync`, `logr list` (filter by source, mode, days ahead or text; output as table, JSON or CSV), `logr show`, `logr status`
- Tests run offline against recorded fixtures, including the retry and per-source isolation paths

**Done when:** `logr sync && logr list --within 7` lists real hackathons from all five sources, sorted by next deadline. Met: 498 listings on the first live run.

### Phase 2: Precise deadlines and enrichment
- Detail fetchers: Devpost `data-iso-date` (exact submission deadline), Unstop round timelines, Hack2skill event-page dates, and a check of Devfolio's submission time
- Add a `rounds` table with name, start, end and submission deadline. Many Indian hackathons (Unstop, Hack2skill) have several stages.
- Normalize Luma timezones and add a city parameter for discovery
- Cross-source dedupe: the same hackathon listed on Devfolio and Luma gets matched on fuzzy title, start date and organizer, then shown once with every source link
- Mark listings that have gone (missing for N syncs) as `delisted`

### Phase 3: Personal tracking
- Accounts (start with a single user locally) and a `tracked` table with status `interested → registered → team formed → submitted → shortlisted → won/lost`
- Personal notes, team members, the project repo URL and checklist items for each hackathon
- Filters for saved searches and preferences: online only, region, themes, prize above a threshold, team size
- CLI: `logr track <uid>`, `logr status-set`, `logr mine`

### Phase 4: API and web dashboard
- FastAPI service over the same store (move to Postgres here), with OpenAPI docs
- Next.js dashboard with a deadline timeline, a calendar view, discovery filters, a Kanban board of tracked hackathons, and deadline-change badges
- Auth (OAuth with GitHub or Google)

### Phase 5: Scheduling and reminders
- Scheduled sync (cron, APScheduler or a GitHub Action) every 6 hours, plus health alerts when a connector starts returning 0 results or errors (a sign the endpoint changed)
- Reminders 7 days, 1 day and 3 hours before each tracked deadline by email, Telegram or Discord, plus "deadline moved" alerts built on `date_changes`
- An ICS feed of tracked hackathons, so Google Calendar or Apple Calendar can subscribe, and optional direct Google Calendar sync

### Phase 6: LinkedIn ingestion and smart features
- LinkedIn through user input only: paste a post URL or text, use a browser-extension "save to Logr" button, or forward notification emails. An LLM extracts title, dates and URL into the `Hackathon` model, with a confidence score and a human confirmation step.
- The same extractor handles any other link (college club pages, Discord announcements and so on)
- Recommendations: rank open hackathons by your skills and past participation
- Hardening: per-source contract tests on a schedule, monitoring, rate-limit budgets, and a terms-of-service review before any public launch
