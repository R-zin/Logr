# Logr web dashboard

The browser UI for Logr. It's a Next.js 15 (App Router) app built as a **static export**: `npm run build` writes plain HTML/JS/CSS to `web/out`, and `logr serve` serves that at `/` next to the JSON API at `/api` (see `../docs/API.md`). There's no Node server in production.

## Development

```bash
# terminal 1: the API on :8000
logr serve

# terminal 2: the dashboard with hot reload on :3000
cd web
npm install
npm run dev
```

In development `next.config.mjs` proxies `/api/*` to `http://127.0.0.1:8000` (override with `LOGR_API_URL`), so the browser still talks to a single origin and the session cookie works.

## Production

```bash
cd web
npm ci
npm run build        # → web/out
logr serve           # serves web/out at / and the API at /api
```

## Without the Python backend

`scripts/mock-api.mjs` is a dependency-free Node server. It serves `web/out` and answers every `/api` endpoint with realistic in-memory data. This includes a merged listing, approximate dates, round timelines and tracked hackathons with to-dos.

```bash
npm run build && npm run mock                 # http://127.0.0.1:3001, local (single-user) mode
MOCK_AUTH=oauth npm run mock                  # OAuth mode, signed in
MOCK_AUTH=anon  npm run mock                  # OAuth mode, signed out
PORT=4000 MOCK_TZ=Asia/Kolkata npm run mock   # other port, preset tz preference
```

## Scripts

| Script | What it does |
|---|---|
| `npm run dev` | Next dev server with the `/api` proxy |
| `npm run build` | Static export to `out/` |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run mock` | Mock API and static server (needs a build) |

## Layout

```
app/                 one folder per page (all client components; data is fetched from /api in the browser)
  page.tsx           Discover: filters kept in the URL query, results, load more, save search
  hackathon/         Detail (?uid=…): dates, round timeline, date changes, tracking panel
  mine/              Kanban board by status, plus "Next up"
  calendar/          Month grid (agenda list on narrow screens)
  searches/          Saved searches
  settings/          Preferences and sync status
components/          Nav, FilterBar, HackathonRow, TrackingPanel, TrackButton, ui primitives, inline SVG icons
lib/api.ts           Typed API client: types mirror docs/API.md, CSRF header on non-GET, ApiError(status, detail)
lib/time.ts          Intl-only date formatting, relative times, tz ↔ UTC conversion
lib/session.tsx      /api/me + prefs context; display timezone = `tz` pref or the browser's zone
```

Pages take their parameters from query strings (for example `/hackathon/?uid=unstop%3A1794479`) because a static export can't have dynamic route segments. Colours are CSS variables in `app/globals.css`, with a dark set under `prefers-color-scheme: dark`.
