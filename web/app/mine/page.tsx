"use client";

import Link from "next/link";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { IconChevronDown, IconChevronRight } from "@/components/icons";
import {
  Approx,
  EmptyState,
  ErrorState,
  InlineError,
  PageHeader,
  RequireUser,
  SectionTitle,
  Skeleton,
  SourceBadges,
  StatusChip,
  cx,
  useDocTitle,
} from "@/components/ui";
import { api, ApiError, PIPELINE, STATUSES, TERMINAL, type Status, type TrackedOut } from "@/lib/api";
import { STATUS_LABEL } from "@/lib/labels";
import { useNow, useSession } from "@/lib/session";
import { formatShort, relative, urgency } from "@/lib/time";

type ColumnId = Status | "finished";
const COLUMNS: ColumnId[] = [...PIPELINE, "finished"];
const COLUMN_LABEL: Record<ColumnId, string> = { ...STATUS_LABEL, finished: "Finished" };
const DRAG_TYPE = "application/x-logr-uid";

function columnOf(s: Status): ColumnId {
  return TERMINAL.includes(s) ? "finished" : s;
}

function titleOf(t: TrackedOut): string {
  return t.hackathon?.title ?? t.uid;
}

function href(uid: string) {
  return `/hackathon/?uid=${encodeURIComponent(uid)}`;
}

function NextAction({ t, tz, now, compact }: { t: TrackedOut; tz: string; now: Date; compact?: boolean }) {
  const a = t.next_action;
  if (!a) return <p className="text-xs text-faint">Nothing due</p>;
  const u = urgency(a.at, now);
  return (
    <p className="text-xs leading-relaxed text-muted">
      <span className="text-ink first-letter:uppercase">{a.label}</span>
      {compact ? <br /> : " · "}
      <span className="tabular-nums">
        {a.approximate && <Approx />}
        {formatShort(a.at, tz, now)}
      </span>{" "}
      <span className={cx("tabular-nums", u === "critical" ? "font-semibold text-warm" : u === "soon" ? "font-medium text-ink" : "text-faint")}>
        {relative(a.at, now)}
      </span>
    </p>
  );
}

function BoardCard({
  t,
  tz,
  now,
  onMove,
  dragging,
  setDragging,
}: {
  t: TrackedOut;
  tz: string;
  now: Date;
  onMove: (uid: string, s: Status) => void;
  dragging: string | null;
  setDragging: (uid: string | null) => void;
}) {
  const open = t.checklist.filter((c) => !c.done).length;
  const selectId = `move-${t.uid}`;
  return (
    <li
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(DRAG_TYPE, t.uid);
        e.dataTransfer.setData("text/plain", t.uid);
        e.dataTransfer.effectAllowed = "move";
        setDragging(t.uid);
      }}
      onDragEnd={() => setDragging(null)}
      className={cx(
        "group rounded-lg border border-line bg-surface p-3 shadow-card transition",
        "cursor-grab active:cursor-grabbing hover:border-faint/50",
        dragging === t.uid && "opacity-40",
      )}
    >
      <Link href={href(t.uid)} className="line-clamp-2 text-sm font-medium leading-snug text-ink hover:underline">
        {titleOf(t)}
      </Link>
      <div className="mt-1.5">
        <NextAction t={t} tz={tz} now={now} compact />
      </div>
      <div className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1.5 border-t border-line pt-2">
        {t.hackathon && <SourceBadges sources={t.hackathon.sources.length ? t.hackathon.sources : [t.hackathon.source]} size="xs" />}
        {open > 0 && <span className="whitespace-nowrap text-[11px] text-faint">{open} to-do</span>}
        <label htmlFor={selectId} className="sr-only">
          Move {titleOf(t)} to status
        </label>
        <select
          id={selectId}
          value={t.status}
          onChange={(e) => onMove(t.uid, e.target.value as Status)}
          className="ml-auto max-w-full cursor-pointer rounded-md border border-line bg-surface py-0.5 pl-1.5 pr-6 text-[11px] text-muted hover:text-ink"
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABEL[s]}
            </option>
          ))}
        </select>
      </div>
    </li>
  );
}

function Column({
  id,
  items,
  children,
  onDropUid,
  collapsed,
  onToggle,
  dragging,
}: {
  id: ColumnId;
  items: TrackedOut[];
  children: React.ReactNode;
  onDropUid: (uid: string, col: ColumnId) => void;
  collapsed?: boolean;
  onToggle?: () => void;
  dragging: boolean;
}) {
  const [over, setOver] = useState(false);
  const depth = useRef(0);
  const handlers = {
    onDragOver: (e: DragEvent) => {
      if (!e.dataTransfer.types.includes(DRAG_TYPE)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
    },
    onDragEnter: (e: DragEvent) => {
      if (!e.dataTransfer.types.includes(DRAG_TYPE)) return;
      depth.current += 1;
      setOver(true);
    },
    onDragLeave: () => {
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setOver(false);
    },
    onDrop: (e: DragEvent) => {
      e.preventDefault();
      depth.current = 0;
      setOver(false);
      const uid = e.dataTransfer.getData(DRAG_TYPE);
      if (uid) onDropUid(uid, id);
    },
  };
  const headingId = `col-${id}`;

  if (collapsed)
    return (
      <section
        aria-labelledby={headingId}
        {...handlers}
        className={cx(
          "rounded-xl border border-dashed transition-colors xl:w-12",
          over ? "border-accent bg-accent-soft" : dragging ? "border-faint/50" : "border-line",
        )}
      >
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={false}
          className="flex w-full items-center gap-2 rounded-xl px-3 py-3 text-left text-sm text-muted hover:text-ink xl:h-full xl:flex-col xl:px-0 xl:py-4"
        >
          <IconChevronRight className="shrink-0" width={14} height={14} />
          <span id={headingId} className="font-medium xl:[writing-mode:vertical-rl]">
            {COLUMN_LABEL[id]}
          </span>
          <span className="rounded-full bg-sunken px-1.5 text-xs tabular-nums">{items.length}</span>
        </button>
      </section>
    );

  return (
    <section
      aria-labelledby={headingId}
      {...handlers}
      className={cx(
        "flex min-w-0 flex-col rounded-xl border p-2 transition-colors",
        over ? "border-accent bg-accent-soft/70" : dragging ? "border-dashed border-faint/50 bg-sunken/60" : "border-transparent bg-sunken/60",
      )}
    >
      <div className="flex items-center justify-between px-1.5 pb-2 pt-1">
        <h2 id={headingId} className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted">
          {COLUMN_LABEL[id]}
          <span className="rounded-full bg-surface px-1.5 text-[11px] font-medium tabular-nums text-faint">{items.length}</span>
        </h2>
        {onToggle && (
          <button type="button" onClick={onToggle} aria-expanded className="rounded p-1 text-faint hover:text-ink">
            <IconChevronDown width={14} height={14} />
            <span className="sr-only">Collapse {COLUMN_LABEL[id]}</span>
          </button>
        )}
      </div>
      <ul className="flex min-h-[4rem] flex-col gap-2">{children}</ul>
      {items.length === 0 && <p className="px-1.5 pb-2 text-xs text-faint">{dragging ? "Drop here" : "Nothing here"}</p>}
    </section>
  );
}

function BoardSkeleton() {
  return (
    <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-5">
      {Array.from({ length: 5 }, (_, i) => (
        <div key={i} className="space-y-2 rounded-xl bg-sunken/60 p-2">
          <Skeleton className="m-1.5 h-3 w-20" />
          {Array.from({ length: 2 - (i % 2) }, (_, j) => (
            <Skeleton key={j} className="h-24 w-full rounded-lg" />
          ))}
        </div>
      ))}
    </div>
  );
}

function Board() {
  const { tz } = useSession();
  const now = useNow();
  const [items, setItems] = useState<TrackedOut[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [moveError, setMoveError] = useState<unknown>(null);
  const [finishedOpen, setFinishedOpen] = useState(false);
  const [dragging, setDragging] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");
  const [pendingFinish, setPendingFinish] = useState<TrackedOut | null>(null);
  const finishRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (pendingFinish) finishRef.current?.focus();
  }, [pendingFinish]);

  const load = useCallback(async () => {
    setError(null);
    try {
      setItems(await api.tracked());
    } catch (e) {
      setError(e);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const move = useCallback(
    async (uid: string, status: Status) => {
      const prev = items;
      const cur = prev?.find((t) => t.uid === uid);
      if (!prev || !cur || cur.status === status) return;
      setMoveError(null);
      setItems(prev.map((t) => (t.uid === uid ? { ...t, status } : t)));
      try {
        const updated = await api.updateTracked(uid, { status });
        setItems((p) => p?.map((t) => (t.uid === uid ? updated : t)) ?? null);
        setAnnounce(`Moved ${titleOf(cur)} to ${STATUS_LABEL[status]}`);
      } catch (e) {
        setItems(prev);
        setMoveError(e);
      }
    },
    [items],
  );

  const onDropUid = (uid: string, col: ColumnId) => {
    const t = items?.find((x) => x.uid === uid);
    if (!t) return;
    if (col === "finished") {
      // Finished covers three outcomes; ask which one.
      if (!TERMINAL.includes(t.status)) {
        setFinishedOpen(true);
        setPendingFinish(t);
      }
    } else void move(uid, col);
  };

  const groups = useMemo(() => {
    const g = Object.fromEntries(COLUMNS.map((c) => [c, [] as TrackedOut[]])) as Record<ColumnId, TrackedOut[]>;
    for (const t of items ?? []) g[columnOf(t.status)].push(t);
    return g;
  }, [items]);

  const nextUp = useMemo(
    () =>
      (items ?? [])
        .filter((t) => !TERMINAL.includes(t.status) && t.next_action)
        .sort((a, b) => Date.parse(a.next_action!.at) - Date.parse(b.next_action!.at))
        .slice(0, 5),
    [items],
  );

  if (error) {
    if (error instanceof ApiError && error.status === 401) return <RequireUser what="track hackathons" unauthorized>{null}</RequireUser>;
    return <ErrorState error={error} onRetry={load} />;
  }

  return (
    <>
      <PageHeader
        title="My hackathons"
        subtitle="Drag cards between columns, or use the status menu on each card."
        actions={
          <Link href="/" className="btn-secondary">
            Find more
          </Link>
        }
      />
      <p className="sr-only" aria-live="polite">
        {announce}
      </p>

      {items === null ? (
        <>
          <Skeleton className="mb-8 h-40 w-full rounded-xl" />
          <BoardSkeleton />
        </>
      ) : items.length === 0 ? (
        <EmptyState
          title="You're not tracking anything yet"
          action={
            <Link href="/" className="btn-primary">
              Browse hackathons
            </Link>
          }
        >
          Track a hackathon from Discover and it lands here as Interested.
        </EmptyState>
      ) : (
        <>
          <section aria-labelledby="next-up" className="mb-8">
            <SectionTitle>
              <span id="next-up">Next up</span>
            </SectionTitle>
            {nextUp.length === 0 ? (
              <p className="text-sm text-faint">No upcoming actions on your active hackathons.</p>
            ) : (
              <ol className="card divide-y divide-line overflow-hidden">
                {nextUp.map((t) => {
                  const a = t.next_action!;
                  const u = urgency(a.at, now);
                  return (
                    <li key={t.uid} className="flex flex-col gap-1 px-4 py-3 sm:flex-row sm:items-center sm:gap-4">
                      <div className="flex w-full items-center gap-3 sm:w-28 sm:shrink-0">
                        <span
                          className={cx(
                            "text-sm tabular-nums",
                            u === "critical" ? "font-semibold text-warm" : u === "soon" ? "font-medium text-ink" : "text-muted",
                          )}
                        >
                          {relative(a.at, now)}
                        </span>
                        <span className="ml-auto sm:hidden">
                          <StatusChip status={t.status} />
                        </span>
                      </div>
                      <div className="min-w-0 flex-1">
                        <Link href={href(t.uid)} className="block truncate text-sm font-medium hover:underline">
                          {titleOf(t)}
                        </Link>
                        <p className="truncate text-xs text-muted">
                          <span className="first-letter:uppercase">{a.label}</span> · {a.approximate && <Approx />}
                          {formatShort(a.at, tz, now)}
                        </p>
                      </div>
                      <span className="hidden sm:block">
                        <StatusChip status={t.status} />
                      </span>
                    </li>
                  );
                })}
              </ol>
            )}
          </section>

          <SectionTitle>Pipeline</SectionTitle>
          <InlineError error={moveError} />
          {pendingFinish && (
            <div
              role="group"
              aria-label="Choose an outcome"
              className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-accent/30 bg-accent-soft px-4 py-3 text-sm"
              onKeyDown={(e) => e.key === "Escape" && setPendingFinish(null)}
            >
              <span className="mr-1">
                How did <strong className="font-semibold">{titleOf(pendingFinish)}</strong> end?
              </span>
              {TERMINAL.map((s, i) => (
                <button
                  key={s}
                  ref={i === 0 ? finishRef : undefined}
                  type="button"
                  className="btn-secondary btn-sm"
                  onClick={() => {
                    void move(pendingFinish.uid, s);
                    setPendingFinish(null);
                  }}
                >
                  {STATUS_LABEL[s]}
                </button>
              ))}
              <button type="button" className="btn-ghost btn-sm" onClick={() => setPendingFinish(null)}>
                Cancel
              </button>
            </div>
          )}
          <div
            className={cx(
              "grid gap-3 md:grid-cols-3",
              finishedOpen ? "xl:grid-cols-6" : "xl:grid-cols-[repeat(5,minmax(0,1fr))_auto]",
            )}
          >
            {COLUMNS.map((c) => {
              const list = groups[c];
              const isFinished = c === "finished";
              return (
                <Column
                  key={c}
                  id={c}
                  items={list}
                  onDropUid={onDropUid}
                  dragging={!!dragging}
                  collapsed={isFinished && !finishedOpen}
                  onToggle={isFinished ? () => setFinishedOpen((o) => !o) : undefined}
                >
                  {list.map((t) => (
                    <BoardCard key={t.uid} t={t} tz={tz} now={now} onMove={move} dragging={dragging} setDragging={setDragging} />
                  ))}
                </Column>
              );
            })}
          </div>
        </>
      )}
    </>
  );
}

export default function Page() {
  useDocTitle("My hackathons");
  return (
    <Suspense fallback={null}>
      <RequireUser what="track hackathons">
        <Board />
      </RequireUser>
    </Suspense>
  );
}
