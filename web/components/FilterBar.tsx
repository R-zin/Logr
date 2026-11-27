"use client";

import { useEffect, useId, useRef, useState } from "react";
import { MODES, SOURCES, type Mode, type SavedSearch, type Source } from "@/lib/api";
import { MODE_LABEL, SOURCE_LABEL } from "@/lib/labels";
import { IconChevronDown, IconFilter, IconSearch, IconX } from "./icons";
import { cx } from "./ui";

/** Discover's filters live in the URL; keys match GET /api/hackathons params. */
export const FILTER_KEYS = [
  "q",
  "source",
  "mode",
  "theme",
  "region",
  "min_prize",
  "team_size",
  "within",
  "include_closed",
] as const;

export type Patch = Record<string, string | string[] | null>;

const WITHIN_OPTIONS = [
  { v: "", l: "Any time" },
  { v: "1", l: "24 hours" },
  { v: "3", l: "3 days" },
  { v: "7", l: "7 days" },
  { v: "14", l: "14 days" },
  { v: "30", l: "30 days" },
  { v: "60", l: "60 days" },
];

function useDebounced(value: string, onCommit: (v: string) => void, ms = 350) {
  const first = useRef(true);
  const cb = useRef(onCommit);
  cb.current = onCommit;
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const t = setTimeout(() => cb.current(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
}

/** A text input whose value is committed to the URL after typing pauses. */
function DebouncedInput({
  value,
  onCommit,
  ...rest
}: { value: string; onCommit: (v: string) => void } & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) {
  const [local, setLocal] = useState(value);
  const lastExternal = useRef(value);
  useEffect(() => {
    if (value !== lastExternal.current) {
      lastExternal.current = value;
      setLocal(value);
    }
  }, [value]);
  useDebounced(local, (v) => {
    if (v !== lastExternal.current) {
      lastExternal.current = v;
      onCommit(v);
    }
  });
  return <input {...rest} value={local} onChange={(e) => setLocal(e.target.value)} />;
}

function ToggleChip({ checked, onChange, children }: { checked: boolean; onChange: (c: boolean) => void; children: React.ReactNode }) {
  return (
    <label
      className={cx(
        "relative inline-flex cursor-pointer select-none items-center rounded-full border px-3 py-1 text-xs font-medium transition-colors",
        "has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-accent",
        checked ? "border-accent/40 bg-accent-soft text-accent" : "border-line bg-surface text-muted hover:text-ink",
      )}
    >
      <input type="checkbox" className="sr-only" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {children}
    </label>
  );
}

export function FilterBar({
  params,
  onChange,
  savedSearches,
  signedIn,
}: {
  params: URLSearchParams;
  onChange: (patch: Patch) => void;
  savedSearches: SavedSearch[] | null;
  signedIn: boolean;
}) {
  const id = useId();
  const sources = params.getAll("source") as Source[];
  const modes = params.getAll("mode") as Mode[];
  const advancedCount = ["theme", "region", "min_prize", "team_size", "within", "include_closed"].filter((k) => params.get(k))
    .length;
  const [open, setOpen] = useState(advancedCount > 0);

  const toggle = (key: "source" | "mode", list: string[], v: string, on: boolean) =>
    onChange({ [key]: on ? [...list, v] : list.filter((x) => x !== v) });

  const anyFilter = FILTER_KEYS.some((k) => params.get(k)) || params.get("saved");

  return (
    <section aria-label="Filters" className="card p-3 sm:p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <label htmlFor={`${id}-q`} className="sr-only">
            Search hackathons
          </label>
          <IconSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
          <DebouncedInput
            id={`${id}-q`}
            type="search"
            className="field pl-9"
            placeholder="Search title, organizer, location or theme"
            value={params.get("q") ?? ""}
            onCommit={(v) => onChange({ q: v || null })}
          />
        </div>
        <div className="flex items-center gap-2">
          {signedIn && savedSearches && savedSearches.length > 0 && (
            <div className="min-w-0 flex-1 sm:w-44 sm:flex-none">
              <label htmlFor={`${id}-saved`} className="sr-only">
                Saved search
              </label>
              <select
                id={`${id}-saved`}
                className="field"
                value={params.get("saved") ?? ""}
                onChange={(e) => {
                  const v = e.target.value;
                  // A saved search replaces the current filters (other params would override it).
                  const clear: Patch = Object.fromEntries(FILTER_KEYS.map((k) => [k, null]));
                  onChange({ ...clear, saved: v || null, no_prefs: v ? null : "1" });
                }}
              >
                <option value="">Saved search…</option>
                {savedSearches.map((s) => (
                  <option key={s.name} value={s.name}>
                    {s.name}
                    {s.is_default ? " (default)" : ""}
                  </option>
                ))}
              </select>
            </div>
          )}
          <button
            type="button"
            className="btn-secondary shrink-0"
            aria-expanded={open}
            aria-controls={`${id}-more`}
            onClick={() => setOpen((o) => !o)}
          >
            <IconFilter />
            More filters
            {advancedCount > 0 && (
              <span className="rounded-full bg-accent px-1.5 text-[10px] font-semibold leading-4 text-accent-ink">{advancedCount}</span>
            )}
            <IconChevronDown className={cx("transition-transform", open && "rotate-180")} />
          </button>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        <fieldset className="flex flex-wrap items-center gap-1.5">
          <legend className="sr-only">Source</legend>
          {SOURCES.map((s) => (
            <ToggleChip key={s} checked={sources.includes(s)} onChange={(on) => toggle("source", sources, s, on)}>
              {SOURCE_LABEL[s]}
            </ToggleChip>
          ))}
        </fieldset>
        <span aria-hidden className="hidden h-4 w-px bg-line sm:block" />
        <fieldset className="flex flex-wrap items-center gap-1.5">
          <legend className="sr-only">Mode</legend>
          {MODES.map((m) => (
            <ToggleChip key={m} checked={modes.includes(m)} onChange={(on) => toggle("mode", modes, m, on)}>
              {MODE_LABEL[m]}
            </ToggleChip>
          ))}
        </fieldset>
        {anyFilter && (
          <button
            type="button"
            className="btn-ghost btn-sm ml-auto"
            onClick={() => onChange({ ...Object.fromEntries(FILTER_KEYS.map((k) => [k, null])), saved: null, no_prefs: "1" })}
          >
            <IconX width={14} height={14} />
            Clear all
          </button>
        )}
      </div>

      <div id={`${id}-more`} hidden={!open} className="mt-4 border-t border-line pt-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <div className="col-span-2 sm:col-span-1">
            <label className="label" htmlFor={`${id}-theme`}>
              Themes
            </label>
            <DebouncedInput
              id={`${id}-theme`}
              className="field"
              placeholder="AI, Web3"
              value={params.getAll("theme").join(", ")}
              onCommit={(v) =>
                onChange({
                  theme: v
                    .split(",")
                    .map((t) => t.trim())
                    .filter(Boolean),
                })
              }
            />
          </div>
          <div className="col-span-2 sm:col-span-1">
            <label className="label" htmlFor={`${id}-region`}>
              Region
            </label>
            <DebouncedInput
              id={`${id}-region`}
              className="field"
              placeholder="Bengaluru"
              value={params.get("region") ?? ""}
              onCommit={(v) => onChange({ region: v.trim() || null })}
            />
          </div>
          <div>
            <label className="label" htmlFor={`${id}-prize`}>
              Min prize (USD)
            </label>
            <DebouncedInput
              id={`${id}-prize`}
              className="field"
              type="number"
              inputMode="numeric"
              min={0}
              step={100}
              placeholder="0"
              value={params.get("min_prize") ?? ""}
              onCommit={(v) => onChange({ min_prize: v || null })}
            />
          </div>
          <div>
            <label className="label" htmlFor={`${id}-team`}>
              Team size
            </label>
            <DebouncedInput
              id={`${id}-team`}
              className="field"
              type="number"
              inputMode="numeric"
              min={1}
              max={20}
              placeholder="Any"
              value={params.get("team_size") ?? ""}
              onCommit={(v) => onChange({ team_size: v || null })}
            />
          </div>
          <div>
            <label className="label" htmlFor={`${id}-within`}>
              Closing within
            </label>
            <select
              id={`${id}-within`}
              className="field"
              value={params.get("within") ?? ""}
              onChange={(e) => onChange({ within: e.target.value || null })}
            >
              {WITHIN_OPTIONS.some((o) => o.v === (params.get("within") ?? "")) ? null : (
                <option value={params.get("within") ?? ""}>{params.get("within")} days</option>
              )}
              {WITHIN_OPTIONS.map((o) => (
                <option key={o.v} value={o.v}>
                  {o.l}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-end">
            <label className="flex h-[38px] cursor-pointer items-center gap-2 text-sm text-muted">
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-line accent-[rgb(var(--accent))]"
                checked={params.get("include_closed") === "true"}
                onChange={(e) => onChange({ include_closed: e.target.checked ? "true" : null })}
              />
              Include closed
            </label>
          </div>
        </div>
      </div>
    </section>
  );
}
