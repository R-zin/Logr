"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { FilterBar, type Patch } from "@/components/FilterBar";
import { HackathonRow, HackathonRowSkeleton } from "@/components/HackathonRow";
import { IconCheck } from "@/components/icons";
import { Approx, EmptyState, ErrorState, InlineError, PageHeader, cx, useDocTitle } from "@/components/ui";
import { api, ApiError, type HackathonList, type ListingOut, type SavedSearch } from "@/lib/api";
import { useNow, useSession } from "@/lib/session";

const PAGE = 25;
const PASSTHROUGH = ["q", "source", "mode", "theme", "region", "min_prize", "team_size", "within", "include_closed", "saved", "sort", "no_prefs"];

function apiQuery(sp: URLSearchParams, offset: number): Record<string, string | string[]> {
  const q: Record<string, string | string[]> = {};
  for (const k of PASSTHROUGH) {
    const all = sp.getAll(k).filter(Boolean);
    if (all.length) q[k] = all.length > 1 || ["source", "mode", "theme"].includes(k) ? all : all[0];
  }
  q.limit = String(PAGE);
  q.offset = String(offset);
  return q;
}

function SaveSearchForm({ data, onSaved }: { data: HackathonList; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(data.saved ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [done, setDone] = useState<string | null>(null);
  const empty = Object.keys(data.filter).length === 0;

  if (!open)
    return (
      <div className="flex items-center gap-2">
        {done && (
          <span className="inline-flex items-center gap-1 text-xs text-good" role="status">
            <IconCheck width={14} height={14} /> Saved as “{done}”
          </span>
        )}
        <button
          type="button"
          className="btn-ghost btn-sm"
          disabled={empty}
          title={empty ? "Set at least one filter to save a search" : undefined}
          onClick={() => {
            setOpen(true);
            setDone(null);
          }}
        >
          Save this search
        </button>
      </div>
    );

  return (
    <form
      className="flex flex-wrap items-start gap-2"
      onSubmit={async (e) => {
        e.preventDefault();
        const n = name.trim().toLowerCase();
        if (!n) return;
        setBusy(true);
        setErr(null);
        try {
          await api.saveSearch(n, data.filter);
          setDone(n);
          setOpen(false);
          onSaved();
        } catch (e2) {
          setErr(e2);
        } finally {
          setBusy(false);
        }
      }}
    >
      <div>
        <label htmlFor="save-name" className="sr-only">
          Name for this search
        </label>
        <input
          id="save-name"
          autoFocus
          className="field w-44 py-1.5"
          placeholder="e.g. blr-ai"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <InlineError error={err} />
      </div>
      <button type="submit" className="btn-primary btn-sm mt-0.5" disabled={busy || !name.trim()}>
        {busy ? "Saving…" : "Save"}
      </button>
      <button type="button" className="btn-ghost btn-sm mt-0.5" onClick={() => setOpen(false)}>
        Cancel
      </button>
    </form>
  );
}

function Discover() {
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const { tz, signedIn, loading: sessionLoading } = useSession();
  const now = useNow();
  const key = sp.toString();

  const [data, setData] = useState<HackathonList | null>(null);
  const [items, setItems] = useState<ListingOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [more, setMore] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [moreError, setMoreError] = useState<unknown>(null);
  const [searches, setSearches] = useState<SavedSearch[] | null>(null);
  const reqId = useRef(0);

  const load = useCallback(async () => {
    const id = ++reqId.current;
    setLoading(true);
    setError(null);
    try {
      const res = await api.hackathons(apiQuery(new URLSearchParams(key), 0));
      if (id !== reqId.current) return;
      setData(res);
      setItems(res.items);
    } catch (e) {
      if (id === reqId.current) setError(e);
    } finally {
      if (id === reqId.current) setLoading(false);
    }
  }, [key]);

  // Wait for /api/me so a signed-in user's default search and tracked stars apply.
  useEffect(() => {
    if (!sessionLoading) void load();
  }, [load, sessionLoading]);

  const loadSearches = useCallback(() => {
    api.searches().then(setSearches, () => setSearches(null));
  }, []);
  useEffect(() => {
    if (signedIn) loadSearches();
  }, [signedIn, loadSearches]);

  const update = useCallback(
    (patch: Patch) => {
      const next = new URLSearchParams(key);
      for (const [k, v] of Object.entries(patch)) {
        next.delete(k);
        if (Array.isArray(v)) v.forEach((x) => next.append(k, x));
        else if (v) next.set(k, v);
      }
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [key, pathname, router],
  );

  const loadMore = async () => {
    if (!data) return;
    setMore(true);
    setMoreError(null);
    try {
      const res = await api.hackathons(apiQuery(new URLSearchParams(key), items.length));
      setItems((prev) => {
        const seen = new Set(prev.map((h) => h.uid));
        return [...prev, ...res.items.filter((h) => !seen.has(h.uid))];
      });
      setData((d) => (d ? { ...d, total: res.total } : res));
    } catch (e) {
      setMoreError(e);
    } finally {
      setMore(false);
    }
  };

  const onTracked = (uid: string, s: ListingOut["tracked_status"]) =>
    setItems((prev) => prev.map((h) => (h.uid === uid ? { ...h, tracked_status: s } : h)));

  const sort = sp.get("sort") ?? "deadline";
  const defaultApplied = data?.saved && !sp.get("saved");
  const activeSaved = searches?.find((s) => s.name === data?.saved);

  return (
    <>
      <PageHeader
        title="Discover"
        subtitle="Open hackathons across Devpost, Devfolio, Unstop, Hack2skill and Luma, soonest deadline first."
      />
      <FilterBar params={sp as unknown as URLSearchParams} onChange={update} savedSearches={searches} signedIn={signedIn} />

      {defaultApplied && (
        <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg bg-accent-soft px-4 py-2.5 text-sm">
          <span>
            Showing your default search <strong className="font-semibold">{data.saved}</strong>
            {activeSaved && <span className="text-muted"> · {activeSaved.description}</span>}
          </span>
          <button type="button" className="link text-sm" onClick={() => update({ no_prefs: "1" })}>
            Show everything
          </button>
        </div>
      )}

      <div className="mb-3 mt-6 flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted" aria-live="polite">
          {loading && !data ? (
            "Loading…"
          ) : data ? (
            <>
              <span className="font-semibold text-ink tabular-nums">{data.total.toLocaleString()}</span>{" "}
              {data.total === 1 ? "hackathon" : "hackathons"}
              {data.saved && sp.get("saved") && (
                <>
                  {" "}
                  in <span className="font-medium text-ink">{data.saved}</span>
                </>
              )}
            </>
          ) : null}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {signedIn && data && <SaveSearchForm key={key} data={data} onSaved={loadSearches} />}
          <div role="group" aria-label="Sort by" className="inline-flex rounded-lg border border-line bg-surface p-0.5 shadow-sm">
            {(["deadline", "prize"] as const).map((s) => (
              <button
                key={s}
                type="button"
                aria-pressed={sort === s}
                onClick={() => update({ sort: s === "deadline" ? null : s })}
                className={cx(
                  "rounded-md px-2.5 py-1 text-xs font-medium",
                  sort === s ? "bg-sunken text-ink" : "text-muted hover:text-ink",
                )}
              >
                {s === "deadline" ? "Deadline" : "Prize"}
              </button>
            ))}
          </div>
        </div>
      </div>

      {error ? (
        <ErrorState error={error} onRetry={load} />
      ) : loading && !items.length ? (
        <ul className="card divide-y divide-line overflow-hidden" aria-busy>
          {Array.from({ length: 6 }, (_, i) => (
            <HackathonRowSkeleton key={i} />
          ))}
        </ul>
      ) : items.length === 0 ? (
        <EmptyState
          title="No hackathons match these filters"
          action={
            <button type="button" className="btn-secondary" onClick={() => update({ q: null, source: null, mode: null, theme: null, region: null, min_prize: null, team_size: null, within: null, include_closed: null, saved: null, no_prefs: "1" })}>
              Clear filters
            </button>
          }
        >
          Try widening the deadline window, removing a source, or including closed hackathons.
        </EmptyState>
      ) : (
        <>
          <ul className={cx("card divide-y divide-line overflow-hidden transition-opacity", loading && "opacity-60")} aria-busy={loading}>
            {items.map((h) => (
              <HackathonRow key={h.uid} h={h} tz={tz} now={now} onTracked={onTracked} />
            ))}
            {more && <HackathonRowSkeleton />}
          </ul>
          <div className="mt-6 flex flex-col items-center gap-2">
            {data && items.length < data.total && (
              <button type="button" className="btn-secondary" onClick={loadMore} disabled={more}>
                {more ? "Loading…" : `Load more (${(data.total - items.length).toLocaleString()} left)`}
              </button>
            )}
            <InlineError error={moreError instanceof ApiError ? moreError : null} />
            <p className="text-xs text-faint">
              <Approx /> marks an approximate time: the source doesn&apos;t publish it exactly. Times shown in {tz}.
            </p>
          </div>
        </>
      )}
    </>
  );
}

export default function Page() {
  useDocTitle("Discover");
  return (
    <Suspense fallback={null}>
      <Discover />
    </Suspense>
  );
}
