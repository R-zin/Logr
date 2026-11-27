import type { EventKind, Mode, RoundKind, Source, Status } from "./api";

export const STATUS_LABEL: Record<Status, string> = {
  interested: "Interested",
  registered: "Registered",
  team_formed: "Team formed",
  submitted: "Submitted",
  shortlisted: "Shortlisted",
  won: "Won",
  lost: "Lost",
  withdrawn: "Withdrawn",
};

export const SOURCE_LABEL: Record<Source, string> = {
  devpost: "Devpost",
  devfolio: "Devfolio",
  unstop: "Unstop",
  hack2skill: "Hack2skill",
  luma: "Luma",
  linkedin: "LinkedIn",
};

export const MODE_LABEL: Record<Mode, string> = {
  online: "Online",
  offline: "In person",
  hybrid: "Hybrid",
  unknown: "Mode unknown",
};

export const ROUND_KIND_LABEL: Record<RoundKind, string> = {
  registration: "Registration",
  submission: "Submission",
  judging: "Judging",
  results: "Results",
  event: "Event",
  other: "Other",
};

export const EVENT_KIND_LABEL: Record<EventKind, string> = {
  deadline: "Deadline",
  round_end: "Round ends",
  start: "Starts",
  end: "Ends",
  todo: "To-do",
};

export const DATE_FIELD_LABEL: Record<string, string> = {
  registration_opens_at: "Registration opens",
  registration_closes_at: "Registration closes",
  submission_deadline: "Submission deadline",
  starts_at: "Starts",
  ends_at: "Ends",
  rounds: "Round timeline",
};

export function sourceLabel(s: string): string {
  return SOURCE_LABEL[s as Source] ?? s;
}

export function statusLabel(s: string): string {
  return STATUS_LABEL[s as Status] ?? s.replace(/_/g, " ");
}

export function fieldLabel(f: string): string {
  return DATE_FIELD_LABEL[f] ?? f.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export function teamSize(min: number | null, max: number | null): string | null {
  if (min == null && max == null) return null;
  if (min != null && max != null) return min === max ? `${min}` : `${min}–${max}`;
  if (min != null) return `${min}+`;
  return `up to ${max}`;
}

export function capitalize(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}
