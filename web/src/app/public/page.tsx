import type { Metadata } from "next";
import Link from "next/link";
import { PublicMapLoader } from "@/components/MapLoader";
import { ATTRIBUTION } from "@/components/AppShell";
import { isConfigured } from "@/lib/supabase/env";

export const metadata: Metadata = {
  title: "Galamsey Watch — confirmed illegal mining sites in Ghana",
  description: "Public map of illegal small-scale mining sites confirmed by Galamsey Watch analysts from satellite imagery.",
  robots: { index: true, follow: true },
};

export default function PublicPage() {
  return (
    <div className="flex h-dvh flex-col">
      <header className="flex items-center justify-between border-b border-ink-700 bg-ink-950 px-4 py-2.5">
        <span className="font-semibold text-white">Galamsey Watch</span>
        <Link href="/login" className="text-sm text-ink-300 hover:text-white">Staff sign in</Link>
      </header>
      <main className="relative flex-1"><PublicMapLoader demo={!isConfigured} /></main>
      <footer className="border-t border-ink-700 bg-ink-950 px-4 py-1.5 text-[11px] text-ink-400">{ATTRIBUTION}</footer>
    </div>
  );
}
