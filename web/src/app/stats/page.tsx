import AppShell, { Card, DemoNotice, Page } from "@/components/AppShell";
import { Accuracy, HectaresByRegion, ResponseTime } from "@/components/StatsCharts";
import { requireViewer } from "@/lib/auth";
import { fmtDate } from "@/lib/format";
import { isDemo } from "@/lib/supabase/env";
import { createClient } from "@/lib/supabase/server";

const weeksAgo = (n: number) => new Date(Date.now() - n * 7 * 86_400_000).toISOString().slice(0, 10);

export default async function StatsPage() {
  if (isDemo) return <AppShell viewer={null} demo><Page title="Stats"><DemoNotice /></Page></AppShell>;
  const viewer = await requireViewer(["admin", "analyst"]);
  const supabase = await createClient();
  const since = weeksAgo(26);
  const [{ data: ha }, { data: acc }, { data: rivers }, { data: rt }] = await Promise.all([
    supabase.from("stats_weekly_hectares").select("*").gte("week", since).order("week"),
    supabase.from("stats_accuracy").select("*").order("month"),
    supabase.from("stats_rivers").select("*").order("hectares", { ascending: false }).limit(15),
    supabase.from("stats_response_time").select("*").gte("week", since).order("week"),
  ]);
  const totalHa = (ha ?? []).reduce((s, r) => s + Number(r.hectares), 0);
  const conf = (acc ?? []).reduce((s, r) => s + r.confirmed, 0);
  const rej = (acc ?? []).reduce((s, r) => s + r.rejected, 0);
  const lastRt = rt?.at(-1)?.median_hours;

  const tile = (label: string, value: string, note: string) => (
    <div className="rounded-xl border border-ink-700 bg-ink-900 p-4">
      <p className="text-xs uppercase tracking-wider text-ink-400">{label}</p>
      <p className="mt-1 text-3xl font-semibold tabular-nums text-white">{value}</p>
      <p className="mt-1 text-xs text-ink-400">{note}</p>
    </div>
  );

  return (
    <AppShell viewer={viewer}>
      <Page title="Stats" subtitle="Last 26 weeks. Rejected alerts are excluded from hectares. Counts follow your access rights.">
        <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
          {tile("New hectares", Math.round(totalHa).toLocaleString(), "Sum of alert areas, not rejected")}
          {tile("Rivers affected", String(rivers?.length ?? 0), "Confirmed sites ≤ 500 m from a major river")}
          {tile("Alert precision", conf + rej ? `${Math.round((100 * conf) / (conf + rej))}%` : "—", `${conf} confirmed / ${rej} rejected (all time)`)}
          {tile("Response time", lastRt != null ? `${lastRt} h` : "—", "Median, alert → confirm/reject, latest week")}
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="New hectares per week, by region"><HectaresByRegion rows={ha ?? []} /></Card>
          <Card title="Review outcomes per month"><Accuracy rows={acc ?? []} /></Card>
          <Card title="Response time"><ResponseTime rows={rt ?? []} /></Card>
          <Card title="Rivers affected (confirmed sites)">
            <table className="w-full text-sm">
              <thead className="text-left text-ink-400"><tr><th className="py-1 font-medium">River</th><th className="font-medium">Sites</th><th className="font-medium">Hectares</th><th className="font-medium">Latest</th></tr></thead>
              <tbody>{(rivers ?? []).map((r) => (
                <tr key={r.river_name} className="border-t border-ink-800"><td className="py-1.5 text-white">{r.river_name}</td><td>{r.alerts}</td><td className="tabular-nums">{r.hectares}</td><td>{fmtDate(r.latest)}</td></tr>
              ))}</tbody>
            </table>
            {!rivers?.length && <p className="text-sm text-ink-300">No confirmed river sites yet.</p>}
          </Card>
        </div>
      </Page>
    </AppShell>
  );
}
