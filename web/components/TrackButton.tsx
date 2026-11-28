"use client";

import Link from "next/link";
import { useState } from "react";
import { api, ApiError, type Status } from "@/lib/api";
import { useSession } from "@/lib/session";
import { IconStar } from "./icons";
import { StatusChip, cx, useCurrentPath } from "./ui";

/** ★ Track for untracked hackathons; the status chip once tracked. */
export function TrackButton({
  uid,
  status,
  onTracked,
  size = "sm",
}: {
  uid: string;
  status: Status | null;
  onTracked?: (s: Status) => void;
  size?: "sm" | "md";
}) {
  const { me, signedIn } = useSession();
  const next = useCurrentPath();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (status)
    return (
      <Link
        href={`/hackathon/?uid=${encodeURIComponent(uid)}`}
        className="inline-flex items-center gap-1.5 rounded-full"
        title="Tracked: open the tracking panel"
      >
        <IconStar filled className="text-accent" width={14} height={14} />
        <StatusChip status={status} />
      </Link>
    );

  const cls = cx("btn-secondary", size === "sm" && "btn-sm");

  if (me && !signedIn && me.auth_mode === "oauth") {
    const provider = me.providers[0];
    if (!provider) return null;
    return (
      <a href={api.loginUrl(provider, next)} className={cls} title="Sign in to track hackathons">
        <IconStar width={14} height={14} />
        Track
      </a>
    );
  }

  return (
    <span className="inline-flex flex-col items-end">
      <button
        type="button"
        className={cls}
        disabled={busy || !me}
        onClick={async () => {
          setBusy(true);
          setErr(null);
          try {
            const t = await api.track(uid);
            onTracked?.(t.status);
          } catch (e) {
            setErr(e instanceof ApiError ? e.detail : "Couldn't track");
          } finally {
            setBusy(false);
          }
        }}
      >
        <IconStar width={14} height={14} />
        {busy ? "Tracking…" : "Track"}
      </button>
      {err && (
        <span role="alert" className="mt-1 text-[11px] text-warm">
          {err}
        </span>
      )}
    </span>
  );
}
