"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { api, ApiError, type Deadline, type Mode, type Source, type Status } from "@/lib/api";
import { MODE_LABEL, SOURCE_LABEL, STATUS_LABEL } from "@/lib/labels";
import { useSession } from "@/lib/session";
import { formatDateTime, formatShort, relative, urgency } from "@/lib/time";
import { IconCheck, IconGitHub, IconGoogle } from "./icons";

export function cx(...c: Array<string | false | null | undefined>): string {
  return c.filter(Boolean).join(" ");
}

// --- layout -------------------------------------------------------------------

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:mb-8 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight text-ink sm:text-[1.7rem]">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/** A titled block on a page like Settings: h2 + optional subtitle and actions. */
export function PageSection({
  id,
  title,
  subtitle,
  aside,
  className,
  children,
}: {
  id: string;
  title: ReactNode;
  subtitle?: ReactNode;
  aside?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={id} className={className}>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 id={id} className="text-lg font-semibold tracking-tight">
            {title}
          </h2>
          {subtitle && <p className="text-sm text-muted">{subtitle}</p>}
        </div>
        {aside && <div className="flex items-center gap-3">{aside}</div>}
      </div>
      {children}
    </section>
  );
}

export function SectionTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-3 flex items-baseline justify-between gap-3">
      <h2 className="text-xs font-semibold uppercase tracking-wider text-faint">{children}</h2>
      {aside}
    </div>
  );
}

// --- states ---------------------------------------------------------------------

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("skeleton", className)} aria-hidden />;
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center rounded-xl border border-dashed border-line px-6 py-12 text-center">
      <p className="text-sm font-medium text-ink">{title}</p>
      {children && <p className="mt-1 max-w-md text-sm text-muted">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg = error instanceof ApiError ? error.detail : error instanceof Error ? error.message : String(error);
  return (
    <div role="alert" className="rounded-xl border border-warm/30 bg-warm-soft px-4 py-3 text-sm text-ink">
      <p className="font-medium text-warm">Something went wrong</p>
      <p className="mt-0.5 text-muted">{msg}</p>
      {onRetry && (
        <button type="button" className="btn-secondary btn-sm mt-3" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function InlineError({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof ApiError ? error.detail : error instanceof Error ? error.message : String(error);
  return (
    <p role="alert" className="mt-2 text-xs text-warm">
      {msg}
    </p>
  );
}

/** A "✓ Saved" note that fades in for a moment after a save. */
export function SavedNote({ show, children = "Saved" }: { show: boolean; children?: ReactNode }) {
  return (
    <span role="status" className={cx("inline-flex items-center gap-1 text-xs text-good transition-opacity", show ? "opacity-100" : "opacity-0")}>
      <IconCheck width={14} height={14} aria-hidden /> {show ? children : ""}
    </span>
  );
}

export function useCurrentPath(): string {
  const pathname = usePathname() || "/";
  const sp = useSearchParams();
  const qs = sp?.toString();
  return qs ? `${pathname}?${qs}` : pathname;
}

export function SignInButtons({ compact = false }: { compact?: boolean }) {
  const { me } = useSession();
  const next = useCurrentPath();
  if (!me || me.auth_mode !== "oauth") return null;
  if (!me.providers.length) return <span className="text-xs text-muted">No sign-in providers configured</span>;
  return (
    <div className="flex flex-wrap items-center gap-2">
      {me.providers.map((p) => (
        <a key={p} href={api.loginUrl(p, next)} className={cx("btn-secondary", compact && "btn-sm")}>
          {p === "github" ? <IconGitHub /> : p === "google" ? <IconGoogle /> : null}
          {compact ? (p === "github" ? "GitHub" : p === "google" ? "Google" : p) : `Sign in with ${p === "github" ? "GitHub" : p === "google" ? "Google" : p}`}
        </a>
      ))}
    </div>
  );
}

export function SignInPrompt({ what }: { what: string }) {
  return (
    <div className="card mx-auto max-w-lg px-6 py-10 text-center">
      <h2 className="text-lg font-semibold">Sign in to {what}</h2>
      <p className="mx-auto mt-2 max-w-sm text-sm text-muted">
        Browsing hackathons is open to everyone. Tracking, saved searches and preferences are tied to your account.
      </p>
      <div className="mt-6 flex justify-center">
        <SignInButtons />
      </div>
    </div>
  );
}

/** Wraps pages that need a user: shows a skeleton while /api/me loads and a
 * sign-in prompt when nobody is signed in (or a request came back 401). */
export function RequireUser({ what, children, unauthorized }: { what: string; children: ReactNode; unauthorized?: boolean }) {
  const { me, loading, meError } = useSession();
  if (loading)
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  if (meError && !me) return <ErrorState error={meError} />;
  if (!me?.user || unauthorized) return <SignInPrompt what={what} />;
  return <>{children}</>;
}

// --- badges -------------------------------------------------------------------------

export function SourceBadges({ sources, size = "sm" }: { sources: Source[]; size?: "sm" | "xs" }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {sources.map((s) => (
        <span
          key={s}
          className={cx(
            "inline-flex items-center rounded border border-line bg-sunken font-medium text-muted",
            size === "xs" ? "px-1 py-0 text-[10px]" : "px-1.5 py-0.5 text-[11px]",
          )}
        >
          {SOURCE_LABEL[s] ?? s}
        </span>
      ))}
    </span>
  );
}

export function ModeText({ mode }: { mode: Mode }) {
  return <span>{MODE_LABEL[mode] ?? mode}</span>;
}

const STATUS_TONE: Record<Status, string> = {
  interested: "bg-sunken text-muted",
  registered: "bg-accent-soft text-accent",
  team_formed: "bg-accent-soft text-accent",
  submitted: "bg-accent text-accent-ink",
  shortlisted: "bg-good-soft text-good",
  won: "bg-good text-surface",
  lost: "bg-sunken text-faint",
  withdrawn: "bg-sunken text-faint",
};

export function StatusChip({ status, className }: { status: Status; className?: string }) {
  return <span className={cx("chip", STATUS_TONE[status], className)}>{STATUS_LABEL[status] ?? status}</span>;
}

export function Approx({ label = "Approximate: the source doesn't publish the exact time" }: { label?: string }) {
  return (
    <abbr
      title={label}
      aria-label="approximately"
      className="cursor-help font-semibold text-warm no-underline decoration-dotted"
      style={{ textDecoration: "none" }}
    >
      ~
    </abbr>
  );
}

/** Date + relative time, with urgency colouring and approximate marker. */
export function DeadlineWhen({
  at,
  approximate,
  tz,
  now,
  layout = "stack",
  long = false,
}: {
  at: string;
  approximate: boolean;
  tz: string;
  now: Date;
  layout?: "stack" | "inline";
  long?: boolean;
}) {
  const u = urgency(at, now);
  const rel = relative(at, now);
  const relCls = cx(
    "tabular-nums",
    u === "critical" && "font-semibold text-warm",
    u === "soon" && "font-medium text-ink",
    u === "later" && "text-muted",
    u === "past" && "text-faint",
  );
  const date = (
    <time dateTime={at} title={formatDateTime(at, tz)} className="tabular-nums">
      {approximate && <Approx />}
      {long ? formatDateTime(at, tz) : formatShort(at, tz, now)}
    </time>
  );
  if (layout === "inline")
    return (
      <span className="inline-flex flex-wrap items-baseline gap-x-2">
        <span className="text-ink">{date}</span>
        <span className={cx("text-xs", relCls)}>{rel}</span>
      </span>
    );
  return (
    <span className="flex flex-col">
      <span className={cx("text-sm", relCls)}>{rel}</span>
      <span className="text-xs text-muted">{date}</span>
    </span>
  );
}

export function UrgencyDot({ at, now }: { at: string | null | undefined; now: Date }) {
  const u = urgency(at, now);
  if (u !== "critical" && u !== "soon") return null;
  return (
    <span
      aria-hidden
      className={cx("inline-block h-1.5 w-1.5 shrink-0 rounded-full", u === "critical" ? "bg-warm" : "bg-accent")}
    />
  );
}

export function NextDeadline({ d, tz, now }: { d: Deadline | null; tz: string; now: Date }) {
  if (!d) return <span className="text-sm text-faint">No upcoming deadline</span>;
  return (
    <div className="min-w-0">
      <p className="truncate text-xs text-faint first-letter:uppercase">{d.label}</p>
      <DeadlineWhen at={d.at} approximate={d.approximate} tz={tz} now={now} />
    </div>
  );
}

export function useDocTitle(title: string | null | undefined) {
  useEffect(() => {
    if (title) document.title = `${title} · Logr`;
  }, [title]);
}
