"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { IconRefresh } from "@/components/icons";
import { CalendarFeed } from "@/components/CalendarFeed";
import { NotifySettings } from "@/components/NotifySettings";
import { ErrorState, InlineError, PageHeader, PageSection, SavedNote, SignInButtons, Skeleton, cx, useDocTitle } from "@/components/ui";
import { api, ApiError, type PrefKey, type SavedSearch, type SyncStatus } from "@/lib/api";
import { sourceLabel } from "@/lib/labels";
import { useNow, useSession } from "@/lib/session";
import { browserTz, formatShort, listTimeZones, parse, relative } from "@/lib/time";

function useSaver() {
  const { setPrefs } = useSession();
  const [saving, setSaving] = useState<PrefKey | null>(null);
  const [saved, setSaved] = useState<PrefKey | null>(null);
  const [error, setError] = useState<{ key: PrefKey; err: unknown } | null>(null);
  const save = async (key: PrefKey, value: string | null) => {
    setSaving(key);
    setError(null);
    setSaved(null);
    try {
      if (value) await api.setPref(key, value);
      else await api.unsetPref(key);
      setPrefs(await api.prefs());
      setSaved(key);
      setTimeout(() => setSaved((k) => (k === key ? null : k)), 2000);
    } catch (e) {
      setError({ key, err: e });
    } finally {
      setSaving(null);
    }
  };
  return { save, saving, saved, error };
}

const Saved = SavedNote;

function Row({ label, htmlFor, help, children }: { label: string; htmlFor: string; help?: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-2 px-5 py-5 sm:grid-cols-[14rem_minmax(0,1fr)] sm:gap-6">
      <div>
        <label htmlFor={htmlFor} className="text-sm font-medium">
          {label}
        </label>
        {help && <p className="mt-0.5 text-xs text-muted">{help}</p>}
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function Preferences() {
  const { prefs, me, loading } = useSession();
  const { save, saving, saved, error } = useSaver();
  const zones = useMemo(() => listTimeZones(), []);
  const [luma, setLuma] = useState("");
  const [searches, setSearches] = useState<SavedSearch[] | null>(null);
  const lumaInit = useRef(false);

  useEffect(() => {
    if (prefs && !lumaInit.current) {
      lumaInit.current = true;
      setLuma(prefs.luma_near?.value ?? "");
    }
  }, [prefs]);
  useEffect(() => {
    if (me?.user) api.searches().then(setSearches, () => setSearches([]));
  }, [me?.user]);

  if (loading)
    return (
      <div className="card space-y-4 p-5">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-9 w-full" />
        <Skeleton className="h-9 w-full" />
      </div>
    );
  if (!me?.user)
    return (
      <div className="card px-5 py-6">
        <p className="text-sm text-muted">Sign in to set your timezone, default search, notifications and calendar feed.</p>
        <div className="mt-3">
          <SignInButtons />
        </div>
      </div>
    );
  if (!prefs) return <ErrorState error={new ApiError(0, "Couldn't load preferences.")} />;

  const tz = prefs.tz?.value ?? "";
  const def = prefs.default_search?.value ?? "";
  const errFor = (k: PrefKey) => (error?.key === k ? error.err : null);

  return (
    <div className="card divide-y divide-line">
      <Row label="Timezone" htmlFor="pref-tz" help="Every date in Logr is shown in this zone.">
        <div className="flex items-center gap-3">
          <select
            id="pref-tz"
            className="field max-w-sm"
            value={tz}
            disabled={saving === "tz"}
            onChange={(e) => save("tz", e.target.value || null)}
          >
            <option value="">Browser default ({browserTz()})</option>
            {zones.map((z) => (
              <option key={z} value={z}>
                {z.replace(/_/g, " ")}
              </option>
            ))}
          </select>
          <Saved show={saved === "tz"} />
        </div>
        <InlineError error={errFor("tz")} />
      </Row>
      <Row
        label="Luma places"
        htmlFor="pref-luma"
        help="Cities (slug) or lat,lng to sync Luma events from, separated by “;”."
      >
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void save("luma_near", luma.trim() || null);
          }}
        >
          <input
            id="pref-luma"
            className="field max-w-sm flex-1"
            placeholder="bengaluru; 12.97,77.59"
            value={luma}
            onChange={(e) => setLuma(e.target.value)}
          />
          <button type="submit" className="btn-secondary" disabled={saving === "luma_near" || luma.trim() === (prefs.luma_near?.value ?? "")}>
            Save
          </button>
          <Saved show={saved === "luma_near"} />
        </form>
        <InlineError error={errFor("luma_near")} />
      </Row>
      <Row label="Default search" htmlFor="pref-default" help="Applied to Discover when you haven't set any filters.">
        <div className="flex items-center gap-3">
          <select
            id="pref-default"
            className="field max-w-sm"
            value={def}
            disabled={saving === "default_search" || searches === null}
            onChange={(e) => save("default_search", e.target.value || null)}
          >
            <option value="">None</option>
            {(searches ?? []).map((s) => (
              <option key={s.name} value={s.name}>
                {s.name} · {s.description}
              </option>
            ))}
          </select>
          <Saved show={saved === "default_search"} />
        </div>
        {searches?.length === 0 && <p className="mt-1 text-xs text-faint">Save a search on Discover first.</p>}
        <InlineError error={errFor("default_search")} />
      </Row>
    </div>
  );
}

function SyncPanel() {
  const { me, tz } = useSession();
  const now = useNow(15_000);
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [startError, setStartError] = useState<unknown>(null);
  const [starting, setStarting] = useState(false);

  const load = useCallback(async () => {
    try {
      setStatus(await api.syncStatus());
      setError(null);
    } catch (e) {
      setError(e);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (!status?.running) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [status?.running, load]);

  const start = async () => {
    setStarting(true);
    setStartError(null);
    try {
      await api.startSync();
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 409)) setStartError(e); // 409: already running
    } finally {
      setStarting(false);
      await load();
    }
  };

  const num = (n: number | null) => (n == null ? "—" : n.toLocaleString());

  return (
    <section aria-labelledby="sync-h" className="mt-10">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 id="sync-h" className="text-lg font-semibold tracking-tight">
            Sync
          </h2>
          <p className="text-sm text-muted">The latest run for each source.</p>
        </div>
        <div className="flex items-center gap-3">
          {status?.running && (
            <span className="inline-flex items-center gap-2 text-sm text-accent" role="status">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-60" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-accent" />
              </span>
              Syncing…
            </span>
          )}
          {me?.is_admin && (
            <button type="button" className="btn-primary" disabled={starting || !!status?.running} onClick={start}>
              <IconRefresh width={15} height={15} className={cx(status?.running && "animate-spin")} />
              Sync now
            </button>
          )}
        </div>
      </div>
      <InlineError error={startError} />
      {error ? (
        <ErrorState error={error} onRetry={load} />
      ) : !status ? (
        <Skeleton className="h-48 w-full rounded-xl" />
      ) : status.runs.length === 0 ? (
        <div className="card px-5 py-8 text-center text-sm text-muted">No sync has run yet.</div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[40rem] text-left text-sm">
            <thead className="border-b border-line bg-sunken/60 text-xs text-faint">
              <tr>
                <th scope="col" className="px-5 py-2.5 font-medium">
                  Source
                </th>
                <th scope="col" className="px-3 py-2.5 font-medium">
                  Finished
                </th>
                <th scope="col" className="px-3 py-2.5 text-right font-medium">
                  Fetched
                </th>
                <th scope="col" className="px-3 py-2.5 text-right font-medium">
                  New
                </th>
                <th scope="col" className="px-3 py-2.5 text-right font-medium">
                  Updated
                </th>
                <th scope="col" className="px-3 py-2.5 text-right font-medium">
                  Details
                </th>
                <th scope="col" className="px-3 py-2.5 text-right font-medium">
                  Delisted
                </th>
                <th scope="col" className="px-5 py-2.5 font-medium">
                  Result
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {status.runs.map((r) => {
                const when = r.finished_at ?? r.started_at;
                const stale = parse(when) && now.getTime() - parse(when)!.getTime() > 36 * 3600_000;
                return (
                  <tr key={r.source}>
                    <th scope="row" className="px-5 py-3 font-medium">
                      {sourceLabel(r.source)}
                    </th>
                    <td className="px-3 py-3 tabular-nums">
                      {when ? (
                        <span title={formatShort(when, tz, now)} className={cx(stale ? "text-warm" : "text-muted")}>
                          {r.finished_at ? relative(r.finished_at, now) : "running"}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums">{num(r.fetched)}</td>
                    <td className="px-3 py-3 text-right tabular-nums">{num(r.created)}</td>
                    <td className="px-3 py-3 text-right tabular-nums">{num(r.updated)}</td>
                    <td className="px-3 py-3 text-right tabular-nums">
                      {num(r.enriched)}
                      {!!r.enrich_errors && <span className="ml-1 text-xs text-warm">({r.enrich_errors} failed)</span>}
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums">{num(r.delisted)}</td>
                    <td className="max-w-[16rem] px-5 py-3">
                      {r.error ? (
                        <span className="block truncate text-warm" title={r.error}>
                          {r.error}
                        </span>
                      ) : r.finished_at ? (
                        <span className="text-good">OK</span>
                      ) : (
                        <span className="text-accent">In progress</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Settings() {
  const { me } = useSession();
  return (
    <>
      <PageHeader
        title="Settings"
        subtitle={me?.auth_mode === "local" ? "Single-user mode: these preferences are shared with the logr CLI." : "Your preferences, notifications, calendar feed and the state of the data sync."}
      />
      <PageSection id="prefs-h" title="Preferences">
        <Preferences />
      </PageSection>
      {me?.user && (
        <>
          <NotifySettings className="mt-10" />
          <CalendarFeed className="mt-10" />
        </>
      )}
      <SyncPanel />
    </>
  );
}

export default function Page() {
  useDocTitle("Settings");
  return (
    <Suspense fallback={null}>
      <Settings />
    </Suspense>
  );
}
