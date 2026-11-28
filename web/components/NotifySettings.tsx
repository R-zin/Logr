"use client";

import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { api, CHANNEL_PREF, CHANNELS, type Channel, type ChannelStatus, type NotifyStatus, type NotifyTestResult } from "@/lib/api";
import { useNow, useSession } from "@/lib/session";
import { formatDateTime, relative } from "@/lib/time";
import { IconAlert, IconBell, IconCheck, IconX } from "./icons";
import { ErrorState, InlineError, PageSection, SavedNote, Skeleton, cx } from "./ui";

interface ChannelMeta {
  name: string;
  inputLabel: string;
  type: "email" | "text" | "url";
  inputMode?: "email" | "text" | "url" | "numeric";
  placeholder: string;
  autoComplete: string;
  help: ReactNode;
  /** What the server is missing when `configured && !available`. */
  serverNeeds?: string;
  /** Never put the stored value back into the input (it's masked). */
  secret?: boolean;
}

const META: Record<Channel, ChannelMeta> = {
  email: {
    name: "Email",
    inputLabel: "Email address",
    type: "email",
    inputMode: "email",
    placeholder: "you@example.com",
    autoComplete: "email",
    help: "One address. Reminders come from the server's mail account.",
    serverNeeds: "Email needs the server's SMTP settings (LOGR_SMTP_HOST and friends).",
  },
  telegram: {
    name: "Telegram",
    inputLabel: "Telegram chat id",
    type: "text",
    placeholder: "123456789 or @mychannel",
    autoComplete: "off",
    help: (
      <>
        Send <span className="font-mono">/start</span> to your Logr server's bot first (ask the admin for its name), then enter your
        numeric chat id (the admin, or a bot like <span className="font-mono">@userinfobot</span>, can tell you). For a channel, add the
        bot and use <span className="font-mono">@channelname</span>.
      </>
    ),
    serverNeeds: "Telegram needs a bot token on the server (LOGR_TELEGRAM_BOT_TOKEN).",
  },
  discord: {
    name: "Discord",
    inputLabel: "Discord webhook URL",
    type: "url",
    inputMode: "url",
    placeholder: "https://discord.com/api/webhooks/…",
    autoComplete: "off",
    help: "In your Discord server: channel settings → Integrations → Webhooks → New Webhook → Copy Webhook URL. It's a secret, so Logr only ever shows the end of it.",
    secret: true,
  },
};

function StatusChip({ ch }: { ch: ChannelStatus }) {
  if (!ch.configured) return <span className="chip bg-sunken text-muted">Not set up</span>;
  if (!ch.available)
    return (
      <span className="chip bg-warm-soft text-warm">
        <IconAlert width={12} height={12} />
        Server not ready
      </span>
    );
  return (
    <span className="chip bg-good-soft text-good">
      <IconCheck width={12} height={12} />
      Set up
    </span>
  );
}

function ChannelRow({ channel, ch, isAdmin, onChanged }: { channel: Channel; ch: ChannelStatus; isAdmin: boolean; onChanged: () => Promise<void> }) {
  const meta = META[channel];
  const key = CHANNEL_PREF[channel];
  const uid = useId();
  const inputId = `${uid}-input`;
  const helpId = `${uid}-help`;
  const errId = `${uid}-err`;
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const changeRef = useRef<HTMLButtonElement>(null);
  const refocusChange = useRef(false);

  const showForm = !ch.configured || editing;

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);
  useEffect(() => {
    if (!showForm && refocusChange.current) {
      refocusChange.current = false;
      changeRef.current?.focus();
    }
  }, [showForm]);

  const flash = (msg: string) => {
    setSaved(msg);
    setTimeout(() => setSaved((s) => (s === msg ? null : s)), 2500);
  };

  const startEdit = () => {
    setValue(meta.secret ? "" : (ch.destination ?? ""));
    setError(null);
    setEditing(true);
  };
  const cancel = () => {
    setEditing(false);
    setError(null);
    refocusChange.current = true;
  };

  const save = async () => {
    const v = value.trim();
    if (!v) {
      setError(new Error(`Enter ${channel === "email" ? "an email address" : channel === "telegram" ? "a chat id" : "the webhook URL"}.`));
      inputRef.current?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.setPref(key, v);
      await onChanged();
      setEditing(false);
      setValue("");
      refocusChange.current = true;
      flash("Saved");
    } catch (e) {
      setError(e);
      inputRef.current?.focus();
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    const what = meta.secret ? "this Discord webhook" : (ch.destination ?? meta.name);
    const extra = meta.secret ? " You'll need the full webhook URL to add it again." : "";
    if (!window.confirm(`Stop sending notifications to ${what}?${extra}`)) return;
    setBusy(true);
    setError(null);
    try {
      await api.unsetPref(key);
      await onChanged();
      setEditing(false);
      flash("Removed");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-3 px-5 py-5 sm:grid-cols-[14rem_minmax(0,1fr)] sm:gap-6" aria-busy={busy}>
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-medium">{meta.name}</h3>
          <StatusChip ch={ch} />
        </div>
        <p id={helpId} className="mt-1 text-xs text-muted">
          {meta.help}
        </p>
      </div>
      <div className="min-w-0">
        {ch.configured && !ch.available && (
          <div className="mb-3 rounded-lg border border-warm/30 bg-warm-soft px-3 py-2 text-xs text-ink">
            <p className="font-medium text-warm">Set up, but the server isn't configured for this channel.</p>
            <p className="mt-0.5 text-muted">
              {meta.serverNeeds} {isAdmin ? "See" : "Ask your admin, or see"} <span className="font-mono">docs/DEPLOY.md</span>. Nothing is sent
              here until then.
            </p>
          </div>
        )}
        {showForm ? (
          <form
            noValidate
            className="flex flex-wrap items-start gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            <label htmlFor={inputId} className="sr-only">
              {meta.inputLabel}
            </label>
            <input
              ref={inputRef}
              id={inputId}
              type={meta.type}
              inputMode={meta.inputMode}
              className={cx("field min-w-0 flex-1 basis-56", meta.secret && "font-mono text-xs sm:text-sm")}
              placeholder={meta.placeholder}
              autoComplete={meta.autoComplete}
              spellCheck={false}
              autoCapitalize="off"
              value={value}
              disabled={busy}
              aria-invalid={!!error}
              aria-describedby={cx(helpId, !!error && errId) || undefined}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape" && editing) {
                  e.preventDefault();
                  cancel();
                }
              }}
            />
            <div className="flex items-center gap-2">
              <button type="submit" className="btn-primary" disabled={busy}>
                {busy ? "Saving…" : "Save"}
              </button>
              {editing && (
                <button type="button" className="btn-ghost" onClick={cancel} disabled={busy}>
                  Cancel
                </button>
              )}
              {!ch.configured && <SavedNote show={!!saved}>{saved}</SavedNote>}
            </div>
          </form>
        ) : (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <span
              className={cx("min-w-0 max-w-full break-all text-sm", meta.secret ? "font-mono text-xs text-muted sm:text-sm" : "text-ink")}
              title={meta.secret ? "Masked: the full webhook URL is never shown" : undefined}
            >
              {meta.secret && <span className="sr-only">Webhook (masked): </span>}
              {ch.destination}
            </span>
            <span className="flex items-center gap-1">
              <button ref={changeRef} type="button" className="btn-secondary btn-sm" onClick={startEdit} disabled={busy}>
                {meta.secret ? "Replace" : "Change"}
              </button>
              <button type="button" className="btn-ghost btn-sm" onClick={remove} disabled={busy}>
                Remove
              </button>
            </span>
            <SavedNote show={!!saved}>{saved}</SavedNote>
          </div>
        )}
        <div id={errId}>
          <InlineError error={error} />
        </div>
      </div>
    </div>
  );
}

const PRESETS = ["7d,1d,3h", "1d,3h", "3d,1d,1h"];
const pretty = (v: string) => v.split(",").join(", ");

function OffsetsRow({ value, onChanged }: { value: string | null; onChanged: () => Promise<void> }) {
  const [text, setText] = useState(value ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => setText(value ?? ""), [value]);

  const save = async (v: string) => {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      if (v.trim()) await api.setPref("remind_offsets", v.trim());
      else await api.unsetPref("remind_offsets");
      await onChanged();
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const norm = (s: string) => s.replace(/\s+/g, "");
  const current = norm(value ?? "");

  return (
    <div className="grid gap-2 px-5 py-5 sm:grid-cols-[14rem_minmax(0,1fr)] sm:gap-6" aria-busy={busy}>
      <div>
        <label htmlFor="pref-offsets" className="text-sm font-medium">
          Reminder times
        </label>
        <p id="pref-offsets-help" className="mt-0.5 text-xs text-muted">
          How long before each deadline to remind you. Units m, h, d, w; up to 6 times, at most 60d. When several are due at once you
          get only the nearest.
        </p>
      </div>
      <div className="min-w-0">
        <form
          noValidate
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void save(text);
          }}
        >
          <input
            id="pref-offsets"
            className="field min-w-0 max-w-xs flex-1 basis-40 font-mono"
            placeholder="7d,1d,3h"
            value={text}
            disabled={busy}
            spellCheck={false}
            autoCapitalize="off"
            autoComplete="off"
            aria-invalid={!!error}
            aria-describedby="pref-offsets-help"
            onChange={(e) => setText(e.target.value)}
          />
          <button type="submit" className="btn-secondary" disabled={busy || norm(text) === current}>
            Save
          </button>
          <SavedNote show={saved} />
        </form>
        <div className="mt-3 flex flex-wrap items-center gap-2" role="group" aria-label="Reminder presets">
          <span className="text-xs text-faint">Presets:</span>
          {PRESETS.map((p) => {
            const active = current === p;
            return (
              <button
                key={p}
                type="button"
                aria-pressed={active}
                disabled={busy}
                onClick={() => {
                  setText(p);
                  if (!active) void save(p);
                }}
                className={cx(
                  "rounded-full border px-2.5 py-1 font-mono text-xs transition-colors disabled:opacity-50",
                  active ? "border-accent/40 bg-accent-soft text-accent" : "border-line bg-surface text-muted hover:bg-sunken hover:text-ink",
                )}
              >
                {pretty(p)}
              </button>
            );
          })}
        </div>
        <InlineError error={error} />
      </div>
    </div>
  );
}

function TestRow({ status }: { status: NotifyStatus }) {
  const { tz } = useSession();
  const now = useNow(30_000);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<NotifyTestResult | null>(null);
  const anyConfigured = CHANNELS.some((c) => status.channels[c]?.configured);

  const run = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.testNotify());
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const entries = result ? Object.entries(result.results) : [];
  const label = (c: string) => (c in META ? META[c as Channel].name : c);

  return (
    <div className="flex flex-col gap-3 bg-sunken/40 px-5 py-4 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
      <div className="min-w-0 text-sm">
        <p className="text-muted">
          {status.last_pass_at ? (
            <>
              Last reminder check:{" "}
              <time dateTime={status.last_pass_at} title={formatDateTime(status.last_pass_at, tz)} className="text-ink">
                {relative(status.last_pass_at, now)}
              </time>
            </>
          ) : (
            <>
              Reminders run when the server is started with <code className="rounded bg-sunken px-1 py-0.5 font-mono text-xs text-ink">logr serve --schedule</code>.
            </>
          )}
        </p>
        <div aria-live="polite">
          {entries.length > 0 && (
            <ul className="mt-2 space-y-1">
              {entries.map(([c, r]) => {
                const ok = r === "ok";
                return (
                  <li key={c} className="flex items-start gap-2 text-sm">
                    <span
                      className={cx(
                        "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full",
                        ok ? "bg-good-soft text-good" : "bg-warm-soft text-warm",
                      )}
                    >
                      {ok ? <IconCheck width={11} height={11} strokeWidth={2.6} /> : <IconX width={11} height={11} strokeWidth={2.6} />}
                    </span>
                    <span className="min-w-0">
                      <span className="font-medium">{label(c)}</span>
                      <span className="sr-only">{ok ? ": sent" : ": failed"}</span>
                      <span className={cx("ml-1.5 break-words", ok ? "text-good" : "text-warm")}>{ok ? "Sent" : r}</span>
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        <InlineError error={error} />
      </div>
      <div className="flex shrink-0 flex-col items-start gap-1 sm:items-end">
        <button type="button" className="btn-secondary" onClick={run} disabled={busy || !anyConfigured}>
          <IconBell width={15} height={15} />
          {busy ? "Sending…" : "Send test notification"}
        </button>
        {!anyConfigured && <p className="text-xs text-faint">Set up a channel first.</p>}
      </div>
    </div>
  );
}

export function NotifySettings({ className }: { className?: string }) {
  const { me } = useSession();
  const [status, setStatus] = useState<NotifyStatus | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await api.notify());
      setError(null);
    } catch (e) {
      setError(e);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  return (
    <PageSection
      id="notify-h"
      title="Notifications"
      subtitle="Reminders before the deadlines of hackathons you track, and a note when one of their dates moves."
      className={className}
    >
      {error && !status ? (
        <ErrorState error={error} onRetry={load} />
      ) : !status ? (
        <div className="card space-y-4 p-5">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
        </div>
      ) : (
        <div className="card divide-y divide-line overflow-hidden">
          {CHANNELS.map((c) => (
            <ChannelRow
              key={c}
              channel={c}
              ch={status.channels[c] ?? { configured: false, available: true, destination: null }}
              isAdmin={!!me?.is_admin}
              onChanged={load}
            />
          ))}
          <OffsetsRow value={status.remind_offsets} onChanged={load} />
          <TestRow status={status} />
        </div>
      )}
    </PageSection>
  );
}
