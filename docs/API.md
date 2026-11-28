# Logr HTTP API (Phase 4 contract)

The web dashboard and any other client talk to this API. `logr serve` runs it with FastAPI. The JSON API lives under `/api`, and the built dashboard (`web/out`) is served at `/`.

## Conventions
- **JSON everywhere.** Timestamps are ISO-8601 UTC with a `Z` suffix, e.g. `"2026-12-04T22:00:00Z"`.
- **Errors** look like `{"detail": "message"}`. The status codes are 400 (bad input), 401 (login needed), 403 (forbidden), 404 (unknown uid, search or todo) and 422 (validation, FastAPI's default shape).
- **A uid contains a colon**, e.g. `unstop:1794479`. In paths it is URL-encoded (`unstop%3A1794479`); the server accepts the raw colon too.
- **CSRF.** Every non-GET request must carry the header `X-Requested-With: logr`; without it the server returns 403. Browsers can't add this header cross-site without CORS, and the API sends no CORS headers.
- **Times are UTC.** The API never formats dates for display. The client renders them in the user's `tz` preference, falling back to the browser's timezone.

## Auth
Two modes, chosen by `LOGR_AUTH`:
- **`local`** (default). There is no login; every request acts as user `local`. This is the same user the CLI uses, so CLI and web share tracking data. `GET /api/me` returns that user.
- **`oauth`.** Users sign in with GitHub and/or Google. These are configured with `LOGR_GITHUB_CLIENT_ID`/`LOGR_GITHUB_CLIENT_SECRET` and `LOGR_GOOGLE_CLIENT_ID`/`LOGR_GOOGLE_CLIENT_SECRET`, plus `LOGR_SESSION_SECRET` (required) and `LOGR_BASE_URL` (e.g. `https://logr.example.com`, used to build callback URLs). User ids are `github:<numeric id>` and `google:<sub>`.
  - The session is a signed, HttpOnly cookie `logr_session` with SameSite=Lax, valid for 30 days. It is marked Secure when `LOGR_BASE_URL` is https.
  - Hackathon browsing is public. Tracking, saved searches and preferences need a signed-in user and return 401 otherwise.
  - Admins are the comma-separated user ids in `LOGR_ADMINS`. In `local` mode the local user is an admin.

| Method & path | Body | Response |
|---|---|---|
| `GET /api/me` | — | `{"user": User \| null, "auth_mode": "local"\|"oauth", "providers": ["github", ...], "is_admin": bool}` |
| `GET /api/auth/{provider}/login?next=/path` | — | 302 to the provider. It stores a random `state` (and `next`, which must be a relative path) in a short-lived signed cookie. Unknown or unconfigured provider → 404. |
| `GET /api/auth/{provider}/callback?code=&state=` | — | Checks `state`, exchanges the code, fetches the profile, upserts the user, sets the session and returns 302 to `next` (default `/`). A bad state → 400. |
| `POST /api/auth/logout` | — | 204 and clears the cookie |

`User` = `{"id": str, "provider": "local"|"github"|"google", "login": str, "name": str, "avatar_url": str|null}`

## Hackathons (public)
`ListingOut` is one hackathon as shown in a list, after cross-source merging:
```json
{
  "uid": "unstop:1794479", "title": "Inceptrix 3.0", "url": "https://...",
  "organizer": "…" , "mode": "online|offline|hybrid|unknown", "location": "Bangalore, Karnataka",
  "themes": ["AI"], "prize": "₹250,000", "prize_usd": 3000.0,
  "participants": 82, "team_size_min": 4, "team_size_max": 5, "image_url": "https://...",
  "source": "unstop", "sources": ["unstop", "devpost"],
  "also_listed": [{"uid": "devpost:32017", "source": "devpost", "url": "https://..."}],
  "registration_opens_at": "…Z", "registration_closes_at": "…Z", "submission_deadline": "…Z",
  "starts_at": "…Z", "ends_at": "…Z", "approx_fields": ["submission_deadline"],
  "next_deadline": {"label": "registration closes", "at": "…Z", "approximate": false} ,
  "tracked_status": "registered"
}
```
`next_deadline` is null when nothing is ahead. `tracked_status` is the current user's status for any uid in the merged group, or null when the hackathon isn't tracked or nobody is signed in.

| Method & path | Response |
|---|---|
| `GET /api/hackathons` | `{"items": [ListingOut], "total": int, "filter": Filter.to_dict(), "saved": str\|null, "now": "…Z"}` |
| `GET /api/hackathons/{uid}` | `HackathonDetail` or 404 |
| `GET /api/calendar?from=…Z&to=…Z&scope=tracked\|all` | `{"events": [CalendarEvent]}` |
| `GET /api/sync/status` | `{"runs": [{"source", "started_at", "finished_at", "fetched", "created", "updated", "enriched", "enrich_errors", "delisted", "error"}], "running": bool}` |
| `POST /api/sync` | Admin only (403 otherwise). Starts `logr sync` in a background thread and returns 202 `{"started": true}`, or 409 if one is already running. |

**Query parameters for `GET /api/hackathons`**
- Repeatable: `source`, `mode`, `theme`
- Single: `within` (days, float), `q`, `region`, `min_prize` (USD), `team_size`, `include_closed` (bool), `merge` (bool, default true), `saved` (name of a saved search, which the other params override), `sort` (`deadline` or `prize`, default `deadline`), `limit` (default 50, max 500), `offset`
- Applying preferences: if the user has a `default_search` pref and the request has no filter params and no `saved` or `no_prefs=1`, it is applied, and `"saved"` in the response names it.

**`HackathonDetail`** is `ListingOut` plus:
- `rounds`: `[{"name", "kind", "starts_at", "ends_at"}]`
- `date_changes`: `[{"field", "old_value", "new_value", "changed_at"}]`
- `first_seen_at`, `last_seen_at`, `delisted_at`
- `tracking`: `TrackedOut` or null

**`CalendarEvent`** is `{"uid", "title", "label", "at", "approximate", "kind": "deadline"|"round_end"|"start"|"end"|"todo", "tracked_status"}`.
- With `scope=tracked` (the default when signed in), events cover only tracked hackathons. Their deadlines are status-aware (`tracking.upcoming`), and todos are included.
- With `scope=all`, events cover every open listing's deadlines, starts and ends in the range.
- Both `from` and `to` are required, and the range can be at most 100 days.

## Tracking (signed in)
**`TrackedOut`**
```json
{"uid", "status", "added_at", "updated_at", "notes", "repo_url", "team": [str],
 "checklist": [{"id": int, "text": str, "done": bool, "due_at": "…Z"|null}],
 "next_action": {"label", "at", "approximate"}|null,
 "history": [{"old": str|null, "new": str, "changed_at": "…Z", "note": str|null}],
 "hackathon": ListingOut|null}
```
`next_action` is computed from the best record of the uid's merged group (see `logr mine`). `hackathon` is null if the listing has gone from the database.

| Method & path | Body | Response |
|---|---|---|
| `GET /api/tracked?status=…` (repeatable) | — | `[TrackedOut]`, sorted by next action (nulls last). Terminal statuses are included; the client groups them. |
| `POST /api/tracked` | `{"uid", "status"?}` | 201 `TrackedOut`, or 200 if it was already tracked; 404 if the uid isn't in the store |
| `PATCH /api/tracked/{uid}` | `{"status"?, "status_note"?, "repo_url"?}` (`repo_url: null` clears it) | `TrackedOut` |
| `DELETE /api/tracked/{uid}` | — | 204 |
| `POST /api/tracked/{uid}/notes` | `{"text"}` | `TrackedOut` |
| `POST /api/tracked/{uid}/team` | `{"name"}` | `TrackedOut` |
| `DELETE /api/tracked/{uid}/team/{name}` | — | `TrackedOut`, or 404 if that name isn't on the team |
| `POST /api/tracked/{uid}/todos` | `{"text", "due_at"?}` | 201 `TrackedOut` |
| `PATCH /api/tracked/{uid}/todos/{id}` | `{"done": bool}` | `TrackedOut` |
| `DELETE /api/tracked/{uid}/todos/{id}` | — | `TrackedOut` |

Any of these on a uid that isn't tracked returns 404.

## Saved searches and preferences (signed in)
| Method & path | Body | Response |
|---|---|---|
| `GET /api/searches` | — | `[{"name", "filter": Filter.to_dict(), "description": Filter.describe(), "is_default": bool}]` |
| `PUT /api/searches/{name}` | `{"filter": Filter.to_dict()}` | 200 with the saved search; 400 for a bad name or an empty filter |
| `DELETE /api/searches/{name}` | — | 204. Also unsets `default_search` if it pointed at this search; 404 if the search doesn't exist. |
| `GET /api/prefs` | — | `{"<key>": {"value": str\|null, "description": str}}` for every key in `PREF_KEYS` |
| `PUT /api/prefs/{key}` | `{"value"}` | 200. Validated like `logr prefs set`; 400 if invalid, 404 for an unknown key. |
| `DELETE /api/prefs/{key}` | — | 204 |

## Notifications and calendar feed (Phase 5)
Behaviour is defined in `logr/notify.py` and `logr/ics.py`.

| Method & path | Auth | Response |
|---|---|---|
| `GET /api/calendar.ics?token=…` | the token is the credential (no cookie) | `text/calendar; charset=utf-8`, the user's `ics.tracked_events`, with `Cache-Control: private, max-age=300`. A missing or unknown token returns 404 (not 401, so token guessing learns nothing). |
| `GET /api/feed` | signed in | `{"url": "<base>/api/calendar.ics?token=…", "webcal_url": "webcal://…"}`. Creates the token on first call. `<base>` is `LOGR_BASE_URL` if set, else the request's own origin. |
| `POST /api/feed/rotate` | signed in | Same shape as `GET /api/feed`, with a new token; the old URL stops working. |
| `GET /api/notify` | signed in | `{"channels": {"email": Ch, "telegram": Ch, "discord": Ch}, "remind_offsets": "7d,1d,3h", "last_pass_at": "…Z"\|null}`. `Ch` = `{"configured": bool, "available": bool, "destination": str\|null}`: `configured` means the user set the pref, `available` means the server has the credentials needed (SMTP, bot token; always true for Discord), and `destination` is the address or chat id. The Discord webhook is masked with `notify.mask_secret`. |
| `POST /api/notify/test` | signed in | `{"results": {"email": "ok" \| "<error>", ...}}` from `notify.send_test`. 400 if the user has no channel configured. |

**Preferences.** Four new keys are set via the existing `PUT /api/prefs/{key}`: `notify_email`, `notify_telegram_chat`, `notify_discord_webhook` and `remind_offsets`. They are validated with `notify.validate_pref`, and an invalid value returns 400 with its message. `GET /api/prefs` masks any key in `searches.SECRET_PREF_KEYS` (currently the Discord webhook) using `notify.mask_secret`. A masked value is never written back: a `PUT` whose value equals the masked form returns 400 with `{"detail": "enter the full webhook URL"}`.
