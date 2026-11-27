import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { Nav } from "@/components/Nav";
import { SessionProvider } from "@/lib/session";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Logr", template: "%s · Logr" },
  description: "Find hackathons across Devpost, Devfolio, Unstop, Hack2skill and Luma, and never miss a deadline.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fafaf9" },
    { media: "(prefers-color-scheme: dark)", color: "#0d0d0f" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen overflow-x-hidden">
        <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-3 focus:z-50 focus:rounded-md focus:bg-surface focus:px-3 focus:py-2 focus:text-sm focus:shadow-pop">
          Skip to content
        </a>
        <SessionProvider>
          <Nav />
          <main id="main" className="mx-auto w-full max-w-7xl px-4 pb-24 pt-6 sm:px-6 sm:pt-10">
            {children}
          </main>
        </SessionProvider>
      </body>
    </html>
  );
}
