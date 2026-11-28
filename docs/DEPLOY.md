# Running Logr unattended

Phase 5 adds work that has to happen on a schedule: syncing the sources, sending
deadline reminders (email, Telegram, Discord) and keeping the calendar feed fresh.
The calendar feed is served by the web server on request; the other two need a
long-running process or a scheduler.

| What | Default interval | Notes |
|---|---|---|
| Sync all sources | every 6 hours | the first sync waits 1 minute after start-up |
| Notification pass | every 15 minutes | runs immediately at start-up |

Both jobs remember when they last succeeded (table `scheduler_state` in the
database), so restarting the process does not re-sync if a sync ran recently.
A failure in one run is logged and retried at the next interval; it never stops
the other job.

## Option 1: one process for everything (recommended)

```sh
logr serve --schedule                      # dashboard + API + scheduled jobs
logr serve --schedule --sync-every 4 --notify-every 10 --host 0.0.0.0 --port 8000
```

`--sync-every` is in hours, `--notify-every` in minutes. Logs go to stderr.

## Option 2: scheduler without the web server

```sh
logr daemon                                # Ctrl-C or SIGTERM to stop cleanly
logr daemon --sync-every 6 --notify-every 15
```

Use this if you only use the CLI, or run the web server as a separate process
(don't also pass `--schedule` to that server, or both will run the jobs).

## systemd

`/etc/systemd/system/logr.service`:

```ini
[Unit]
Description=Logr hackathon tracker (web + scheduled syncs and reminders)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=logr
WorkingDirectory=/opt/logr
EnvironmentFile=/etc/logr/logr.env
ExecStart=/opt/logr/.venv/bin/logr serve --schedule --host 127.0.0.1 --port 8000
# or: ExecStart=/opt/logr/.venv/bin/logr daemon
Restart=on-failure
RestartSec=30
# SIGTERM stops the scheduler and the server cleanly
KillSignal=SIGTERM
TimeoutStopSec=40

[Install]
WantedBy=multi-user.target
```

`/etc/logr/logr.env` (mode `0600`, owned by root or `logr`: it holds secrets):

```sh
LOGR_DB=/var/lib/logr/logr.db
LOGR_BASE_URL=https://logr.example.org
LOGR_SMTP_HOST=smtp.example.org
LOGR_SMTP_USER=logr@example.org
LOGR_SMTP_PASSWORD=...
LOGR_TELEGRAM_BOT_TOKEN=...
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now logr
journalctl -u logr -f
```

Put a TLS-terminating reverse proxy (Caddy, nginx) in front if the server is
reachable from the internet; calendar apps fetch the feed from their own servers,
so a feed for Google Calendar must be reachable publicly over HTTPS.

## Alternative: cron

If you'd rather not run a long-lived process, call the one-shot commands. Each
`logr notify` is idempotent (it records what it has sent), so running it often
is safe.

```cron
# m   h    dom mon dow  command
17    */6  *   *   *    . /etc/logr/logr.env; /opt/logr/.venv/bin/logr sync    >> /var/log/logr.log 2>&1
*/15  *    *   *   *    . /etc/logr/logr.env; /opt/logr/.venv/bin/logr notify  >> /var/log/logr.log 2>&1
```

(For `. file` to work in cron, write the env file as `export VAR=value` lines.)
`logr notify` exits 1 when a delivery failed, so cron's mail-on-output or your
log monitoring will notice. You still need `logr serve` running somewhere for the
calendar feed URL to work.

## Alternative: GitHub Actions (sketch only; not recommended)

> **Warning:** Logr keeps everything in one SQLite file: tracked hackathons,
> preferences, *and the record of which reminders were already sent*. GitHub
> runners are thrown away after each job, so the database does not persist
> between runs. Without extra work every run starts empty (nothing tracked, so
> nothing to remind about), or, if you restore an old copy, it re-sends
> reminders. You would have to download and upload the database every run
> (e.g. to object storage) and make sure two runs never overlap. Prefer a small
> VM or a home server with `logr serve --schedule`.

```yaml
# .github/workflows/logr.yml: illustration only, see the warning above
on:
  schedule:
    - cron: "*/30 * * * *"
concurrency: logr            # never run two passes at once
jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.13" }
      - run: pip install .
      # - fetch logr.db from persistent storage here (REQUIRED)
      - run: logr --db logr.db sync && logr --db logr.db notify
        env:
          LOGR_SMTP_HOST: ${{ secrets.LOGR_SMTP_HOST }}
          LOGR_SMTP_USER: ${{ secrets.LOGR_SMTP_USER }}
          LOGR_SMTP_PASSWORD: ${{ secrets.LOGR_SMTP_PASSWORD }}
          LOGR_TELEGRAM_BOT_TOKEN: ${{ secrets.LOGR_TELEGRAM_BOT_TOKEN }}
      # - upload logr.db back to persistent storage here (REQUIRED)
```

## Environment variables

Server-wide settings and secrets come from the environment. Per-user
destinations (which email address, which Telegram chat, which Discord webhook)
are preferences (`logr prefs set ...`, or the dashboard's Settings page).

### General

| Variable | What it does |
|---|---|
| `LOGR_DB` | Path of the SQLite database (default `~/.local/share/logr/logr.db`; `--db` overrides). |
| `LOGR_TZ` | Display timezone for the CLI (IANA name, e.g. `Asia/Kolkata`). Reminder texts use each user's `tz` pref, else UTC. |
| `LOGR_LUMA_NEAR` | `;`-separated Luma places to search when syncing (overrides the `luma_near` pref). |
| `LOGR_BASE_URL` | Public URL of the web server, e.g. `https://logr.example.org`. Used for the calendar feed URL (`logr feed-url`), dashboard links in reminders, and OAuth redirect URLs. Default `http://127.0.0.1:8000`. |

### Email (SMTP)

Email is enabled when `LOGR_SMTP_HOST` and a sender (`LOGR_SMTP_FROM` or `LOGR_SMTP_USER`) are set.

| Variable | What it does |
|---|---|
| `LOGR_SMTP_HOST` | SMTP server host name. |
| `LOGR_SMTP_PORT` | Port (default `587`). A non-number is a start-up error. |
| `LOGR_SMTP_USER` | Login user name (also the default sender). |
| `LOGR_SMTP_PASSWORD` | Login password or app password. Secret. |
| `LOGR_SMTP_FROM` | Sender address (default `LOGR_SMTP_USER`). |
| `LOGR_SMTP_STARTTLS` | `true` (default): STARTTLS. `false`: implicit TLS on port 465, plain SMTP on any other port. |

### Telegram

| Variable | What it does |
|---|---|
| `LOGR_TELEGRAM_BOT_TOKEN` | Token of your bot from [@BotFather](https://t.me/BotFather). Secret. Without it, Telegram destinations are reported as "not configured". |

### Discord

No server setting: each user stores their own webhook URL in the
`notify_discord_webhook` pref.

### Authentication (web server)

| Variable | What it does |
|---|---|
| `LOGR_AUTH` | `local` (default; single user, no login, that user is the admin) or `oauth`. |
| `LOGR_SESSION_SECRET` | Signs session cookies; required in `oauth` mode, at least 32 characters. Secret. |
| `LOGR_GITHUB_CLIENT_ID` / `LOGR_GITHUB_CLIENT_SECRET` | GitHub OAuth app (callback `<LOGR_BASE_URL>/api/auth/github/callback`). |
| `LOGR_GOOGLE_CLIENT_ID` / `LOGR_GOOGLE_CLIENT_SECRET` | Google OAuth client (callback `<LOGR_BASE_URL>/api/auth/google/callback`). |
| `LOGR_ADMINS` | Comma-separated user ids (e.g. `github:12345`) who can trigger syncs and **receive sync-health alerts** (failed or empty sources, no successful sync in 24h). In `local` mode the local user gets them. |

## Setting up your channels

```sh
logr prefs set notify_email you@example.com
logr prefs set notify_telegram_chat 123456789
logr prefs set notify_discord_webhook https://discord.com/api/webhooks/<id>/<token>
logr prefs set remind_offsets 7d,1d,3h      # default; units m, h, d, w
logr notify --test                          # sends a test message to each channel
logr notify --dry-run                       # what the next pass would send
```

### Your Telegram chat id

1. Open a chat with the server's bot and send it any message (bots can't
   message you first).
2. Open `https://api.telegram.org/bot<token>/getUpdates` in a browser
   (`<token>` is `LOGR_TELEGRAM_BOT_TOKEN`; only the server admin has it).
3. Find `"chat":{"id": ...}` in the response. That number is your chat id
   (group chats have negative ids). For a public channel you can use
   `@channelname` instead, after adding the bot as an admin of the channel.

If someone else runs the server, ask them for the id or use a helper bot such
as @userinfobot to see your numeric id.

### A Discord webhook

In Discord: Server Settings, then Integrations, then Webhooks, then New Webhook.
Pick the channel, then **Copy Webhook URL**. It looks like
`https://discord.com/api/webhooks/<id>/<token>`.

### The calendar feed

```sh
logr feed-url                 # prints the https:// URL and a webcal:// variant
logr feed-url --rotate        # new secret URL; the old one stops working
logr ics -o deadlines.ics     # a one-off file to import instead
```

Google Calendar: Settings, then Other calendars (+), then **From URL**, and
paste the `https://` URL. Google refreshes subscribed calendars on its own
schedule (often every few hours), so the reminders are the timely channel.

## Security notes

- **The feed URL is a password.** Anyone with it can read your tracked
  hackathons and deadlines; calendar apps can't log in, so the token in the URL
  is the only credential. Don't paste it into shared documents. If it leaks,
  run `logr feed-url --rotate` (or rotate it in the dashboard) and re-subscribe.
- **A Discord webhook URL is a secret.** Anyone with it can post to that
  channel. Logr shows it masked (`logr prefs`) and never logs it. If it leaks,
  delete the webhook in Discord and create a new one.
- The SMTP password, Telegram bot token and session secret belong in the
  environment file (mode `0600`), not in the database, a shell history or a
  repository.
- Serve over HTTPS whenever the server is reachable beyond localhost; feed
  tokens and session cookies travel in every request.
- Back up the database file: it holds your tracking data and the sent-reminder
  log (restoring an old copy can re-send some reminders).
