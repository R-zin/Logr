"use client";

import Link from "next/link";
import type { ListingOut, Status } from "@/lib/api";
import { MODE_LABEL, teamSize } from "@/lib/labels";
import { IconGlobe, IconPin, IconTrophy, IconUsers } from "./icons";
import { TrackButton } from "./TrackButton";
import { NextDeadline, SourceBadges, Skeleton, UrgencyDot } from "./ui";

export function HackathonRow({
  h,
  tz,
  now,
  onTracked,
}: {
  h: ListingOut;
  tz: string;
  now: Date;
  onTracked: (uid: string, s: Status) => void;
}) {
  const href = `/hackathon/?uid=${encodeURIComponent(h.uid)}`;
  const team = teamSize(h.team_size_min, h.team_size_max);
  return (
    <li className="group relative flex flex-col gap-3 px-4 py-4 transition-colors hover:bg-sunken/50 sm:flex-row sm:items-center sm:gap-6 sm:px-5">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <UrgencyDot at={h.next_deadline?.at} now={now} />
          <h3 className="min-w-0 truncate text-[15px] font-semibold leading-snug text-ink">
            <Link href={href} className="rounded-sm after:absolute after:inset-0 after:content-[''] focus-visible:outline-none group-has-[a:focus-visible]:underline">
              {h.title}
            </Link>
          </h3>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
          <SourceBadges sources={h.sources.length ? h.sources : [h.source]} />
          {h.organizer && <span className="truncate">by {h.organizer}</span>}
        </div>
        <dl className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
          <div className="flex items-center gap-1">
            <dt className="sr-only">Mode and location</dt>
            {h.mode === "online" ? <IconGlobe width={13} height={13} /> : <IconPin width={13} height={13} />}
            <dd className="max-w-[16rem] truncate">
              {MODE_LABEL[h.mode]}
              {h.location ? ` · ${h.location}` : ""}
            </dd>
          </div>
          {h.prize && (
            <div className="flex items-center gap-1">
              <dt className="sr-only">Prize</dt>
              <IconTrophy width={13} height={13} />
              <dd className="font-medium text-ink">
                {h.prize}
                {h.prize_usd != null && !h.prize.startsWith("$") && (
                  <span className="ml-1 font-normal text-faint">≈ ${Math.round(h.prize_usd).toLocaleString("en-US")}</span>
                )}
              </dd>
            </div>
          )}
          {team && (
            <div className="flex items-center gap-1">
              <dt className="sr-only">Team size</dt>
              <IconUsers width={13} height={13} />
              <dd>{team}</dd>
            </div>
          )}
          {h.themes.length > 0 && (
            <div className="hidden items-center gap-1 md:flex">
              <dt className="sr-only">Themes</dt>
              <dd className="truncate">{h.themes.slice(0, 3).join(" · ")}</dd>
            </div>
          )}
        </dl>
      </div>
      <div className="flex items-end justify-between gap-4 sm:w-[17rem] sm:shrink-0 sm:items-center">
        <div className="min-w-0 flex-1">
          <NextDeadline d={h.next_deadline} tz={tz} now={now} />
        </div>
        <div className="relative z-10 shrink-0">
          <TrackButton uid={h.uid} status={h.tracked_status} onTracked={(s) => onTracked(h.uid, s)} />
        </div>
      </div>
    </li>
  );
}

export function HackathonRowSkeleton() {
  return (
    <li className="flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:gap-6 sm:px-5" aria-hidden>
      <div className="flex-1 space-y-2">
        <Skeleton className="h-4 w-2/3 max-w-sm" />
        <Skeleton className="h-3 w-40" />
        <Skeleton className="h-3 w-1/2 max-w-xs" />
      </div>
      <div className="flex items-center justify-between gap-4 sm:w-[17rem]">
        <div className="space-y-1.5">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-4 w-16" />
        </div>
        <Skeleton className="h-7 w-16 rounded-lg" />
      </div>
    </li>
  );
}
