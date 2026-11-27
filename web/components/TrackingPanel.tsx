"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { api, STATUSES, type Status, type TrackedOut } from "@/lib/api";
import { STATUS_LABEL, statusLabel } from "@/lib/labels";
import { endOfDayIso, formatDateTime, formatShort, relative, urgency } from "@/lib/time";
import { IconPlus, IconTrash, IconX } from "./icons";
import { Approx, InlineError, SectionTitle, StatusChip, cx } from "./ui";

type Mutate = (fn: () => Promise<TrackedOut>) => Promise<boolean>;

function Block({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="border-t border-line px-5 py-5 first:border-t-0">
      <SectionTitle aside={aside}>{title}</SectionTitle>
      {children}
    </section>
  );
}

/** "[2026-11-20 09:12 UTC] text" lines → entries. */
function parseNotes(notes: string): Array<{ at: string | null; text: string }> {
  return notes
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => {
      const m = /^\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) UTC\]\s*(.*)$/.exec(l);
      return m ? { at: `${m[1]}T${m[2]}:00Z`, text: m[3] } : { at: null, text: l };
    });
}

export function TrackingPanel({
  t,
  tz,
  now,
  onChange,
  onUntracked,
}: {
  t: TrackedOut;
  tz: string;
  now: Date;
  onChange: (t: TrackedOut) => void;
  onUntracked: () => void;
}) {
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [statusNote, setStatusNote] = useState("");
  const [note, setNote] = useState("");
  const [mate, setMate] = useState("");
  const [repo, setRepo] = useState(t.repo_url ?? "");
  const [todo, setTodo] = useState("");
  const [due, setDue] = useState("");

  const mutate: Mutate = async (fn) => {
    setBusy(true);
    setErr(null);
    try {
      onChange(await fn());
      return true;
    } catch (e) {
      setErr(e);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const submit = (fn: () => Promise<boolean | void>) => async (e: FormEvent) => {
    e.preventDefault();
    await fn();
  };

  const notes = parseNotes(t.notes || "");
  const open = t.checklist.filter((c) => !c.done).length;

  return (
    <div className="card overflow-hidden" aria-busy={busy}>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line bg-sunken/60 px-5 py-4">
        <div>
          <h2 className="text-sm font-semibold">Your tracking</h2>
          <p className="text-xs text-muted">Tracked since {formatShort(t.added_at, tz, now)}</p>
        </div>
        <button
          type="button"
          className="btn-ghost btn-sm text-muted hover:text-warm"
          onClick={async () => {
            if (!window.confirm("Stop tracking? Notes, team and checklist for this hackathon are deleted.")) return;
            setBusy(true);
            try {
              await api.untrack(t.uid);
              onUntracked();
            } catch (e) {
              setErr(e);
            } finally {
              setBusy(false);
            }
          }}
        >
          Untrack
        </button>
      </div>

      {err ? (
        <div className="px-5 pt-4">
          <InlineError error={err} />
        </div>
      ) : null}

      <Block title="Status">
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-[10rem] flex-1">
            <label htmlFor="tp-status" className="label">
              Current status
            </label>
            <select
              id="tp-status"
              className="field"
              value={t.status}
              disabled={busy}
              onChange={async (e) => {
                const status = e.target.value as Status;
                const ok = await mutate(() => api.updateTracked(t.uid, { status, status_note: statusNote.trim() || undefined }));
                if (ok) setStatusNote("");
              }}
            >
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABEL[s]}
                </option>
              ))}
            </select>
          </div>
          <div className="min-w-[10rem] flex-[2]">
            <label htmlFor="tp-status-note" className="label">
              Note for the next change <span className="font-normal text-faint">(optional)</span>
            </label>
            <input
              id="tp-status-note"
              className="field"
              value={statusNote}
              placeholder="e.g. team of 4"
              onChange={(e) => setStatusNote(e.target.value)}
            />
          </div>
        </div>
        {t.next_action && (
          <p className="mt-3 text-sm">
            <span className="text-muted">Next: </span>
            <span className="font-medium">{t.next_action.label}</span>{" "}
            <span className="text-muted">
              {t.next_action.approximate && <Approx />}
              {formatShort(t.next_action.at, tz, now)} ·{" "}
              <span className={cx(urgency(t.next_action.at, now) === "critical" && "font-semibold text-warm")}>
                {relative(t.next_action.at, now)}
              </span>
            </span>
          </p>
        )}
      </Block>

      <Block title="Checklist" aside={t.checklist.length ? <span className="text-xs text-faint">{open} open</span> : null}>
        {t.checklist.length > 0 && (
          <ul className="mb-3 space-y-1">
            {t.checklist.map((c) => {
              const u = c.due_at && !c.done ? urgency(c.due_at, now) : "later";
              return (
                <li key={c.id} className="group flex items-start gap-3 rounded-lg px-2 py-1.5 hover:bg-sunken/60">
                  <input
                    id={`todo-${c.id}`}
                    type="checkbox"
                    className="mt-0.5 h-4 w-4 shrink-0 accent-[rgb(var(--accent))]"
                    checked={c.done}
                    disabled={busy}
                    onChange={(e) => mutate(() => api.setTodoDone(t.uid, c.id, e.target.checked))}
                  />
                  <label htmlFor={`todo-${c.id}`} className="min-w-0 flex-1 cursor-pointer text-sm">
                    <span className={cx("block break-words", c.done && "text-faint line-through")}>{c.text}</span>
                    {c.due_at && (
                      <span
                        className={cx(
                          "mt-0.5 block text-xs",
                          c.done ? "text-faint" : u === "critical" || u === "past" ? "font-medium text-warm" : u === "soon" ? "text-ink" : "text-muted",
                        )}
                      >
                        due {formatShort(c.due_at, tz, now)}
                        {!c.done && ` · ${relative(c.due_at, now)}`}
                      </span>
                    )}
                  </label>
                  <button
                    type="button"
                    className="rounded p-1 text-faint opacity-60 hover:bg-surface hover:text-warm group-hover:opacity-100 focus-visible:opacity-100"
                    onClick={() => mutate(() => api.deleteTodo(t.uid, c.id))}
                  >
                    <IconTrash width={14} height={14} />
                    <span className="sr-only">Delete “{c.text}”</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        <form
          className="flex flex-wrap gap-2"
          onSubmit={submit(async () => {
            if (!todo.trim()) return;
            const dueIso = due ? endOfDayIso(due, tz) : null;
            if (await mutate(() => api.addTodo(t.uid, todo.trim(), dueIso))) {
              setTodo("");
              setDue("");
            }
          })}
        >
          <label htmlFor="tp-todo" className="sr-only">
            New to-do
          </label>
          <input id="tp-todo" className="field min-w-[12rem] flex-[3]" placeholder="Add a to-do" value={todo} onChange={(e) => setTodo(e.target.value)} />
          <label htmlFor="tp-due" className="sr-only">
            Due date (23:59 in {tz})
          </label>
          <input
            id="tp-due"
            type="date"
            className="field w-auto min-w-[9rem] flex-1"
            value={due}
            onChange={(e) => setDue(e.target.value)}
            title={`Due at 23:59 ${tz}`}
          />
          <button type="submit" className="btn-secondary" disabled={busy || !todo.trim()}>
            <IconPlus width={14} height={14} /> Add
          </button>
        </form>
      </Block>

      <Block title="Team">
        <ul className="mb-3 flex flex-wrap gap-1.5">
          {t.team.length === 0 && <li className="text-sm text-faint">Just you so far.</li>}
          {t.team.map((name) => (
            <li key={name} className="inline-flex items-center gap-1 rounded-full border border-line bg-sunken py-0.5 pl-2.5 pr-1 text-sm">
              {name}
              <button
                type="button"
                className="rounded-full p-0.5 text-faint hover:bg-surface hover:text-warm"
                onClick={() => mutate(() => api.removeTeammate(t.uid, name))}
              >
                <IconX width={12} height={12} />
                <span className="sr-only">Remove {name}</span>
              </button>
            </li>
          ))}
        </ul>
        <form
          className="flex gap-2"
          onSubmit={submit(async () => {
            if (mate.trim() && (await mutate(() => api.addTeammate(t.uid, mate.trim())))) setMate("");
          })}
        >
          <label htmlFor="tp-mate" className="sr-only">
            Teammate name
          </label>
          <input id="tp-mate" className="field" placeholder="Add a teammate" value={mate} onChange={(e) => setMate(e.target.value)} />
          <button type="submit" className="btn-secondary" disabled={busy || !mate.trim()}>
            Add
          </button>
        </form>
      </Block>

      <Block title="Project repo">
        <form
          className="flex flex-wrap gap-2"
          onSubmit={submit(() => mutate(() => api.updateTracked(t.uid, { repo_url: repo.trim() || null })))}
        >
          <label htmlFor="tp-repo" className="sr-only">
            Repository URL
          </label>
          <input
            id="tp-repo"
            type="url"
            className="field min-w-0 flex-1"
            placeholder="https://github.com/you/project"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
          />
          <button type="submit" className="btn-secondary" disabled={busy || repo.trim() === (t.repo_url ?? "")}>
            Save
          </button>
          {t.repo_url && (
            <button
              type="button"
              className="btn-ghost"
              onClick={async () => {
                if (await mutate(() => api.updateTracked(t.uid, { repo_url: null }))) setRepo("");
              }}
            >
              Clear
            </button>
          )}
        </form>
        {t.repo_url && (
          <a href={t.repo_url} target="_blank" rel="noreferrer" className="link mt-2 inline-block break-all text-sm">
            {t.repo_url}
          </a>
        )}
      </Block>

      <Block title="Notes">
        {notes.length > 0 && (
          <ol className="mb-3 space-y-2.5">
            {notes.map((n, i) => (
              <li key={i} className="text-sm">
                {n.at && (
                  <time dateTime={n.at} className="block text-xs text-faint">
                    {formatDateTime(n.at, tz)}
                  </time>
                )}
                <p className="whitespace-pre-wrap break-words">{n.text}</p>
              </li>
            ))}
          </ol>
        )}
        <form
          className="space-y-2"
          onSubmit={submit(async () => {
            if (note.trim() && (await mutate(() => api.addNote(t.uid, note.trim())))) setNote("");
          })}
        >
          <label htmlFor="tp-note" className="sr-only">
            New note
          </label>
          <textarea
            id="tp-note"
            rows={2}
            className="field resize-y"
            placeholder="Add a note (timestamped)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <button type="submit" className="btn-secondary btn-sm" disabled={busy || !note.trim()}>
            Add note
          </button>
        </form>
      </Block>

      <Block title="Status history">
        <ol className="relative space-y-3 border-l border-line pl-4">
          {[...t.history].reverse().map((h, i) => (
            <li key={i} className="relative text-sm">
              <span aria-hidden className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full border-2 border-surface bg-line ring-1 ring-line" />
              <div className="flex flex-wrap items-center gap-1.5">
                {h.old ? (
                  <>
                    <span className="text-muted">{statusLabel(h.old)}</span>
                    <span aria-label="to" className="text-faint">
                      →
                    </span>
                  </>
                ) : (
                  <span className="text-muted">Started tracking as</span>
                )}
                <StatusChip status={h.new} />
              </div>
              <time dateTime={h.changed_at} className="text-xs text-faint">
                {formatDateTime(h.changed_at, tz)}
              </time>
              {h.note && <p className="mt-0.5 text-muted">“{h.note}”</p>}
            </li>
          ))}
        </ol>
      </Block>
    </div>
  );
}
