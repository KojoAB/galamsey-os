import { redirect } from "next/navigation";
import MapLoader from "@/components/MapLoader";
import { isDemo } from "@/lib/supabase/env";
import { createClient } from "@/lib/supabase/server";

async function signOut() {
  "use server";
  const supabase = await createClient();
  await supabase.auth.signOut();
  redirect("/login");
}

export default async function Home() {
  let who: { email?: string; role?: string } | null = null;
  if (!isDemo) {
    const supabase = await createClient();
    const { data } = await supabase.auth.getClaims();
    if (!data?.claims) redirect("/login");
    const { data: profile } = await supabase.from("profiles").select("role").eq("user_id", data.claims.sub).single();
    who = { email: data.claims.email as string | undefined, role: profile?.role };
  }

  return (
    <div className="flex h-dvh flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-ink-700 bg-ink-950 px-4 py-2.5">
        <div className="flex items-baseline gap-3">
          <span className="text-base font-semibold tracking-tight text-white">Galamsey Watch</span>
          <span className="hidden text-xs text-ink-400 sm:inline">Free data watches the whole country. Paid data confirms.</span>
        </div>
        {who ? (
          <form action={signOut} className="flex items-center gap-3 text-sm">
            <span className="hidden text-ink-300 sm:inline">{who.email}</span>
            {who.role && <span className="rounded-md bg-ink-800 px-2 py-0.5 text-xs capitalize text-ink-200">{who.role.replace("_", " ")}</span>}
            <button className="text-ink-300 underline-offset-2 hover:text-white hover:underline">Sign out</button>
          </form>
        ) : (
          <span className="rounded-md border border-alert-amber/50 bg-alert-amber/10 px-2 py-0.5 text-xs text-alert-amber">
            Demo<span className="hidden sm:inline"> — not connected to the database</span>
          </span>
        )}
      </header>

      <main className="relative flex-1">
        <MapLoader demo={isDemo} />
      </main>

      <footer className="border-t border-ink-700 bg-ink-950 px-4 py-1.5 text-[11px] leading-snug text-ink-400">
        Data: Contains modified Copernicus Sentinel data · Global Forest Watch · geoBoundaries · WDPA (UNEP-WCMC &amp; IUCN) ·
        © OpenStreetMap contributors · HydroSHEDS · Imagery © Esri. Every alert is reviewed by a person before it is treated as fact.
      </footer>
    </div>
  );
}
