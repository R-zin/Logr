"use client";

import { useRef, useState } from "react";
import { api, type Feed } from "@/lib/api";
import { IconCalendar, IconCheck, IconCopy, IconRefresh } from "./icons";
import { InlineError, PageSection, cx } from "./ui";

/** Clipboard API where available (secure contexts), else select + execCommand. */
async function copyText(text: string, input: HTMLInputElement | null): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to the legacy path
  }
  try {
    if (!input) return false;
    input.focus();
    input.select();
    return document.execCommand("copy");
  } catch {
    return false;
  }
}

function Steps({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <h4 className="text-sm font-medium">{title}</h4>
      <p className="mt-1 text-xs leading-relaxed text-muted">{children}</p>
    </div>
  );
}

const Path = ({ children }: { children: React.ReactNode }) => <span className="font-medium text-ink">{children}</span>;

export function CalendarFeed({ className }: { className?: string }) {
  const [feed, setFeed] = useState<Feed | null>(null);
  const [busy, setBusy] = useState<"load" | "rotate" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [copy, setCopy] = useState<"copied" | "failed" | null>(null);
  const [rotated, setRotated] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const show = async () => {
    setBusy("load");
    setError(null);
    try {
      setFeed(await api.feed());
      // Move focus to the link once it renders.
      setTimeout(() => inputRef.current?.focus(), 0);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };

  const doCopy = async () => {
    if (!feed) return;
    const ok = await copyText(feed.url, inputRef.current);
    setCopy(ok ? "copied" : "failed");
    if (!ok) inputRef.current?.select();
    setTimeout(() => setCopy((c) => (c === (ok ? "copied" : "failed") ? null : c)), ok ? 2000 : 6000);
  };

  const rotate = async () => {
    if (
      !window.confirm(
        "Reset your calendar link?\n\nThe current link stops working right away. Every calendar app subscribed to it stops updating until you subscribe again with the new link.",
      )
    )
      return;
    setBusy("rotate");
    setError(null);
    setCopy(null);
    try {
      setFeed(await api.rotateFeed());
      setRotated(true);
      inputRef.current?.focus();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };

  return (
    <PageSection
      id="feed-h"
      title="Calendar subscription"
      subtitle="See the deadlines, rounds and to-dos of the hackathons you track in Google Calendar, Apple Calendar or Outlook."
      className={className}
    >
      <div className="card overflow-hidden">
        {!feed ? (
          <div className="flex flex-col gap-4 px-5 py-5 sm:flex-row sm:items-center sm:justify-between">
            <p className="max-w-xl text-sm text-muted">
              Your calendar link is private, like a password: anyone who has it can see your tracked hackathons. It's created the first time
              you show it.
            </p>
            <button type="button" className="btn-primary shrink-0 self-start sm:self-auto" onClick={show} disabled={busy === "load"}>
              <IconCalendar width={15} height={15} />
              {busy === "load" ? "Loading…" : "Show my calendar link"}
            </button>
          </div>
        ) : (
          <>
            <div className="px-5 py-5">
              <label htmlFor="feed-url" className="text-sm font-medium">
                Your calendar link
              </label>
              <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                <input
                  ref={inputRef}
                  id="feed-url"
                  className="field min-w-0 flex-1 font-mono text-xs sm:text-sm"
                  readOnly
                  value={feed.url}
                  spellCheck={false}
                  aria-describedby="feed-secret"
                  onFocus={(e) => e.currentTarget.select()}
                />
                <div className="flex gap-2">
                  <button type="button" className="btn-secondary flex-1 sm:flex-none" onClick={doCopy}>
                    {copy === "copied" ? <IconCheck width={15} height={15} className="text-good" /> : <IconCopy width={15} height={15} />}
                    {copy === "copied" ? "Copied" : "Copy"}
                  </button>
                  <a href={feed.webcal_url} className="btn-secondary flex-1 sm:flex-none">
                    <IconCalendar width={15} height={15} />
                    Open in Apple Calendar
                  </a>
                </div>
              </div>
              <p role="status" className={cx("mt-2 text-xs", copy === "failed" ? "text-warm" : "sr-only")}>
                {copy === "failed" ? "Couldn't copy automatically. The link is selected: press Ctrl+C (⌘C on a Mac)." : copy === "copied" ? "Link copied" : ""}
              </p>
              {rotated && (
                <p role="status" className="mt-2 text-xs text-good">
                  New link created. The old one no longer works, so subscribe again with this one.
                </p>
              )}
              <p id="feed-secret" className="mt-2 text-xs text-muted">
                Keep this link secret. Anyone with it can read your tracked hackathons, no sign-in needed. If it leaks, reset it.
              </p>
            </div>
            <div className="grid gap-4 border-t border-line px-5 py-5 sm:grid-cols-3 sm:gap-6">
              <Steps title="Google Calendar">
                On calendar.google.com, go to <Path>Other calendars → + → From URL</Path> and paste the link. Google refreshes subscriptions
                on its own schedule, every few hours, so changes show up there with a delay.
              </Steps>
              <Steps title="Apple Calendar">
                Use <Path>Open in Apple Calendar</Path>, or <Path>File → New Calendar Subscription</Path> and paste the link.
              </Steps>
              <Steps title="Outlook">
                On outlook.com, <Path>Add calendar → Subscribe from web</Path> and paste the link. In desktop Outlook, use{" "}
                <Path>Add calendar → From internet</Path>.
              </Steps>
            </div>
            <div className="flex flex-col gap-3 border-t border-line bg-sunken/40 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-xs text-muted">Resetting makes a new link and stops the old one from working.</p>
              <button type="button" className="btn-secondary self-start text-warm sm:self-auto" onClick={rotate} disabled={busy === "rotate"}>
                <IconRefresh width={15} height={15} className={cx(busy === "rotate" && "animate-spin")} />
                {busy === "rotate" ? "Resetting…" : "Reset link"}
              </button>
            </div>
          </>
        )}
        {error ? (
          <div className="px-5 pb-4">
            <InlineError error={error} />
          </div>
        ) : null}
      </div>
    </PageSection>
  );
}
