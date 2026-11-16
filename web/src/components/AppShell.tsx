import Link from "next/link";
import { redirect } from "next/navigation";
import type { Viewer } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";

async function signOut() {
  "use server";
  const supabase = await createClient();
  await supabase.auth.signOut();
  redirect("/login");
}

const NAV = [
  { href: "/", label: "Map", roles: ["admin", "analyst", "field_scout"] },
  { href: "/alerts", label: "Alerts", roles: ["admin", "analyst", "field_scout"] },
  { href: "/field", label: "Field", roles: ["admin", "analyst", "field_scout"] },
  { href: "/labels", label: "Labels", roles: ["admin", "analyst"] },
  { href: "/stats", label: "Stats", roles: ["admin", "analyst"] },
  { href: "/admin", label: "Admin", roles: ["admin"] },
];

export const ATTRIBUTION =
  "Contains modified Copernicus Sentinel data · Global Forest Watch · geoBoundaries · WDPA (UNEP-WCMC & IUCN) · © OpenStreetMap contributors · HydroSHEDS · Imagery © Esri · Planet/NICFI where used. Every alert is reviewed by a person before it is treated as fact.";

export default function AppShell({ viewer, demo, children, fullBleed = false }: {
  viewer: Viewer | null; demo?: boolean; children: React.ReactNode; fullBleed?: boolean;
}) {
  const links = NAV.filter((n) => !viewer || n.roles.includes(viewer.role));
  return (
    <div className="flex h-dvh flex-col">
      <header className="flex items-center gap-3 border-b border-ink-700 bg-ink-950 px-3 py-2 sm:px-4">
        <Link href="/" className="shrink-0 text-base font-semibold tracking-tight text-white">Galamsey Watch</Link>
        <nav aria-label="Main" className="-mx-1 flex min-w-0 flex-1 gap-0.5 overflow-x-auto text-sm">
          {(viewer || demo ? links : []).map((n) => (
            <Link key={n.href} href={n.href}
              className="rounded-md px-2.5 py-1.5 text-ink-300 hover:bg-ink-800 hover:text-white focus-visible:outline-2 focus-visible:outline-brand-400">
              {n.label}
            </Link>
          ))}
        </nav>
        {viewer ? (
          <form action={signOut} className="flex shrink-0 items-center gap-2 text-sm">
            <span className="hidden rounded-md bg-ink-800 px-2 py-0.5 text-xs capitalize text-ink-200 md:inline">{viewer.role.replace("_", " ")}</span>
            <button className="text-ink-300 hover:text-white hover:underline">Sign out</button>
          </form>
        ) : demo ? (
          <span className="shrink-0 rounded-md border border-alert-amber/50 bg-alert-amber/10 px-2 py-0.5 text-xs text-alert-amber">
            Demo<span className="hidden sm:inline"> — not connected</span>
          </span>
        ) : null}
      </header>
      <main className={fullBleed ? "relative flex-1" : "flex-1 overflow-y-auto"}>{children}</main>
      <footer className="border-t border-ink-700 bg-ink-950 px-4 py-1.5 text-[11px] leading-snug text-ink-400">{ATTRIBUTION}</footer>
    </div>
  );
}

export function Page({ title, subtitle, actions, children }: {
  title: string; subtitle?: string; actions?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-white">{title}</h1>
          {subtitle && <p className="mt-1 text-sm text-ink-300">{subtitle}</p>}
        </div>
        {actions}
      </div>
      {children}
    </div>
  );
}

export function Badge({ className = "", children }: { className?: string; children: React.ReactNode }) {
  return <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${className}`}>{children}</span>;
}

export function Card({ title, children, className = "" }: { title?: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-ink-700 bg-ink-900 p-4 ${className}`}>
      {title && <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-ink-300">{title}</h2>}
      {children}
    </section>
  );
}

export function DemoNotice() {
  return (
    <div className="rounded-xl border border-alert-amber/40 bg-alert-amber/10 p-4 text-sm text-alert-amber">
      Demo mode: this page needs the Supabase database. Add <code>NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
      <code>NEXT_PUBLIC_SUPABASE_ANON_KEY</code> to <code>web/.env.local</code>.
    </div>
  );
}
