"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { IconChevronLeft, IconExternal, IconGlobe, IconPin, IconTrophy, IconUsers } from "@/components/icons";
import { TrackButton } from "@/components/TrackButton";
import { TrackingPanel } from "@/components/TrackingPanel";
import {
  Approx,
  DeadlineWhen,
  EmptyState,
  ErrorState,
  SectionTitle,
  Skeleton,
  SourceBadges,
  cx,
  useDocTitle,
} from "@/components/ui";
import { api, ApiError, type DateChange, type HackathonDetail, type Round, type TrackedOut } from "@/lib/api";
import { MODE_LABEL, ROUND_KIND_LABEL, fieldLabel, sourceLabel, teamSize } from "@/lib/labels";
import { useNow, useSession } from "@/lib/session";
import { formatDate, formatShort, parse, relative, urgency } from "@/lib/time";

const KEY_DATES: Array<[keyof HackathonDetail, string]> = [
  ["registration_opens_at", "Registration opens"],
  ["registration_closes_at", "Registration closes"],
  ["submission_deadline", "Submission deadline"],
  ["starts_at", "Starts"],
  ["ends_at", "Ends"],
];

const KIND_TONE: Record<string, string> = {
  registration: "bg-accent-soft text-accent",
  submission: "bg-warm-soft text-warm",
  judging: "bg-sunken text-muted",
  results: "bg-good-soft text-good",
  event: "bg-accent text-accent-ink",
  other: "bg-sunken text-muted",
};

function Timeline({ rounds, tz, now }: { rounds: Round[]; tz: string; now: Date }) {
  const sorted = [...rounds].sort((a, b) => {
    const ta = parse(a.starts_at ?? a.ends_at)?.getTime() ?? Infinity;
    const tb = parse(b.starts_at ?? b.ends_at)?.getTime() ?? Infinity;
    return ta - tb;
  });
  return (
    <ol className="relative">
      {sorted.map((r, i) => {
        const start = parse(r.starts_at);
        const end = parse(r.ends_at);
        const past = end ? end < now : start ? start < now && !end : false;
        const current = !past && start != null && start <= now && (!end || end >= now);
        const last = i === sorted.length - 1;
        return (
          <li key={`${r.name}-${i}`} className={cx("relative flex gap-4 pb-6", last && "pb-0", past && "opacity-55")}>
            <div className="relative flex w-4 shrink-0 justify-center">
              {!last && <span aria-hidden className="absolute top-4 h-full w-px bg-line" />}
              <span
                aria-hidden
                className={cx(
                  "relative mt-1 h-3 w-3 rounded-full border-2",
                  current ? "border-accent bg-accent ring-4 ring-accent/20" : past ? "border-line bg-line" : "border-accent bg-surface",
                )}
              />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-medium text-ink">{r.name}</h3>
                <span className={cx("chip text-[10px] uppercase tracking-wide", KIND_TONE[r.kind] ?? KIND_TONE.other)}>
                  {ROUND_KIND_LABEL[r.kind] ?? r.kind}
                </span>
                {current && <span className="chip bg-accent-soft text-[10px] font-semibold uppercase text-accent">Now</span>}
                {past && <span className="sr-only">(past)</span>}
              </div>
              <p className="mt-0.5 text-xs tabular-nums text-muted">
                {r.starts_at ? formatShort(r.starts_at, tz, now) : "—"}
                {r.ends_at && r.ends_at !== r.starts_at && <> → {formatShort(r.ends_at, tz, now)}</>}
                {!past && (end || start) && (
                  <span className="ml-2 text-faint">
                    {current && end ? `ends ${relative(r.ends_at, now)}` : start && start > now ? `starts ${relative(r.starts_at, now)}` : ""}
                  </span>
                )}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

function ChangeRow({ c, tz, now }: { c: DateChange; tz: string; now: Date }) {
  const o = parse(c.old_value);
  const n = parse(c.new_value);
  let verb = "changed";
  let tone = "text-muted";
  let delta = "";
  if (o && n) {
    const days = (n.getTime() - o.getTime()) / 86_400_000;
    const abs = Math.abs(days);
    delta = abs >= 1 ? `${Math.round(abs)}d` : `${Math.round(abs * 24)}h`;
    if (days > 0) {
      verb = "extended";
      tone = "text-good";
    } else {
      verb = "moved earlier";
      tone = "text-warm";
    }
  } else if (!o && n) verb = "announced";
  else if (o && !n) verb = "removed";
  const show = (v: string | null, d: Date | null) => (d ? formatShort(v, tz, now) : v ? (v.length > 40 ? `${v.slice(0, 40)}…` : v) : "—");
  return (
    <li className="flex flex-col gap-0.5 py-3 text-sm sm:flex-row sm:items-baseline sm:gap-4">
      <time dateTime={c.changed_at} className="w-28 shrink-0 text-xs text-faint">
        {formatDate(c.changed_at, tz)}
      </time>
      <div className="min-w-0">
        <span className="font-medium">{fieldLabel(c.field)}</span>{" "}
        <span className={tone}>
          {verb}
          {delta && ` by ${delta}`}
        </span>
        <p className="text-xs tabular-nums text-muted">
          <span className={cx(o && "line-through decoration-faint")}>{show(c.old_value, o)}</span>
          <span className="mx-1.5 text-faint">→</span>
          <span className="text-ink">{show(c.new_value, n)}</span>
        </p>
      </div>
    </li>
  );
}

function DetailSkeleton() {
  return (
    <div aria-busy>
      <Skeleton className="h-4 w-24" />
      <Skeleton className="mt-6 h-8 w-2/3 max-w-lg" />
      <Skeleton className="mt-3 h-4 w-64" />
      <div className="mt-8 grid gap-6 lg:grid-cols-[minmax(0,1fr)_24rem]">
        <div className="space-y-6">
          <Skeleton className="h-36 w-full rounded-xl" />
          <Skeleton className="h-64 w-full rounded-xl" />
        </div>
        <Skeleton className="h-80 w-full rounded-xl" />
      </div>
    </div>
  );
}

function Detail() {
  const sp = useSearchParams();
  const router = useRouter();
  const uid = sp.get("uid") ?? "";
  const { tz, me, signedIn, loading: sessionLoading } = useSession();
  const now = useNow();
  const [h, setH] = useState<HackathonDetail | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    if (!uid) return;
    setError(null);
    try {
      setH(await api.hackathon(uid));
    } catch (e) {
      setError(e);
    }
  }, [uid]);

  useEffect(() => {
    if (!sessionLoading) void load();
  }, [load, sessionLoading]);
  useDocTitle(h?.title);

  if (!uid)
    return (
      <EmptyState title="No hackathon selected" action={<Link href="/" className="btn-secondary">Browse hackathons</Link>}>
        Open a hackathon from Discover, My hackathons or the calendar.
      </EmptyState>
    );
  if (error)
    return error instanceof ApiError && error.status === 404 ? (
      <EmptyState title="Hackathon not found" action={<Link href="/" className="btn-secondary">Back to Discover</Link>}>
        Nothing is stored under <code className="font-mono text-xs">{uid}</code>. It may have been removed after a sync.
      </EmptyState>
    ) : (
      <ErrorState error={error} onRetry={load} />
    );
  if (!h) return <DetailSkeleton />;

  const links = [{ source: h.source, url: h.url, uid: h.uid }, ...h.also_listed.filter((a) => a.uid !== h.uid)];
  const team = teamSize(h.team_size_min, h.team_size_max);
  const setTracking = (t: TrackedOut | null) => setH((prev) => (prev ? { ...prev, tracking: t, tracked_status: t?.status ?? null } : prev));
  const nd = h.next_deadline;
  const u = nd ? urgency(nd.at, now) : "later";

  return (
    <article>
      <button type="button" onClick={() => (window.history.length > 1 ? router.back() : router.push("/"))} className="btn-ghost btn-sm -ml-2.5 mb-4">
        <IconChevronLeft width={14} height={14} /> Back
      </button>

      <header className="mb-8 flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <SourceBadges sources={h.sources.length ? h.sources : [h.source]} />
            {h.delisted_at && <span className="chip bg-warm-soft text-warm">Delisted {formatDate(h.delisted_at, tz)}</span>}
          </div>
          <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{h.title}</h1>
          {h.organizer && <p className="mt-1 text-sm text-muted">by {h.organizer}</p>}
          <ul className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-sm text-muted">
            <li className="flex items-center gap-1.5">
              {h.mode === "online" ? <IconGlobe /> : <IconPin />}
              {MODE_LABEL[h.mode]}
              {h.location && ` · ${h.location}`}
            </li>
            {h.prize && (
              <li className="flex items-center gap-1.5">
                <IconTrophy />
                <span className="font-medium text-ink">{h.prize}</span>
                {h.prize_usd != null && !h.prize.startsWith("$") && <span className="text-faint">≈ ${Math.round(h.prize_usd).toLocaleString("en-US")}</span>}
              </li>
            )}
            {team && (
              <li className="flex items-center gap-1.5">
                <IconUsers />
                Teams of {team}
              </li>
            )}
            {h.participants != null && <li>{h.participants.toLocaleString()} registered</li>}
          </ul>
          {h.themes.length > 0 && (
            <ul className="mt-3 flex flex-wrap gap-1.5" aria-label="Themes">
              {h.themes.map((t) => (
                <li key={t} className="chip border border-line bg-surface text-muted">
                  {t}
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {!h.tracking && <TrackButton uid={h.uid} status={null} size="md" onTracked={() => void load()} />}
          {links.map((l) => (
            <a key={l.uid} href={l.url} target="_blank" rel="noreferrer" className="btn-secondary">
              {sourceLabel(l.source)} <IconExternal width={14} height={14} />
            </a>
          ))}
        </div>
      </header>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_25rem] lg:items-start">
        <div className="min-w-0 space-y-6">
          <section className="card overflow-hidden">
            {nd && (
              <div className={cx("flex flex-wrap items-baseline justify-between gap-2 border-b border-line px-5 py-4", u === "critical" ? "bg-warm-soft" : "bg-sunken/60")}>
                <div>
                  <p className="text-xs font-medium uppercase tracking-wider text-faint">Next deadline</p>
                  <p className="mt-0.5 text-base font-semibold first-letter:uppercase">{nd.label}</p>
                </div>
                <DeadlineWhen at={nd.at} approximate={nd.approximate} tz={tz} now={now} layout="inline" long />
              </div>
            )}
            <div className="px-5 py-4">
              <SectionTitle>Key dates</SectionTitle>
              <dl className="divide-y divide-line">
                {KEY_DATES.map(([k, label]) => {
                  const v = h[k] as string | null;
                  if (!v) return null;
                  const approx = h.approx_fields.includes(k as string);
                  const past = (parse(v)?.getTime() ?? 0) < now.getTime();
                  return (
                    <div key={k} className={cx("flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 py-2.5 text-sm", past && "text-faint")}>
                      <dt className={cx(past ? "text-faint" : "text-muted")}>{label}</dt>
                      <dd className="tabular-nums">
                        {approx && <Approx />}
                        <span className={cx(!past && "text-ink")}>{formatShort(v, tz, now)}</span>
                        <span className="ml-2 text-xs text-faint">{relative(v, now)}</span>
                      </dd>
                    </div>
                  );
                })}
                {KEY_DATES.every(([k]) => !h[k]) && <p className="py-2 text-sm text-faint">The source hasn&apos;t published dates yet.</p>}
              </dl>
            </div>
          </section>

          <section className="card px-5 py-5">
            <SectionTitle aside={<span className="text-xs text-faint">{h.rounds.length} rounds</span>}>Timeline</SectionTitle>
            {h.rounds.length ? <Timeline rounds={h.rounds} tz={tz} now={now} /> : <p className="text-sm text-faint">No round timeline published.</p>}
          </section>

          {h.date_changes.length > 0 && (
            <section className="card px-5 py-4">
              <SectionTitle>Date changes</SectionTitle>
              <ul className="divide-y divide-line">
                {[...h.date_changes].reverse().map((c, i) => (
                  <ChangeRow key={i} c={c} tz={tz} now={now} />
                ))}
              </ul>
            </section>
          )}
        </div>

        <aside className="min-w-0 lg:sticky lg:top-20">
          {h.tracking ? (
            <TrackingPanel key={h.tracking.uid} t={h.tracking} tz={tz} now={now} onChange={setTracking} onUntracked={() => setTracking(null)} />
          ) : (
            <div className="card px-5 py-6">
              <h2 className="text-sm font-semibold">Track this hackathon</h2>
              <p className="mt-1 text-sm text-muted">
                Keep your status, team, notes and a checklist in one place. Deadlines adapt to where you are in the pipeline.
              </p>
              <div className="mt-4">
                {me?.auth_mode === "oauth" && !signedIn ? (
                  <p className="text-sm text-faint">Sign in to start tracking.</p>
                ) : (
                  <TrackButton uid={h.uid} status={null} size="md" onTracked={() => void load()} />
                )}
              </div>
            </div>
          )}
        </aside>
      </div>
    </article>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<DetailSkeleton />}>
      <Detail />
    </Suspense>
  );
}
