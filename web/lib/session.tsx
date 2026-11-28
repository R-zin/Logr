"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError, type Me, type Prefs } from "./api";
import { browserTz } from "./time";

interface SessionValue {
  /** null until /api/me has answered. */
  me: Me | null;
  meError: ApiError | null;
  loading: boolean;
  signedIn: boolean;
  prefs: Prefs | null;
  /** Display timezone: the `tz` pref when signed in, else the browser's. */
  tz: string;
  refresh: () => Promise<void>;
  setPrefs: (p: Prefs) => void;
}

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [meError, setMeError] = useState<ApiError | null>(null);
  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const [loading, setLoading] = useState(true);
  const [fallbackTz, setFallbackTz] = useState("UTC");

  const refresh = useCallback(async () => {
    try {
      const m = await api.me();
      setMe(m);
      setMeError(null);
      if (m.user) {
        try {
          setPrefs(await api.prefs());
        } catch {
          setPrefs(null);
        }
      } else {
        setPrefs(null);
      }
    } catch (e) {
      setMeError(e instanceof ApiError ? e : new ApiError(0, String(e)));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setFallbackTz(browserTz());
    void refresh();
  }, [refresh]);

  const value = useMemo<SessionValue>(
    () => ({
      me,
      meError,
      loading,
      signedIn: !!me?.user,
      prefs,
      tz: prefs?.tz?.value || fallbackTz,
      refresh,
      setPrefs,
    }),
    [me, meError, loading, prefs, fallbackTz, refresh],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const v = useContext(SessionContext);
  if (!v) throw new Error("useSession outside SessionProvider");
  return v;
}

/** Re-renders every `ms` so relative times stay fresh. */
export function useNow(ms = 60_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}
