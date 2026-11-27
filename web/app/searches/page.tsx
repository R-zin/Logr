"use client";

import Link from "next/link";
import { Suspense, useCallback, useEffect, useState } from "react";
import { IconTrash } from "@/components/icons";
import { EmptyState, ErrorState, InlineError, PageHeader, RequireUser, Skeleton, cx, useDocTitle } from "@/components/ui";
import { api, ApiError, type SavedSearch } from "@/lib/api";
import { useSession } from "@/lib/session";

function Searches() {
  const { refresh } = useSession();
  const [items, setItems] = useState<SavedSearch[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setItems(await api.searches());
    } catch (e) {
      setError(e);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const act = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name);
    setActionError(null);
    try {
      await fn();
      await load();
      void refresh(); // default_search lives in prefs
    } catch (e) {
      setActionError(e);
    } finally {
      setBusy(null);
    }
  };

  if (error instanceof ApiError && error.status === 401) return <RequireUser what="save searches" unauthorized>{null}</RequireUser>;

  return (
    <>
      <PageHeader
        title="Saved searches"
        subtitle="Reusable Discover filters. The default one is applied whenever you open Discover without filters."
        actions={
          <Link href="/" className="btn-secondary">
            New search
          </Link>
        }
      />
      <InlineError error={actionError} />
      {error ? (
        <ErrorState error={error} onRetry={load} />
      ) : items === null ? (
        <div className="card divide-y divide-line">
          {Array.from({ length: 3 }, (_, i) => (
            <div key={i} className="space-y-2 px-5 py-4">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-3 w-64" />
            </div>
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          title="No saved searches yet"
          action={
            <Link href="/" className="btn-primary">
              Go to Discover
            </Link>
          }
        >
          Set some filters on Discover and choose “Save this search”.
        </EmptyState>
      ) : (
        <ul className="card divide-y divide-line overflow-hidden">
          {items.map((s) => (
            <li key={s.name} className={cx("flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center", busy === s.name && "opacity-60")}>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <Link href={`/?saved=${encodeURIComponent(s.name)}`} className="font-medium hover:underline">
                    {s.name}
                  </Link>
                  {s.is_default && <span className="chip bg-accent-soft text-accent">Default</span>}
                </div>
                <p className="mt-0.5 truncate text-sm text-muted">{s.description}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Link href={`/?saved=${encodeURIComponent(s.name)}`} className="btn-secondary btn-sm">
                  Open
                </Link>
                {s.is_default ? (
                  <button
                    type="button"
                    className="btn-ghost btn-sm"
                    disabled={!!busy}
                    onClick={() => act(s.name, () => api.unsetPref("default_search"))}
                  >
                    Unset default
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn-ghost btn-sm"
                    disabled={!!busy}
                    onClick={() => act(s.name, () => api.setPref("default_search", s.name))}
                  >
                    Set as default
                  </button>
                )}
                <button
                  type="button"
                  className="btn-ghost btn-sm hover:text-warm"
                  disabled={!!busy}
                  onClick={() => {
                    if (window.confirm(`Delete the saved search “${s.name}”?`)) void act(s.name, () => api.deleteSearch(s.name));
                  }}
                >
                  <IconTrash width={14} height={14} />
                  <span className="sr-only">Delete {s.name}</span>
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

export default function Page() {
  useDocTitle("Saved searches");
  return (
    <Suspense fallback={null}>
      <RequireUser what="save searches">
        <Searches />
      </RequireUser>
    </Suspense>
  );
}
