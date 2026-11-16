import Link from "next/link";
import AppShell, { Badge, Card, DemoNotice, Page } from "@/components/AppShell";
import FieldReport from "@/components/FieldReport";
import { requireViewer } from "@/lib/auth";
import { LEVEL_STYLE, fmtHa } from "@/lib/format";
import { isDemo } from "@/lib/supabase/env";
import { createClient } from "@/lib/supabase/server";

export default async function FieldPage({ searchParams }: { searchParams: Promise<{ alert?: string }> }) {
  if (isDemo) return <AppShell viewer={null} demo><Page title="Field mode"><DemoNotice /></Page></AppShell>;
  const viewer = await requireViewer();
  const sp = await searchParams;
  const supabase = await createClient();
  const { data: mine } = await supabase.from("alerts")
    .select("id, level, score, area_ha, district, region, status, centroid")
    .eq("assigned_to", viewer.id).in("status", ["confirmed", "reviewing", "new"]).order("score", { ascending: false });

  return (
    <AppShell viewer={viewer}>
      <Page title="Field mode" subtitle="Your assigned alerts. Reports are saved on the phone first and upload when there is signal.">
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title={`My alerts (${mine?.length ?? 0})`}>
            <ul className="divide-y divide-ink-800">
              {(mine ?? []).map((a) => {
                const [lon, lat] = (a.centroid?.coordinates ?? [0, 0]) as [number, number];
                return (
                  <li key={a.id} className="flex items-center justify-between gap-3 py-3">
                    <Link href={`/alerts/${a.id}`} className="min-w-0">
                      <span className="font-medium text-white">#{a.id} <Badge className={LEVEL_STYLE[a.level]}>{a.level}</Badge></span>
                      <span className="block truncate text-sm text-ink-300">{fmtHa(a.area_ha)} · {a.district ?? ""} {a.region}</span>
                    </Link>
                    <a href={`https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}`} target="_blank" rel="noopener noreferrer"
                       className="shrink-0 rounded-lg border border-ink-600 px-3 py-2 text-sm text-white hover:bg-ink-800">Directions</a>
                  </li>
                );
              })}
              {!mine?.length && <li className="py-3 text-sm text-ink-300">Nothing assigned to you right now.</li>}
            </ul>
          </Card>
          <Card title="New field report">
            <FieldReport userId={viewer.id} defaultAlert={sp.alert ? Number(sp.alert) : undefined}
              alerts={(mine ?? []).map((a) => ({ id: a.id, label: `#${a.id} · ${a.district ?? a.region ?? ""}` }))} />
          </Card>
        </div>
      </Page>
    </AppShell>
  );
}
