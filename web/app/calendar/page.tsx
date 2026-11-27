"use client";

import Link from "next/link";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { IconChevronLeft, IconChevronRight, IconStar } from "@/components/icons";
import { Approx, EmptyState, ErrorState, PageHeader, Skeleton, cx, useDocTitle } from "@/components/ui";
import { api, type CalendarEvent, type EventKind } from "@/lib/api";
import { EVENT_KIND_LABEL } from "@/lib/labels";
import { useNow, useSession } from "@/lib/session";
import { dayKey, dayParts, formatTime, monthLabel, weekdayLabels, ymd, zonedToUtcIso } from "@/lib/time";

const KIND_STYLE: Record<EventKind, { pill: string; dot: string }> = {
  deadline: { pill: "bg-warm-soft text-warm", dot: "bg-warm" },
  round_end: { pill: "bg-accent-soft text-accent", dot: "bg-accent" },
  start: { pill: "bg-good-soft text-good", dot: "bg-good" },
  end: { pill: "bg-sunken text-muted", dot: "bg-faint" },
  todo: { pill: "border border-dashed border-accent/50 text-accent", dot: "bg-accent/50" },
};
const KINDS: EventKind[] = ["deadline", "round_end", "start", "end", "todo"];

interface Cell {
  key: string;
  y: number;
  m: number;
  d: number;
  inMonth: boolean;
}

function buildGrid(y: number, m: number): Cell[] {
  const first = new Date(Date.UTC(y, m - 1, 1));
  const lead = (first.getUTCDay() + 6) % 7; // Monday-first
  const days = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const total = Math.ceil((lead + days) / 7) * 7;
  return Array.from({ length: total }, (_, i) => {
    const dt = new Date(Date.UTC(y, m - 1, 1 - lead + i));
    const cy = dt.getUTCFullYear();
    const cm = dt.getUTCMonth() + 1;
    const cd = dt.getUTCDate();
    return { key: ymd(cy, cm, cd), y: cy, m: cm, d: cd, inMonth: cm === m };
  });
}

function href(uid: string) {
  return `/hackathon/?uid=${encodeURIComponent(uid)}`;
}

function EventPill({ e, tz }: { e: CalendarEvent; tz: string }) {
  return (
    <Link
      href={href(e.uid)}
      title={`${e.title}: ${e.label} at ${formatTime(e.at, tz)}${e.approximate ? " (approximate)" : ""}`}
      className={cx("flex items-center gap-1 truncate rounded px-1.5 py-0.5 text-[11px] leading-4 hover:brightness-95 dark:hover:brightness-125", KIND_STYLE[e.kind].pill)}
    >
      <span className="shrink-0 tabular-nums opacity-80">
        {e.approximate && "~"}
        {formatTime(e.at, tz)}
      </span>
      <span className="truncate font-medium">{e.title}</span>
    </Link>
  );
}

function AgendaItem({ e, tz }: { e: CalendarEvent; tz: string }) {
  return (
    <li>
      <Link href={href(e.uid)} className="flex items-start gap-3 rounded-lg px-3 py-2.5 hover:bg-sunken">
        <span aria-hidden className={cx("mt-1.5 h-2 w-2 shrink-0 rounded-full", KIND_STYLE[e.kind].dot)} />
        <span className="w-12 shrink-0 text-sm tabular-nums text-muted">
          {e.approximate && <Approx />}
          {formatTime(e.at, tz)}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5 text-sm font-medium">
            <span className="truncate">{e.title}</span>
            {e.tracked_status && <IconStar filled width={12} height={12} className="shrink-0 text-accent" aria-label="tracked" />}
          </span>
          <span className="block text-xs text-muted first-letter:uppercase">
            {e.label}
            {e.label.toLowerCase() !== EVENT_KIND_LABEL[e.kind].toLowerCase() && <span className="text-faint"> · {EVENT_KIND_LABEL[e.kind]}</span>}
          </span>
        </span>
      </Link>
    </li>
  );
}

function dayHeading(key: string): string {
  const [y, m, d] = key.split("-").map(Number);
  return new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" }).format(
    new Date(Date.UTC(y, m - 1, d)),
  );
}

function CalendarView() {
  const { tz, signedIn, loading: sessionLoading } = useSession();
  const now = useNow();
  const [ym, setYm] = useState<{ y: number; m: number } | null>(null);
  const [scope, setScope] = useState<"tracked" | "all">("tracked");
  const [events, setEvents] = useState<CalendarEvent[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const todayKey = dayKey(now, tz);
  useEffect(() => {
    if (sessionLoading) return;
    const p = dayParts(new Date(), tz);
    setYm((cur) => cur ?? { y: p.y, m: p.m });
  }, [sessionLoading, tz]);

  const effScope = signedIn ? scope : "all";
  const grid = useMemo(() => (ym ? buildGrid(ym.y, ym.m) : []), [ym]);

  const load = useCallback(async () => {
    if (!ym || !grid.length) return;
    const a = grid[0];
    const last = grid[grid.length - 1];
    const after = new Date(Date.UTC(last.y, last.m - 1, last.d + 1));
    const from = zonedToUtcIso(a.y, a.m, a.d, 0, 0, tz);
    const to = zonedToUtcIso(after.getUTCFullYear(), after.getUTCMonth() + 1, after.getUTCDate(), 0, 0, tz);
    setEvents(null);
    setError(null);
    try {
      const res = await api.calendar(from, to, effScope);
      setEvents([...res.events].sort((x, y) => Date.parse(x.at) - Date.parse(y.at)));
    } catch (e) {
      setError(e);
    }
  }, [ym, grid, tz, effScope]);
  useEffect(() => {
    void load();
  }, [load]);

  const byDay = useMemo(() => {
    const map = new Map<string, CalendarEvent[]>();
    for (const e of events ?? []) {
      const k = dayKey(e.at, tz);
      if (!map.has(k)) map.set(k, []);
      map.get(k)!.push(e);
    }
    return map;
  }, [events, tz]);

  const shift = (delta: number) => {
    setSelected(null);
    setYm((cur) => {
      if (!cur) return cur;
      const d = new Date(Date.UTC(cur.y, cur.m - 1 + delta, 1));
      return { y: d.getUTCFullYear(), m: d.getUTCMonth() + 1 };
    });
  };

  const monthDays = grid.filter((c) => c.inMonth && byDay.has(c.key));
  const selectedEvents = selected ? byDay.get(selected) ?? [] : [];

  return (
    <>
      <PageHeader
        title="Calendar"
        subtitle={
          effScope === "tracked" ? "Deadlines, rounds and to-dos for the hackathons you track." : "Deadlines, starts and ends of every open hackathon."
        }
        actions={
          signedIn ? (
            <div role="group" aria-label="Scope" className="inline-flex rounded-lg border border-line bg-surface p-0.5 shadow-sm">
              {(["tracked", "all"] as const).map((s) => (
                <button
                  key={s}
                  type="button"
                  aria-pressed={scope === s}
                  onClick={() => setScope(s)}
                  className={cx("rounded-md px-3 py-1.5 text-xs font-medium", scope === s ? "bg-sunken text-ink" : "text-muted hover:text-ink")}
                >
                  {s === "tracked" ? "Tracked" : "All hackathons"}
                </button>
              ))}
            </div>
          ) : null
        }
      />

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <button type="button" className="btn-secondary px-2" onClick={() => shift(-1)}>
            <IconChevronLeft />
            <span className="sr-only">Previous month</span>
          </button>
          <button type="button" className="btn-secondary px-2" onClick={() => shift(1)}>
            <IconChevronRight />
            <span className="sr-only">Next month</span>
          </button>
          <h2 className="ml-2 text-lg font-semibold tracking-tight" aria-live="polite">
            {ym ? monthLabel(ym.y, ym.m) : <Skeleton className="h-6 w-36" />}
          </h2>
          <button
            type="button"
            className="btn-ghost btn-sm ml-1"
            onClick={() => {
              const p = dayParts(new Date(), tz);
              setSelected(null);
              setYm({ y: p.y, m: p.m });
            }}
          >
            Today
          </button>
        </div>
        <ul className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted" aria-label="Legend">
          {KINDS.filter((k) => effScope === "tracked" || k !== "todo").map((k) => (
            <li key={k} className="flex items-center gap-1.5">
              <span aria-hidden className={cx("h-2 w-2 rounded-full", KIND_STYLE[k].dot)} />
              {EVENT_KIND_LABEL[k]}
            </li>
          ))}
        </ul>
      </div>

      {error ? (
        <ErrorState error={error} onRetry={load} />
      ) : (
        <>
          {/* Month grid (md and up) */}
          <div className="card hidden overflow-hidden md:block" aria-busy={events === null}>
            <div className="grid grid-cols-7 border-b border-line bg-sunken/60">
              {weekdayLabels().map((w) => (
                <div key={w} className="px-2 py-2 text-xs font-medium text-faint">
                  {w}
                </div>
              ))}
            </div>
            <div className="grid grid-cols-7">
              {grid.map((c, i) => {
                const list = byDay.get(c.key) ?? [];
                const isToday = c.key === todayKey;
                const isSel = c.key === selected;
                const shown = list.slice(0, 3);
                return (
                  <div
                    key={c.key}
                    className={cx(
                      "min-h-[7.5rem] border-line p-1.5",
                      i % 7 !== 6 && "border-r",
                      i < grid.length - 7 && "border-b",
                      !c.inMonth && "bg-sunken/40",
                      isSel && "bg-accent-soft/60",
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => setSelected(isSel ? null : c.key)}
                      aria-pressed={isSel}
                      aria-label={`${dayHeading(c.key)}, ${list.length} event${list.length === 1 ? "" : "s"}`}
                      className={cx(
                        "mb-1 flex h-6 min-w-6 items-center justify-center rounded-full px-1.5 text-xs tabular-nums",
                        isToday ? "bg-accent font-semibold text-accent-ink" : c.inMonth ? "text-ink hover:bg-sunken" : "text-faint hover:bg-sunken",
                      )}
                    >
                      {c.d}
                    </button>
                    {events === null ? (
                      c.inMonth && (i * 7) % 5 === 1 ? <Skeleton className="h-4 w-full" /> : null
                    ) : (
                      <div className="space-y-0.5">
                        {shown.map((e, j) => (
                          <EventPill key={`${e.uid}-${e.kind}-${j}`} e={e} tz={tz} />
                        ))}
                        {list.length > shown.length && (
                          <button type="button" className="px-1.5 text-[11px] font-medium text-muted hover:text-ink" onClick={() => setSelected(c.key)}>
                            +{list.length - shown.length} more
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {selected && (
            <section className="card mt-4 hidden p-2 md:block" aria-label={`Events on ${dayHeading(selected)}`}>
              <div className="flex items-center justify-between px-3 py-2">
                <h3 className="text-sm font-semibold">{dayHeading(selected)}</h3>
                <button type="button" className="btn-ghost btn-sm" onClick={() => setSelected(null)}>
                  Close
                </button>
              </div>
              {selectedEvents.length ? (
                <ul>
                  {selectedEvents.map((e, j) => (
                    <AgendaItem key={`${e.uid}-${j}`} e={e} tz={tz} />
                  ))}
                </ul>
              ) : (
                <p className="px-3 pb-3 text-sm text-faint">Nothing on this day.</p>
              )}
            </section>
          )}

          {/* Agenda (narrow screens) */}
          <div className="md:hidden">
            {events === null ? (
              <div className="space-y-3">
                {Array.from({ length: 4 }, (_, i) => (
                  <Skeleton key={i} className="h-16 w-full rounded-xl" />
                ))}
              </div>
            ) : monthDays.length === 0 ? (
              <EmptyState title="Nothing this month">
                {effScope === "tracked" ? "Track hackathons to see their deadlines here." : "No deadlines fall in this month."}
              </EmptyState>
            ) : (
              <div className="space-y-5">
                {monthDays.map((c) => (
                  <section key={c.key} aria-label={dayHeading(c.key)}>
                    <h3 className={cx("mb-1 px-1 text-xs font-semibold uppercase tracking-wider", c.key === todayKey ? "text-accent" : "text-faint")}>
                      {c.key === todayKey ? "Today · " : ""}
                      {dayHeading(c.key)}
                    </h3>
                    <ul className="card divide-y divide-line overflow-hidden p-1">
                      {(byDay.get(c.key) ?? []).map((e, j) => (
                        <AgendaItem key={`${e.uid}-${j}`} e={e} tz={tz} />
                      ))}
                    </ul>
                  </section>
                ))}
              </div>
            )}
          </div>

          {events !== null && events.length === 0 && (
            <p className="mt-4 hidden text-center text-sm text-faint md:block">
              {effScope === "tracked" ? "No events for your tracked hackathons in this range." : "No events in this range."}
            </p>
          )}
          <p className="mt-4 text-xs text-faint">
            Times in {tz}. <Approx /> marks an approximate time.
          </p>
        </>
      )}
    </>
  );
}

export default function Page() {
  useDocTitle("Calendar");
  return (
    <Suspense fallback={null}>
      <CalendarView />
    </Suspense>
  );
}
