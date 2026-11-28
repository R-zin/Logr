#!/usr/bin/env node
// Dependency-free mock of the Logr API (docs/API.md) that also serves the
// static export in web/out. For working on the UI without the Python server.
//
//   npm run build && npm run mock            # http://127.0.0.1:3001
//   MOCK_AUTH=oauth npm run mock             # OAuth mode, signed in
//   MOCK_AUTH=anon npm run mock              # OAuth mode, signed out
//   PORT=4000 MOCK_TZ=Asia/Kolkata npm run mock
//   MOCK_SCHEDULE=off npm run mock           # no reminder pass has run (last_pass_at null)
//
// Notifications: email is set up, Telegram is set up but the "server" has no
// bot token, Discord has a (masked) webhook. The test send fails for Telegram.
//
// State is in memory and resets on restart. Dates are relative to "now" so
// the UI always has upcoming deadlines to show.

import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { extname, join, normalize, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { randomBytes } from "node:crypto";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "out");
const PORT = Number(process.env.PORT || 3001);
const AUTH = process.env.MOCK_AUTH || "local"; // local | oauth | anon

const NOW = Date.now();
const H = 3600_000;
const D = 24 * H;
const iso = (ms) => new Date(Math.round(ms / 60000) * 60000).toISOString().replace(/\.\d{3}Z$/, "Z");
const at = (days, hours = 0) => iso(NOW + days * D + hours * H);
// Pin an offset to a round hour so times look like real deadlines.
const atH = (days, hourUtc, min = 0) => {
  const d = new Date(NOW + days * D);
  d.setUTCHours(hourUtc, min, 0, 0);
  return iso(d.getTime());
};

// --- users -------------------------------------------------------------------

const LOCAL_USER = { id: "local", provider: "local", login: "local", name: "Local user", avatar_url: null };
const GH_USER = { id: "github:583231", provider: "github", login: "asha-dev", name: "Asha Rao", avatar_url: null };
let signedIn = AUTH !== "anon";
const currentUser = () => (AUTH === "local" ? LOCAL_USER : signedIn ? GH_USER : null);

// --- hackathons ----------------------------------------------------------------

function listing(p) {
  return {
    organizer: null,
    mode: "online",
    location: null,
    themes: [],
    prize: null,
    prize_usd: null,
    participants: null,
    team_size_min: null,
    team_size_max: null,
    image_url: null,
    also_listed: [],
    registration_opens_at: null,
    registration_closes_at: null,
    submission_deadline: null,
    starts_at: null,
    ends_at: null,
    approx_fields: [],
    rounds: [],
    date_changes: [],
    first_seen_at: at(-20),
    last_seen_at: at(0, -3),
    delisted_at: null,
    ...p,
    sources: p.sources ?? [p.source],
  };
}

const HACKATHONS = [
  listing({
    uid: "unstop:1794479",
    title: "Inceptrix 3.0",
    url: "https://unstop.com/hackathons/inceptrix-30-1794479",
    organizer: "JSS Academy of Technical Education, Bengaluru",
    mode: "hybrid",
    location: "Bangalore, Karnataka",
    themes: ["AI", "Sustainability", "FinTech"],
    prize: "₹250,000",
    prize_usd: 3000,
    participants: 1824,
    team_size_min: 2,
    team_size_max: 4,
    source: "unstop",
    sources: ["unstop", "devpost"],
    also_listed: [{ uid: "devpost:32017", source: "devpost", url: "https://inceptrix-hackathon.devpost.com/" }],
    registration_opens_at: at(-26),
    registration_closes_at: atH(27, 18, 29),
    submission_deadline: atH(27, 18, 29),
    starts_at: atH(84, 2, 30),
    ends_at: atH(85, 14, 30),
    rounds: [
      { name: "Round 1: Idea Submission", kind: "submission", starts_at: at(-26), ends_at: atH(27, 18, 29) },
      { name: "Evaluation Period", kind: "judging", starts_at: atH(35, 18, 30), ends_at: atH(45, 18, 29) },
      { name: "Finalist Announcement", kind: "results", starts_at: atH(46, 18, 30), ends_at: atH(46, 18, 30) },
      { name: "Final Round Registration", kind: "registration", starts_at: atH(47, 18, 30), ends_at: atH(55, 18, 29) },
      { name: "On-Ground Final Round", kind: "event", starts_at: atH(84, 2, 30), ends_at: atH(85, 14, 30) },
    ],
    date_changes: [
      { field: "registration_closes_at", old_value: atH(20, 18, 29), new_value: atH(27, 18, 29), changed_at: at(-2) },
      { field: "submission_deadline", old_value: atH(20, 18, 29), new_value: atH(27, 18, 29), changed_at: at(-2) },
    ],
  }),
  listing({
    uid: "devfolio:makerspark",
    title: "MakerSpark",
    url: "https://makerspark.devfolio.co/",
    organizer: "Spark Labs",
    mode: "offline",
    location: "Pune, Maharashtra",
    themes: ["Hardware", "IoT"],
    prize: "₹100,000",
    prize_usd: 1200,
    participants: 312,
    team_size_min: 1,
    team_size_max: 4,
    source: "devfolio",
    registration_closes_at: at(0, 9),
    starts_at: at(3),
    ends_at: at(4, 12),
    rounds: [{ name: "Hackathon", kind: "event", starts_at: at(3), ends_at: at(4, 12) }],
  }),
  listing({
    uid: "luma:buenos-buildathon",
    title: "Lovable Buildathon @ Buenos Aires",
    url: "https://lu.ma/lovable-ba",
    organizer: "Lovable",
    mode: "offline",
    location: "Buenos Aires, Argentina",
    themes: ["AI", "No-code"],
    source: "luma",
    registration_closes_at: atH(2, 22),
    starts_at: atH(2, 22),
    ends_at: atH(3, 4),
    approx_fields: ["registration_closes_at"],
  }),
  listing({
    uid: "devpost:kiroween-2026",
    title: "Kiroween 2026",
    url: "https://kiroween.devpost.com/",
    organizer: "Kiro",
    mode: "online",
    themes: ["Developer Tools", "AI", "Gaming"],
    prize: "$100,000",
    prize_usd: 100000,
    participants: 6120,
    team_size_min: 1,
    team_size_max: 4,
    source: "devpost",
    registration_opens_at: at(-30),
    submission_deadline: atH(8, 22),
    starts_at: at(-30),
    ends_at: atH(8, 22),
    rounds: [
      { name: "Submission period", kind: "submission", starts_at: at(-30), ends_at: atH(8, 22) },
      { name: "Judging", kind: "judging", starts_at: atH(9, 0), ends_at: atH(22, 0) },
      { name: "Winners announced", kind: "results", starts_at: atH(24, 17), ends_at: atH(24, 17) },
    ],
  }),
  listing({
    uid: "unstop:hacksprint-3",
    title: "Hacksprint 3.0",
    url: "https://unstop.com/hackathons/hacksprint-30",
    organizer: "IEEE Student Branch, VIT Chennai",
    mode: "hybrid",
    location: "Chennai, Tamil Nadu",
    themes: ["Healthcare", "AI", "Open Innovation"],
    prize: "₹150,000",
    prize_usd: 1800,
    participants: 940,
    team_size_min: 2,
    team_size_max: 5,
    source: "unstop",
    sources: ["unstop", "devfolio"],
    also_listed: [{ uid: "devfolio:hacksprint3", source: "devfolio", url: "https://hacksprint3.devfolio.co/" }],
    registration_closes_at: atH(31, 18, 29),
    submission_deadline: atH(38, 18, 29),
    starts_at: atH(44, 3, 30),
    ends_at: atH(45, 12, 30),
    approx_fields: ["submission_deadline"],
    rounds: [
      { name: "Registrations", kind: "registration", starts_at: at(-10), ends_at: atH(31, 18, 29) },
      { name: "PPT Submission", kind: "submission", starts_at: at(-10), ends_at: atH(38, 18, 29) },
      { name: "24h Finale", kind: "event", starts_at: atH(44, 3, 30), ends_at: atH(45, 12, 30) },
    ],
  }),
  listing({
    uid: "hack2skill:gen-ai-exchange",
    title: "Gen AI Exchange Hackathon",
    url: "https://hack2skill.com/gen-ai-exchange",
    organizer: "Google Cloud × Hack2skill",
    mode: "online",
    themes: ["Generative AI", "Cloud"],
    prize: "₹1,000,000",
    prize_usd: 12000,
    participants: 21450,
    team_size_min: 1,
    team_size_max: 5,
    source: "hack2skill",
    registration_closes_at: atH(5, 18, 29),
    submission_deadline: atH(12, 18, 29),
    rounds: [
      { name: "Prototype Submission", kind: "submission", starts_at: at(-4), ends_at: atH(12, 18, 29) },
      { name: "Top 100 announced", kind: "results", starts_at: atH(20, 6, 30), ends_at: atH(20, 6, 30) },
      { name: "Grand Finale", kind: "event", starts_at: atH(32, 3, 30), ends_at: atH(32, 12, 30) },
    ],
  }),
  listing({
    uid: "devpost:climate-code-2026",
    title: "Climate Code Challenge",
    url: "https://climatecode.devpost.com/",
    organizer: "Open Earth Foundation",
    mode: "online",
    themes: ["Climate", "Data"],
    prize: "$25,000",
    prize_usd: 25000,
    participants: 1430,
    team_size_max: 5,
    source: "devpost",
    submission_deadline: atH(19, 23, 45),
    starts_at: at(-12),
    ends_at: atH(19, 23, 45),
  }),
  listing({
    uid: "devfolio:ethindia-villa",
    title: "ETHIndia Villa Residency",
    url: "https://ethindiavilla.devfolio.co/",
    organizer: "Devfolio",
    mode: "offline",
    location: "Goa, India",
    themes: ["Web3", "Ethereum"],
    prize: "$40,000",
    prize_usd: 40000,
    team_size_min: 1,
    team_size_max: 4,
    source: "devfolio",
    registration_closes_at: atH(14, 18, 29),
    starts_at: atH(40, 4, 30),
    ends_at: atH(42, 12, 30),
  }),
  listing({
    uid: "luma:sf-agents-night",
    title: "Agents Hack Night SF",
    url: "https://lu.ma/agents-sf",
    organizer: "AI Tinkerers",
    mode: "offline",
    location: "San Francisco, CA",
    themes: ["AI Agents"],
    source: "luma",
    registration_closes_at: atH(10, 1),
    starts_at: atH(10, 1),
    ends_at: atH(10, 6),
    approx_fields: ["registration_closes_at"],
  }),
  listing({
    uid: "unstop:smart-city-2026",
    title: "Smart City Ideathon 2026",
    url: "https://unstop.com/hackathons/smart-city-ideathon",
    organizer: "Municipal Innovation Cell",
    mode: "online",
    themes: ["Smart Cities", "GovTech"],
    prize: "₹50,000",
    prize_usd: 600,
    team_size_min: 2,
    team_size_max: 3,
    source: "unstop",
    registration_closes_at: at(-3),
    submission_deadline: at(-1),
    ends_at: at(-1),
  }),
  ...Array.from({ length: 22 }, (_, i) =>
    listing({
      uid: `devpost:filler-${i}`,
      title: ["Build with Llamas", "Rust for Good", "OpenHealth Sprint", "Campus Commerce Jam", "Space Apps Local", "Quantum Weekend"][i % 6] + ` #${i + 1}`,
      url: `https://filler-${i}.devpost.com/`,
      organizer: ["Meta", "Rust Foundation", "WHO Digital", "Shopify", "NASA", "IBM"][i % 6],
      mode: ["online", "hybrid", "offline"][i % 3],
      location: i % 3 === 0 ? null : ["Berlin, Germany", "Toronto, Canada", "Singapore"][i % 3],
      themes: [["AI"], ["Systems"], ["Healthcare"], ["E-commerce"], ["Space"], ["Quantum"]][i % 6],
      prize: i % 4 === 3 ? null : `$${(5 + i * 2) * 1000}`.replace(/(\d)(?=(\d{3})+$)/g, "$1,"),
      prize_usd: i % 4 === 3 ? null : (5 + i * 2) * 1000,
      source: "devpost",
      submission_deadline: atH(22 + i * 3, 23, 0),
    }),
  ),
];

const byUid = new Map(HACKATHONS.map((h) => [h.uid, h]));
for (const h of HACKATHONS) for (const a of h.also_listed) byUid.set(a.uid, h);

function nextDeadline(h) {
  const cands = [
    ["registration closes", h.registration_closes_at, "registration_closes_at"],
    ["submission due", h.submission_deadline, "submission_deadline"],
  ];
  for (const r of h.rounds) if (r.kind === "submission" && r.ends_at) cands.push([`${r.name} ends`, r.ends_at, null]);
  const ahead = cands
    .filter(([, t]) => t && Date.parse(t) > Date.now())
    .sort((a, b) => Date.parse(a[1]) - Date.parse(b[1]));
  if (!ahead.length) return null;
  const [label, t, field] = ahead[0];
  return { label, at: t, approximate: field ? h.approx_fields.includes(field) : false };
}

function isOpen(h) {
  return !h.delisted_at && nextDeadline(h) !== null;
}

// --- per-user state --------------------------------------------------------------

const tracked = new Map();
let todoSeq = 10;
const searches = new Map([
  ["blr-ai", { sources: undefined, themes: ["ai"], region: "bengaluru" }],
  ["online-big", { modes: ["online"], min_prize_usd: 10000 }],
]);
const prefs = {
  tz: process.env.MOCK_TZ || null,
  luma_near: "bengaluru",
  default_search: null,
  notify_email: "asha.rao@example.com",
  notify_telegram_chat: "481516234",
  notify_discord_webhook: "https://discord.com/api/webhooks/1187364520193847296/kQ3vX9-tLr2mYpZbN8wHc5uJ0aEfGs7dRiOq1VnWxKe4abcd",
  remind_offsets: null,
};
const PREF_DESC = {
  tz: "IANA timezone for displayed dates (used when --tz and $LOGR_TZ are absent)",
  luma_near: "';'-separated Luma places for sync (used when --luma-near and $LOGR_LUMA_NEAR are absent)",
  default_search: "saved search applied by `logr list` unless --saved or --no-prefs is given",
  notify_email: "email address for reminders",
  notify_telegram_chat: "Telegram chat id (or @channel) for reminders",
  notify_discord_webhook: "Discord webhook URL for reminders",
  remind_offsets: "how long before a deadline to remind, e.g. 7d,1d,3h",
};

// --- notifications & calendar feed (Phase 5) ------------------------------------

const SECRET_PREF_KEYS = new Set(["notify_discord_webhook"]);
// What the "server" has configured: SMTP yes, Telegram bot token no.
const SERVER = { email: true, telegram: false, discord: true };
const DEFAULT_OFFSETS = "7d,1d,3h";
let lastPassAt = process.env.MOCK_SCHEDULE === "off" ? null : at(0, -0.2);
let feedToken = null;

function maskSecret(v) {
  try {
    const u = new URL(v);
    return `${u.protocol}//${u.host}/…/•••••${v.slice(-4)}`;
  } catch {
    return `•••••${v.slice(-4)}`;
  }
}
const prefOut = (k) => (prefs[k] != null && SECRET_PREF_KEYS.has(k) ? maskSecret(prefs[k]) : prefs[k]);

const UNIT_S = { m: 60, h: 3600, d: 86400, w: 604800 };
function parseOffsets(text) {
  const parts = String(text).split(",").map((p) => p.trim().toLowerCase()).filter(Boolean);
  if (!parts.length) throw new Error("give at least one offset, e.g. 7d,1d,3h");
  const secs = new Map();
  for (const p of parts) {
    const m = /^(\d+)\s*([mhdw])$/.exec(p);
    if (!m) throw new Error(`'${p}' isn't an offset: use a number and a unit (m, h, d, w), e.g. 3h`);
    const s = Number(m[1]) * UNIT_S[m[2]];
    if (s <= 0) throw new Error(`'${p}' must be more than zero`);
    if (s > 60 * 86400) throw new Error(`'${p}' is more than 60 days`);
    secs.set(s, `${m[1]}${m[2]}`);
  }
  if (secs.size > 6) throw new Error("at most 6 offsets");
  const fmt = (s) => (s % 604800 === 0 ? `${s / 604800}w` : s % 86400 === 0 ? `${s / 86400}d` : s % 3600 === 0 ? `${s / 3600}h` : `${s / 60}m`);
  return [...secs.keys()].sort((a, b) => b - a).map(fmt).join(",");
}

/** Mirrors notify.validate_pref: returns the value to store or throws Error(message). */
function validatePref(key, v) {
  if (key === "notify_email") {
    if (!/^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]{2,}$/.test(v)) throw new Error(`'${v}' doesn't look like an email address`);
    const [local, domain] = v.split("@");
    return `${local}@${domain.toLowerCase()}`;
  }
  if (key === "notify_telegram_chat") {
    if (!/^-?\d+$/.test(v) && !/^@[A-Za-z][A-Za-z0-9_]{4,31}$/.test(v))
      throw new Error("a Telegram chat id is a number (e.g. 123456789, or -100… for groups) or @channelname");
    return v;
  }
  if (key === "notify_discord_webhook") {
    if (prefs.notify_discord_webhook && v === maskSecret(prefs.notify_discord_webhook)) throw new Error("enter the full webhook URL");
    if (!/^https:\/\/(?:(?:canary|ptb)\.)?discord(?:app)?\.com\/api\/webhooks\/\d+\/[\w-]+$/.test(v))
      throw new Error("a Discord webhook URL looks like https://discord.com/api/webhooks/<id>/<token>");
    return v;
  }
  if (key === "remind_offsets") return parseOffsets(v);
  return v;
}

function notifyStatus() {
  const ch = (name, key) => ({
    configured: prefs[key] != null,
    available: SERVER[name],
    destination: prefs[key] == null ? null : prefOut(key),
  });
  return {
    channels: { email: ch("email", "notify_email"), telegram: ch("telegram", "notify_telegram_chat"), discord: ch("discord", "notify_discord_webhook") },
    remind_offsets: prefs.remind_offsets ?? DEFAULT_OFFSETS,
    last_pass_at: lastPassAt,
  };
}

function feedOut(req) {
  if (!feedToken) feedToken = randomBytes(24).toString("base64url");
  const base = `http://${req.headers.host ?? `127.0.0.1:${PORT}`}`;
  const url = `${base}/api/calendar.ics?token=${feedToken}`;
  return { url, webcal_url: url.replace(/^https?:/, "webcal:") };
}

function icsFeed() {
  const esc = (s) => String(s).replace(/[\\;,]/g, (c) => "\\" + c).replace(/\n/g, "\\n");
  const stamp = (t) => t.replace(/[-:]/g, "").replace(/\.\d+/, "");
  const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Logr mock//EN", "X-WR-CALNAME:Logr", "CALSCALE:GREGORIAN"];
  for (const t of tracked.values()) {
    const h = byUid.get(t.uid);
    const d = nextAction(t);
    if (!h || !d) continue;
    lines.push("BEGIN:VEVENT", `UID:${esc(t.uid)}-${stamp(d.at)}@logr`, `DTSTAMP:${stamp(iso(Date.now()))}`, `DTSTART:${stamp(d.at)}`, `DTEND:${stamp(d.at)}`, `SUMMARY:${esc(`${h.title}: ${d.label}`)}`, `URL:${h.url}`, "END:VEVENT");
  }
  lines.push("END:VCALENDAR");
  return lines.join("\r\n") + "\r\n";
}

function track(uid, status, extra = {}) {
  const t = {
    uid,
    status,
    added_at: at(-9),
    updated_at: at(-1),
    notes: "",
    repo_url: null,
    team: [],
    checklist: [],
    history: [{ old: null, new: "interested", changed_at: at(-9), note: null }],
    ...extra,
  };
  tracked.set(uid, t);
  return t;
}
track("unstop:1794479", "registered", {
  notes: `[${at(-6).slice(0, 10)} 09:12 UTC] Problem statement 3 (supply-chain carbon) looks strongest.\n[${at(-2).slice(0, 10)} 17:40 UTC] Deadline extended by a week. Use it for the demo video.`,
  repo_url: "https://github.com/asha-dev/carbon-trace",
  team: ["Asha", "Vikram", "Meera"],
  checklist: [
    { id: 1, text: "Pick problem statement", done: true, due_at: null },
    { id: 2, text: "Finish idea deck", done: false, due_at: atH(4, 18, 29) },
    { id: 3, text: "Record 2-minute demo video", done: false, due_at: atH(25, 18, 29) },
  ],
  history: [
    { old: null, new: "interested", changed_at: at(-9), note: null },
    { old: "interested", new: "registered", changed_at: at(-6), note: "team of 4" },
  ],
});
track("devpost:kiroween-2026", "interested");
track("hack2skill:gen-ai-exchange", "team_formed", {
  team: ["Asha", "Rohan"],
  checklist: [{ id: 4, text: "Set up Vertex AI project", done: false, due_at: at(0, 14) }],
});
track("devfolio:makerspark", "submitted", {
  history: [
    { old: null, new: "interested", changed_at: at(-15), note: null },
    { old: "interested", new: "submitted", changed_at: at(-1), note: null },
  ],
});
track("devpost:climate-code-2026", "shortlisted");
track("unstop:smart-city-2026", "lost");
track("devfolio:ethindia-villa", "withdrawn");

function nextAction(t) {
  const h = byUid.get(t.uid);
  const cands = [];
  for (const c of t.checklist) if (!c.done && c.due_at) cands.push({ label: `todo: ${c.text}`, at: c.due_at, approximate: false });
  if (h && ["interested"].includes(t.status) && h.registration_closes_at)
    cands.push({ label: "registration closes", at: h.registration_closes_at, approximate: h.approx_fields.includes("registration_closes_at") });
  if (h && ["interested", "registered", "team_formed"].includes(t.status)) {
    if (h.submission_deadline)
      cands.push({ label: "submission due", at: h.submission_deadline, approximate: h.approx_fields.includes("submission_deadline") });
  }
  if (h && ["submitted", "shortlisted"].includes(t.status)) {
    for (const r of h.rounds) if (["results", "event"].includes(r.kind) && r.starts_at) cands.push({ label: r.name, at: r.starts_at, approximate: false });
    if (h.starts_at) cands.push({ label: "event starts", at: h.starts_at, approximate: false });
  }
  if (["won", "lost", "withdrawn"].includes(t.status)) return null;
  const ahead = cands.filter((c) => Date.parse(c.at) > Date.now()).sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  return ahead[0] ?? null;
}

function trackedStatus(h) {
  if (!currentUser()) return null;
  for (const uid of [h.uid, ...h.also_listed.map((a) => a.uid)]) if (tracked.has(uid)) return tracked.get(uid).status;
  return null;
}

function listingOut(h) {
  // eslint-disable-next-line no-unused-vars
  const { rounds, date_changes, first_seen_at, last_seen_at, delisted_at, ...rest } = h;
  return { ...rest, next_deadline: nextDeadline(h), tracked_status: trackedStatus(h) };
}

function trackedOut(t) {
  const h = byUid.get(t.uid);
  return { ...t, next_action: nextAction(t), hackathon: h ? listingOut(h) : null };
}

function detailOut(h) {
  const t = currentUser() ? [h.uid, ...h.also_listed.map((a) => a.uid)].map((u) => tracked.get(u)).find(Boolean) : null;
  return {
    ...listingOut(h),
    rounds: h.rounds,
    date_changes: h.date_changes,
    first_seen_at: h.first_seen_at,
    last_seen_at: h.last_seen_at,
    delisted_at: h.delisted_at,
    tracking: t ? trackedOut(t) : null,
  };
}

function describe(f) {
  const p = [];
  if (f.sources?.length) p.push("from " + f.sources.join(", "));
  if (f.modes?.length) p.push(f.modes.join(", "));
  if (f.search) p.push(`matching "${f.search}"`);
  if (f.themes?.length) p.push("themes " + f.themes.join(", "));
  if (f.region) p.push(`in ${f.region}`);
  if (f.min_prize_usd != null) p.push(`≥ $${Number(f.min_prize_usd).toLocaleString("en-US")}`);
  if (f.team_size != null) p.push(`team of ${f.team_size}`);
  if (f.within_days != null) p.push(`within ${f.within_days}d`);
  if (f.include_closed) p.push("incl. closed");
  return p.length ? p.join(" · ") : "(everything)";
}

function clean(f) {
  return Object.fromEntries(Object.entries(f).filter(([, v]) => v !== undefined && v !== null && v !== "" && !(Array.isArray(v) && !v.length) && v !== false));
}

function filterFromQuery(sp) {
  const num = (k) => (sp.get(k) ? Number(sp.get(k)) : undefined);
  return clean({
    sources: sp.getAll("source"),
    modes: sp.getAll("mode"),
    within_days: num("within"),
    search: sp.get("q") || undefined,
    include_closed: ["true", "1"].includes(sp.get("include_closed") ?? "") || undefined,
    themes: sp.getAll("theme"),
    region: sp.get("region") || undefined,
    min_prize_usd: num("min_prize"),
    team_size: num("team_size"),
  });
}

function matches(h, f) {
  const nd = nextDeadline(h);
  if (!f.include_closed && !isOpen(h)) return false;
  if (f.within_days != null && (!nd || Date.parse(nd.at) > Date.now() + f.within_days * D)) return false;
  if (f.sources?.length && !f.sources.includes(h.source)) return false;
  if (f.modes?.length && !f.modes.includes(h.mode)) return false;
  if (f.search) {
    const hay = [h.title, h.organizer, h.location, ...h.themes].join(" ").toLowerCase();
    if (!hay.includes(f.search.toLowerCase())) return false;
  }
  if (f.themes?.length && !f.themes.some((w) => h.themes.some((t) => t.toLowerCase().includes(w.toLowerCase())))) return false;
  if (f.region && !(h.location ?? "").toLowerCase().includes(f.region.toLowerCase().replace("bengaluru", "bangalore"))) return false;
  if (f.min_prize_usd != null && (h.prize_usd == null || h.prize_usd < f.min_prize_usd)) return false;
  if (f.team_size != null) {
    const lo = h.team_size_min ?? 1;
    const hi = h.team_size_max ?? Infinity;
    if (f.team_size < lo || f.team_size > hi) return false;
  }
  return true;
}

// --- http -------------------------------------------------------------------------

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".txt": "text/plain; charset=utf-8",
  ".woff2": "font/woff2",
};

function send(res, status, body) {
  if (status === 204 || body === undefined) {
    res.writeHead(status);
    return res.end();
  }
  const data = JSON.stringify(body);
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(data);
}
const err = (res, status, detail) => send(res, status, { detail });

async function readBody(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  if (!chunks.length) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    return null;
  }
}

let syncRunning = false;
let syncRuns = ["devpost", "devfolio", "unstop", "hack2skill", "luma"].map((s, i) => ({
  source: s,
  started_at: at(0, -5 - i * 0.05),
  finished_at: at(0, -4.9 - i * 0.05),
  fetched: [412, 96, 230, 41, 58][i],
  created: [3, 1, 6, 0, 4][i],
  updated: [27, 8, 31, 2, 9][i],
  enriched: [150, 40, 150, 12, 30][i],
  enrich_errors: [0, 0, 2, 0, 0][i],
  delisted: [1, 0, 3, 0, 2][i],
  error: s === "hack2skill" ? "HTTP 503 from hack2skill.com (retried 3×)" : null,
}));

async function api(req, res, url) {
  const path = url.pathname.replace(/^\/api/, "");
  const m = req.method;
  const sp = url.searchParams;
  if (m !== "GET" && m !== "HEAD" && req.headers["x-requested-with"] !== "logr") return err(res, 403, "missing X-Requested-With header");
  const user = currentUser();
  const needUser = () => {
    if (!user) {
      err(res, 401, "login required");
      return false;
    }
    return true;
  };
  let mm;

  if (path === "/me" && m === "GET")
    return send(res, 200, { user, auth_mode: AUTH === "local" ? "local" : "oauth", providers: AUTH === "local" ? [] : ["github", "google"], is_admin: !!user });
  if ((mm = path.match(/^\/auth\/([^/]+)\/login$/)) && m === "GET") {
    if (AUTH === "local" || !["github", "google"].includes(mm[1])) return err(res, 404, "unknown provider");
    signedIn = true;
    res.writeHead(302, { Location: sp.get("next") || "/" });
    return res.end();
  }
  if (path === "/auth/logout" && m === "POST") {
    signedIn = false;
    return send(res, 204);
  }

  if (path === "/hackathons" && m === "GET") {
    const filterKeys = ["source", "mode", "theme", "within", "q", "region", "min_prize", "team_size", "include_closed"];
    let saved = sp.get("saved") || null;
    if (!saved && user && prefs.default_search && !sp.get("no_prefs") && !filterKeys.some((k) => sp.has(k))) saved = prefs.default_search;
    let f = filterFromQuery(sp);
    if (saved) {
      if (!searches.has(saved)) return err(res, 404, `no saved search ${saved}`);
      f = { ...clean(searches.get(saved)), ...f };
    }
    let items = HACKATHONS.filter((h) => matches(h, f));
    if (sp.get("sort") === "prize") items.sort((a, b) => (b.prize_usd ?? -1) - (a.prize_usd ?? -1));
    else
      items.sort((a, b) => {
        const x = nextDeadline(a)?.at;
        const y = nextDeadline(b)?.at;
        return (x ? Date.parse(x) : Infinity) - (y ? Date.parse(y) : Infinity);
      });
    const limit = Math.min(Number(sp.get("limit") || 50), 500);
    const offset = Number(sp.get("offset") || 0);
    await new Promise((r) => setTimeout(r, 250));
    return send(res, 200, { items: items.slice(offset, offset + limit).map(listingOut), total: items.length, filter: f, saved, now: iso(Date.now()) });
  }
  if ((mm = path.match(/^\/hackathons\/(.+)$/)) && m === "GET") {
    const h = byUid.get(decodeURIComponent(mm[1]));
    return h ? send(res, 200, detailOut(h)) : err(res, 404, "unknown uid");
  }
  if (path === "/calendar" && m === "GET") {
    const from = Date.parse(sp.get("from") ?? "");
    const to = Date.parse(sp.get("to") ?? "");
    if (!from || !to) return err(res, 400, "from and to are required");
    if (to - from > 100 * D) return err(res, 400, "range is at most 100 days");
    const scope = sp.get("scope") || (user ? "tracked" : "all");
    if (scope === "tracked" && !needUser()) return;
    const events = [];
    const inRange = (t) => t && Date.parse(t) >= from && Date.parse(t) < to;
    const push = (h, label, t, kind, approximate = false) =>
      inRange(t) && events.push({ uid: h.uid, title: h.title, label, at: t, approximate, kind, tracked_status: trackedStatus(h) });
    const hs = scope === "tracked" ? [...tracked.values()].filter((t) => !["won", "lost", "withdrawn"].includes(t.status)).map((t) => byUid.get(t.uid)) : HACKATHONS.filter(isOpen);
    for (const h of hs) {
      if (!h) continue;
      push(h, "registration closes", h.registration_closes_at, "deadline", h.approx_fields.includes("registration_closes_at"));
      push(h, "submission due", h.submission_deadline, "deadline", h.approx_fields.includes("submission_deadline"));
      push(h, "starts", h.starts_at, "start");
      push(h, "ends", h.ends_at, "end");
      for (const r of h.rounds) if (r.ends_at !== h.submission_deadline && r.ends_at !== h.ends_at) push(h, `${r.name} ends`, r.ends_at, "round_end");
      if (scope === "tracked") for (const c of tracked.get(h.uid)?.checklist ?? []) if (!c.done) push(h, `todo: ${c.text}`, c.due_at, "todo");
    }
    events.sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
    return send(res, 200, { events });
  }
  if (path === "/sync/status" && m === "GET") return send(res, 200, { runs: syncRuns, running: syncRunning });
  if (path === "/sync" && m === "POST") {
    if (!user) return err(res, 403, "admins only");
    if (syncRunning) return err(res, 409, "a sync is already running");
    syncRunning = true;
    setTimeout(() => {
      syncRunning = false;
      syncRuns = syncRuns.map((r) => ({ ...r, started_at: iso(Date.now() - 8000), finished_at: iso(Date.now()), error: null }));
    }, 8000);
    return send(res, 202, { started: true });
  }

  // tracking
  if (path === "/tracked" && m === "GET") {
    if (!needUser()) return;
    const want = sp.getAll("status");
    const list = [...tracked.values()].filter((t) => !want.length || want.includes(t.status)).map(trackedOut);
    list.sort((a, b) => (a.next_action ? Date.parse(a.next_action.at) : Infinity) - (b.next_action ? Date.parse(b.next_action.at) : Infinity));
    return send(res, 200, list);
  }
  if (path === "/tracked" && m === "POST") {
    if (!needUser()) return;
    const b = await readBody(req);
    if (!b?.uid) return err(res, 422, "uid is required");
    if (!byUid.has(b.uid)) return err(res, 404, "unknown uid");
    if (tracked.has(b.uid)) return send(res, 200, trackedOut(tracked.get(b.uid)));
    const st = b.status || "interested";
    return send(res, 201, trackedOut(track(b.uid, st, { added_at: iso(Date.now()), updated_at: iso(Date.now()), history: [{ old: null, new: st, changed_at: iso(Date.now()), note: null }] })));
  }
  if ((mm = path.match(/^\/tracked\/([^/]+)(?:\/(notes|team|todos)(?:\/([^/]+))?)?$/))) {
    if (!needUser()) return;
    const uid = decodeURIComponent(mm[1]);
    const t = tracked.get(uid);
    if (!t) return err(res, 404, "not tracked");
    const sub = mm[2];
    const arg = mm[3] ? decodeURIComponent(mm[3]) : null;
    const b = m === "GET" || m === "DELETE" ? {} : await readBody(req);
    const touch = () => (t.updated_at = iso(Date.now()));
    if (!sub && m === "PATCH") {
      if (b.status && b.status !== t.status) {
        t.history.push({ old: t.status, new: b.status, changed_at: iso(Date.now()), note: b.status_note || null });
        t.status = b.status;
      }
      if ("repo_url" in b) t.repo_url = b.repo_url || null;
      touch();
      return send(res, 200, trackedOut(t));
    }
    if (!sub && m === "DELETE") {
      tracked.delete(uid);
      return send(res, 204);
    }
    if (sub === "notes" && m === "POST") {
      const line = `[${iso(Date.now()).slice(0, 16).replace("T", " ")} UTC] ${String(b.text).trim()}`;
      t.notes = t.notes ? `${t.notes}\n${line}` : line;
      touch();
      return send(res, 200, trackedOut(t));
    }
    if (sub === "team" && m === "POST") {
      if (!t.team.some((n) => n.toLowerCase() === String(b.name).toLowerCase())) t.team.push(String(b.name).trim());
      touch();
      return send(res, 200, trackedOut(t));
    }
    if (sub === "team" && m === "DELETE" && arg) {
      const i = t.team.findIndex((n) => n.toLowerCase() === arg.toLowerCase());
      if (i < 0) return err(res, 404, `${arg} isn't on the team`);
      t.team.splice(i, 1);
      touch();
      return send(res, 200, trackedOut(t));
    }
    if (sub === "todos" && m === "POST") {
      t.checklist.push({ id: ++todoSeq, text: String(b.text).trim(), done: false, due_at: b.due_at ?? null });
      touch();
      return send(res, 201, trackedOut(t));
    }
    if (sub === "todos" && arg) {
      const item = t.checklist.find((c) => c.id === Number(arg));
      if (!item) return err(res, 404, "unknown todo");
      if (m === "PATCH") item.done = !!b.done;
      else if (m === "DELETE") t.checklist = t.checklist.filter((c) => c !== item);
      touch();
      return send(res, 200, trackedOut(t));
    }
  }

  // searches & prefs
  if (path === "/searches" && m === "GET") {
    if (!needUser()) return;
    return send(res, 200, [...searches.entries()].map(([name, f]) => ({ name, filter: clean(f), description: describe(clean(f)), is_default: prefs.default_search === name })));
  }
  if ((mm = path.match(/^\/searches\/([^/]+)$/))) {
    if (!needUser()) return;
    const name = decodeURIComponent(mm[1]).trim().toLowerCase();
    if (m === "PUT") {
      const b = await readBody(req);
      const f = clean(b?.filter ?? {});
      if (!/^[a-z0-9][a-z0-9_-]*$/.test(name)) return err(res, 400, "names use letters, digits, - and _");
      if (!Object.keys(f).length) return err(res, 400, "a saved search needs at least one filter");
      searches.set(name, f);
      return send(res, 200, { name, filter: f, description: describe(f), is_default: prefs.default_search === name });
    }
    if (m === "DELETE") {
      if (!searches.delete(name)) return err(res, 404, "no such search");
      if (prefs.default_search === name) prefs.default_search = null;
      return send(res, 204);
    }
  }
  if (path === "/prefs" && m === "GET") {
    if (!needUser()) return;
    return send(res, 200, Object.fromEntries(Object.keys(PREF_DESC).map((k) => [k, { value: prefOut(k), description: PREF_DESC[k] }])));
  }
  if ((mm = path.match(/^\/prefs\/([^/]+)$/))) {
    if (!needUser()) return;
    const key = mm[1];
    if (!(key in PREF_DESC)) return err(res, 404, "unknown key");
    if (m === "PUT") {
      const b = await readBody(req);
      const v = String(b?.value ?? "").trim();
      if (key === "tz") {
        try {
          new Intl.DateTimeFormat("en", { timeZone: v });
        } catch {
          return err(res, 400, `unknown timezone '${v}'`);
        }
      }
      if (key === "default_search" && !searches.has(v)) return err(res, 400, `no saved search '${v}'`);
      let stored = v;
      try {
        stored = validatePref(key, v);
      } catch (e) {
        return err(res, 400, e.message);
      }
      prefs[key] = stored;
      return send(res, 200, { key, value: prefOut(key) });
    }
    if (m === "DELETE") {
      prefs[key] = null;
      return send(res, 204);
    }
  }
  // notifications & calendar feed
  if (path === "/calendar.ics" && m === "GET") {
    if (!feedToken || sp.get("token") !== feedToken) return err(res, 404, "Not Found");
    res.writeHead(200, { "Content-Type": "text/calendar; charset=utf-8", "Cache-Control": "private, max-age=300" });
    return res.end(icsFeed());
  }
  if (path === "/feed" && m === "GET") {
    if (!needUser()) return;
    return send(res, 200, feedOut(req));
  }
  if (path === "/feed/rotate" && m === "POST") {
    if (!needUser()) return;
    feedToken = null;
    return send(res, 200, feedOut(req));
  }
  if (path === "/notify" && m === "GET") {
    if (!needUser()) return;
    return send(res, 200, notifyStatus());
  }
  if (path === "/notify/test" && m === "POST") {
    if (!needUser()) return;
    const st = notifyStatus().channels;
    const names = Object.keys(st).filter((c) => st[c].configured);
    if (!names.length) return err(res, 400, "no notification channel configured: set notify_email, notify_telegram_chat or notify_discord_webhook first");
    await new Promise((r) => setTimeout(r, 700));
    const results = {};
    for (const c of names) results[c] = !SERVER[c] ? (c === "telegram" ? "server has no LOGR_TELEGRAM_BOT_TOKEN" : "server has no SMTP settings (LOGR_SMTP_HOST)") : "ok";
    return send(res, 200, { results });
  }
  return err(res, 404, "Not Found");
}

async function serveStatic(req, res, url) {
  let p = normalize(decodeURIComponent(url.pathname)).replace(/^(\.\.[/\\])+/, "");
  let file = join(ROOT, p);
  try {
    const s = await stat(file);
    if (s.isDirectory()) file = join(file, "index.html");
  } catch {
    // /mine → /mine/index.html (trailingSlash export)
    file = join(ROOT, p, "index.html");
  }
  try {
    const data = await readFile(file);
    res.writeHead(200, { "Content-Type": MIME[extname(file)] ?? "application/octet-stream" });
    res.end(data);
  } catch {
    try {
      const data = await readFile(join(ROOT, "404.html"));
      res.writeHead(404, { "Content-Type": MIME[".html"] });
      res.end(data);
    } catch {
      res.writeHead(404);
      res.end("Not found. Run `npm run build` first.");
    }
  }
}

createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);
  try {
    if (url.pathname.startsWith("/api/")) await api(req, res, url);
    else await serveStatic(req, res, url);
  } catch (e) {
    console.error(e);
    if (!res.headersSent) err(res, 500, String(e));
  }
}).listen(PORT, "127.0.0.1", () => {
  console.log(`Logr mock API (${AUTH}) serving ${ROOT} on http://127.0.0.1:${PORT}`);
});
