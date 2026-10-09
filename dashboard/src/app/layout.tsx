import type { Metadata } from "next";
import { cookies } from "next/headers";
import Link from "next/link";
import { Geist, Geist_Mono, IBM_Plex_Sans_Arabic, Newsreader } from "next/font/google";

import "./globals.css";
import { NavLink, OperatorMenu, ThemeToggle } from "@/components/controls";
import { ScrollToTop } from "@/components/scroll-to-top";
import { cn } from "@/components/ui";
import { api } from "@/lib/api";

// Self-hosted at build time: no request to Google when the page loads.
const newsreader = Newsreader({ subsets: ["latin"], axes: ["opsz"], variable: "--font-newsreader" });
const geist = Geist({ subsets: ["latin"], variable: "--font-geist" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono" });
const plexArabic = IBM_Plex_Sans_Arabic({ subsets: ["arabic"], weight: ["400", "500"], variable: "--font-plex-arabic" });

export const metadata: Metadata = {
  title: { default: "RevenueOps", template: "%s · RevenueOps" },
  description: "Finds revenue at risk in the store, and recovers it safely.",
};

async function header() {
  const [healthy, waiting] = await Promise.all([
    api.healthy(),
    api.executions({ status: ["pending_approval", "awaiting_human"], limit: 200 }).then(
      (w) => w.length,
      () => null,
    ),
  ]);
  return { healthy, waiting };
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const jar = await cookies();
  const theme = jar.get("theme")?.value;
  const initialTheme = theme === "light" || theme === "dark" ? theme : "system";
  const operator = jar.get("operator")?.value ?? null;
  const { healthy, waiting } = await header();

  return (
    <html
      lang="en"
      data-theme={initialTheme === "system" ? undefined : initialTheme}
      className={cn(newsreader.variable, geist.variable, geistMono.variable, plexArabic.variable)}
      suppressHydrationWarning
    >
      <body className="min-h-dvh">
        <ScrollToTop />
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:start-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-card focus:px-3 focus:py-2"
        >
          Skip to content
        </a>
        <header className="border-b border-line-2">
          <div className="mx-auto flex max-w-[1360px] flex-wrap items-center gap-x-10 px-4 sm:px-8">
            <Link href="/" className="flex h-14 items-center gap-2.5">
              <span className="serif grid size-7 place-items-center rounded-[7px] bg-primary text-[18px] font-medium text-primary-ink">
                R
              </span>
              <span className="text-[15px] font-medium">RevenueOps</span>
            </Link>
            <nav aria-label="Main" className="order-last flex max-w-full flex-[1_1_100%] gap-7 overflow-x-auto md:order-none md:flex-[1_1_auto]">
              <NavLink href="/">Overview</NavLink>
              <NavLink href="/cases">Cases</NavLink>
              <NavLink href="/approvals" badge={waiting ?? undefined}>
                Approvals
              </NavLink>
              <NavLink href="/activity">Activity</NavLink>
              <NavLink href="/outbox">Outbox</NavLink>
              <NavLink href="/policies">Policies</NavLink>
            </nav>
            <div className="ms-auto flex h-14 items-center gap-3 md:ms-0">
              <span className="hidden items-center gap-2 text-[13px] text-muted sm:inline-flex">
                <span
                  className="size-1.5 rounded-full"
                  style={{ background: healthy ? "var(--good-mark)" : "var(--bad)" }}
                  aria-hidden
                />
                {healthy ? "Agents online" : "Agent service offline"}
              </span>
              <ThemeToggle initial={initialTheme} />
              <OperatorMenu name={operator} />
            </div>
          </div>
        </header>
        <main id="main" className="mx-auto flex max-w-[1360px] flex-col gap-5 px-4 pt-10 pb-24 sm:px-8 sm:pt-14">
          {children}
        </main>
      </body>
    </html>
  );
}
