// Typed client for the Logr HTTP API (docs/API.md). Same origin: `/api/...`.

export type Source = "devpost" | "devfolio" | "unstop" | "hack2skill" | "luma" | "linkedin";
export type Mode = "online" | "offline" | "hybrid" | "unknown";
export type Status =
  | "interested"
  | "registered"
  | "team_formed"
  | "submitted"
  | "shortlisted"
  | "won"
  | "lost"
  | "withdrawn";
export type RoundKind = "registration" | "submission" | "judging" | "results" | "event" | "other";
export type EventKind = "deadline" | "round_end" | "start" | "end" | "todo";
export type AuthMode = "local" | "oauth";
export type Provider = "local" | "github" | "google";

export const SOURCES: Source[] = ["devpost", "devfolio", "unstop", "hack2skill", "luma"];
export const MODES: Mode[] = ["online", "offline", "hybrid"];
export const PIPELINE: Status[] = ["interested", "registered", "team_formed", "submitted", "shortlisted"];
export const TERMINAL: Status[] = ["won", "lost", "withdrawn"];
export const STATUSES: Status[] = [...PIPELINE, ...TERMINAL];

export interface User {
  id: string;
  provider: Provider;
  login: string;
  name: string;
  avatar_url: string | null;
}

export interface Me {
  user: User | null;
  auth_mode: AuthMode;
  providers: string[];
  is_admin: boolean;
}

export interface Deadline {
  label: string;
  at: string;
  approximate: boolean;
}

export interface AlsoListed {
  uid: string;
  source: Source;
  url: string;
}

export interface ListingOut {
  uid: string;
  title: string;
  url: string;
  organizer: string | null;
  mode: Mode;
  location: string | null;
  themes: string[];
  prize: string | null;
  prize_usd: number | null;
  participants: number | null;
  team_size_min: number | null;
  team_size_max: number | null;
  image_url: string | null;
  source: Source;
  sources: Source[];
  also_listed: AlsoListed[];
  registration_opens_at: string | null;
  registration_closes_at: string | null;
  submission_deadline: string | null;
  starts_at: string | null;
  ends_at: string | null;
  approx_fields: string[];
  next_deadline: Deadline | null;
  tracked_status: Status | null;
}

/** Filter.to_dict(): unset criteria are omitted. */
export interface FilterDict {
  sources?: Source[];
  modes?: Mode[];
  within_days?: number;
  search?: string;
  include_closed?: boolean;
  themes?: string[];
  region?: string;
  min_prize_usd?: number;
  team_size?: number;
}

export interface HackathonList {
  items: ListingOut[];
  total: number;
  filter: FilterDict;
  saved: string | null;
  now: string;
}

export interface Round {
  name: string;
  kind: RoundKind;
  starts_at: string | null;
  ends_at: string | null;
}

export interface DateChange {
  field: string;
  old_value: string | null;
  new_value: string | null;
  changed_at: string;
}

export interface ChecklistItem {
  id: number;
  text: string;
  done: boolean;
  due_at: string | null;
}

export interface StatusChange {
  old: Status | null;
  new: Status;
  changed_at: string;
  note: string | null;
}

export interface TrackedOut {
  uid: string;
  status: Status;
  added_at: string;
  updated_at: string;
  notes: string;
  repo_url: string | null;
  team: string[];
  checklist: ChecklistItem[];
  next_action: Deadline | null;
  history: StatusChange[];
  hackathon: ListingOut | null;
}

export interface HackathonDetail extends ListingOut {
  rounds: Round[];
  date_changes: DateChange[];
  first_seen_at: string | null;
  last_seen_at: string | null;
  delisted_at: string | null;
  tracking: TrackedOut | null;
}

export interface CalendarEvent {
  uid: string;
  title: string;
  label: string;
  at: string;
  approximate: boolean;
  kind: EventKind;
  tracked_status: Status | null;
}

export interface SyncRun {
  source: string;
  started_at: string | null;
  finished_at: string | null;
  fetched: number | null;
  created: number | null;
  updated: number | null;
  enriched: number | null;
  enrich_errors: number | null;
  delisted: number | null;
  error: string | null;
}

export interface SyncStatus {
  runs: SyncRun[];
  running: boolean;
}

export interface SavedSearch {
  name: string;
  filter: FilterDict;
  description: string;
  is_default: boolean;
}

export type PrefKey = "tz" | "luma_near" | "default_search";
export type Prefs = Record<string, { value: string | null; description: string }>;

// --- transport ------------------------------------------------------------

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(detail || `HTTP ${status}`);
    this.name = "ApiError";
  }
  get isAuth(): boolean {
    return this.status === 401;
  }
}

export type QueryValue = string | number | boolean | null | undefined | Array<string | number>;
export type Query = Record<string, QueryValue>;

export function buildQuery(q?: Query): string {
  if (!q) return "";
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, String(x)));
    else sp.append(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

function detailOf(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    // FastAPI 422: [{loc, msg, type}, ...]
    if (Array.isArray(d))
      return d
        .map((e) => (e && typeof e === "object" && "msg" in e ? String((e as { msg: unknown }).msg) : String(e)))
        .join("; ");
  }
  return `Request failed (HTTP ${status})`;
}

export async function request<T>(method: string, path: string, opts: { query?: Query; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET" && method !== "HEAD") headers["X-Requested-With"] = "logr";
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(`/api${path}${buildQuery(opts.query)}`, {
      method,
      headers,
      credentials: "same-origin",
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Can't reach the Logr server. Is `logr serve` running?");
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!res.ok) throw new ApiError(res.status, detailOf(body, res.status));
  return body as T;
}

const enc = encodeURIComponent;

export const api = {
  me: () => request<Me>("GET", "/me"),
  logout: () => request<void>("POST", "/auth/logout"),
  loginUrl: (provider: string, next: string) => `/api/auth/${enc(provider)}/login?next=${enc(next)}`,

  hackathons: (q: Query) => request<HackathonList>("GET", "/hackathons", { query: q }),
  hackathon: (uid: string) => request<HackathonDetail>("GET", `/hackathons/${enc(uid)}`),
  calendar: (from: string, to: string, scope?: "tracked" | "all") =>
    request<{ events: CalendarEvent[] }>("GET", "/calendar", { query: { from, to, scope } }),
  syncStatus: () => request<SyncStatus>("GET", "/sync/status"),
  startSync: () => request<{ started: boolean }>("POST", "/sync"),

  tracked: (status?: Status[]) => request<TrackedOut[]>("GET", "/tracked", { query: { status } }),
  track: (uid: string, status?: Status) => request<TrackedOut>("POST", "/tracked", { body: status ? { uid, status } : { uid } }),
  updateTracked: (uid: string, body: { status?: Status; status_note?: string; repo_url?: string | null }) =>
    request<TrackedOut>("PATCH", `/tracked/${enc(uid)}`, { body }),
  untrack: (uid: string) => request<void>("DELETE", `/tracked/${enc(uid)}`),
  addNote: (uid: string, text: string) => request<TrackedOut>("POST", `/tracked/${enc(uid)}/notes`, { body: { text } }),
  addTeammate: (uid: string, name: string) => request<TrackedOut>("POST", `/tracked/${enc(uid)}/team`, { body: { name } }),
  removeTeammate: (uid: string, name: string) => request<TrackedOut>("DELETE", `/tracked/${enc(uid)}/team/${enc(name)}`),
  addTodo: (uid: string, text: string, due_at?: string | null) =>
    request<TrackedOut>("POST", `/tracked/${enc(uid)}/todos`, { body: due_at ? { text, due_at } : { text } }),
  setTodoDone: (uid: string, id: number, done: boolean) =>
    request<TrackedOut>("PATCH", `/tracked/${enc(uid)}/todos/${id}`, { body: { done } }),
  deleteTodo: (uid: string, id: number) => request<TrackedOut>("DELETE", `/tracked/${enc(uid)}/todos/${id}`),

  searches: () => request<SavedSearch[]>("GET", "/searches"),
  saveSearch: (name: string, filter: FilterDict) => request<SavedSearch>("PUT", `/searches/${enc(name)}`, { body: { filter } }),
  deleteSearch: (name: string) => request<void>("DELETE", `/searches/${enc(name)}`),

  prefs: () => request<Prefs>("GET", "/prefs"),
  setPref: (key: PrefKey, value: string) => request<unknown>("PUT", `/prefs/${key}`, { body: { value } }),
  unsetPref: (key: PrefKey) => request<void>("DELETE", `/prefs/${key}`),
};
