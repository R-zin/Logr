"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { IconMenu, IconX } from "./icons";
import { SignInButtons, cx } from "./ui";

const LINKS = [
  { href: "/", label: "Discover" },
  { href: "/mine/", label: "My hackathons" },
  { href: "/calendar/", label: "Calendar" },
  { href: "/searches/", label: "Saved searches" },
  { href: "/settings/", label: "Settings" },
];

function isActive(pathname: string, href: string): boolean {
  const p = pathname.endsWith("/") ? pathname : `${pathname}/`;
  if (href === "/") return p === "/" || p.startsWith("/hackathon/");
  return p.startsWith(href);
}

function Logo() {
  return (
    <Link href="/" className="flex items-center gap-2 rounded-md text-ink" aria-label="Logr home">
      <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden>
        <rect x="2" y="2" width="20" height="20" rx="6" className="fill-accent" />
        <path d="M8 7v10h8" stroke="rgb(var(--accent-ink))" strokeWidth="2.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span className="text-[15px] font-semibold tracking-tight">Logr</span>
    </Link>
  );
}

function UserMenu() {
  const { me } = useSession();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!me) return <div className="skeleton h-8 w-8 rounded-full" />;
  if (me.auth_mode === "local") return null; // single-user mode: no login UI
  if (!me.user) return <SignInButtons compact />;

  const u = me.user;
  const initials = (u.name || u.login || "?").slice(0, 1).toUpperCase();
  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        className="flex items-center gap-2 rounded-full p-0.5 hover:bg-sunken"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {u.avatar_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={u.avatar_url} alt="" width={28} height={28} className="h-7 w-7 rounded-full border border-line" />
        ) : (
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-accent-soft text-xs font-semibold text-accent">
            {initials}
          </span>
        )}
        <span className="sr-only">Account menu for {u.name || u.login}</span>
      </button>
      {open && (
        <div role="menu" className="absolute right-0 z-30 mt-2 w-56 overflow-hidden rounded-xl border border-line bg-surface shadow-pop">
          <div className="border-b border-line px-4 py-3">
            <p className="truncate text-sm font-medium">{u.name || u.login}</p>
            <p className="truncate text-xs text-muted">
              @{u.login} · {u.provider}
              {me.is_admin ? " · admin" : ""}
            </p>
          </div>
          <Link role="menuitem" href="/settings/" className="block px-4 py-2 text-sm hover:bg-sunken" onClick={() => setOpen(false)}>
            Settings
          </Link>
          <button
            role="menuitem"
            type="button"
            className="block w-full px-4 py-2 text-left text-sm hover:bg-sunken"
            onClick={async () => {
              try {
                await api.logout();
              } finally {
                window.location.href = "/";
              }
            }}
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

function NavInner() {
  const pathname = usePathname() || "/";
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [pathname]);

  return (
    <header className="sticky top-0 z-20 border-b border-line bg-canvas/85 backdrop-blur supports-[backdrop-filter]:bg-canvas/70">
      <nav aria-label="Main" className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-4 sm:px-6">
        <Logo />
        <ul className="hidden items-center gap-1 md:flex">
          {LINKS.map((l) => {
            const active = isActive(pathname, l.href);
            return (
              <li key={l.href}>
                <Link
                  href={l.href}
                  aria-current={active ? "page" : undefined}
                  className={cx(
                    "rounded-md px-2.5 py-1.5 text-sm transition-colors",
                    active ? "bg-sunken font-medium text-ink" : "text-muted hover:text-ink",
                  )}
                >
                  {l.label}
                </Link>
              </li>
            );
          })}
        </ul>
        <div className="ml-auto flex items-center gap-2">
          <Suspense fallback={null}>
            <UserMenu />
          </Suspense>
          <button
            type="button"
            className="btn-ghost -mr-2 px-2 md:hidden"
            aria-expanded={open}
            aria-controls="mobile-nav"
            onClick={() => setOpen((o) => !o)}
          >
            {open ? <IconX width={20} height={20} /> : <IconMenu width={20} height={20} />}
            <span className="sr-only">{open ? "Close menu" : "Open menu"}</span>
          </button>
        </div>
      </nav>
      {open && (
        <ul id="mobile-nav" className="border-t border-line px-2 py-2 md:hidden">
          {LINKS.map((l) => {
            const active = isActive(pathname, l.href);
            return (
              <li key={l.href}>
                <Link
                  href={l.href}
                  aria-current={active ? "page" : undefined}
                  className={cx("block rounded-md px-3 py-2.5 text-sm", active ? "bg-sunken font-medium" : "text-muted")}
                >
                  {l.label}
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </header>
  );
}

export function Nav() {
  return (
    <Suspense fallback={<header className="h-14 border-b border-line" />}>
      <NavInner />
    </Suspense>
  );
}
