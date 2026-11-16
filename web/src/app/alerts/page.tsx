import Link from "next/link";
import AppShell, { Badge, DemoNotice, Page } from "@/components/AppShell";
import { isStaff, requireViewer } from "@/lib/auth";
import { LEVEL_STYLE, STATUS_LABEL, fmtDate, fmtHa } from "@/lib/format";
import { isDemo } from "@/lib/supabase/env";
import { createClient } from "@/lib/supabase/server";
import { assignAlerts } from "./actions";

const SORTS = { score: "score", area: "area_ha", date: "last_seen", region: "region", status: "status" } as const;
type SortKey = keyof typeof SORTS;

export default async function AlertsPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  if (isDemo) return <AppShell viewer={null} demo><Page title="Alerts"><DemoNotice /></Page></AppShell>;
  const viewer = await requireViewer();
  const sp = await searchParams;
  const sort: SortKey = (sp.sort as SortKey) in SORTS ? (sp.sort as SortKey) : "score";
  const asc = sp.dir === "asc";
  const page = Math.max(1, Number(sp.page) || 1);
  const PER = 50;

  const supabase = await createClient();
  let q = supabase.from("alerts")
    .select("id, level, score, status, area_ha, region, district, last_seen, first_seen, sources, in_forest_reserve, river_distance_m, assigned_to", { count: "exact" })
    .order(SORTS[sort], { ascending: asc }).range((page - 1) * PER, page * PER - 1);
  if (sp.level) q = q.eq("level", sp.level);
  if (sp.status) q = q.eq("status", sp.status); else q = q.not("status", "in", "(rejected,closed)");
  if (sp.region) q = q.eq("region", sp.region);
  if (sp.mine === "1") q = q.eq("assigned_to", viewer.id);
  const { data: alerts, count, error } = await q;

  const staff = isStaff(viewer);
  const { data: people } = staff
    ? await supabase.from("profiles").select("user_id, full_name, role, regions").order("full_name")
    : { data: [] as { user_id: string; full_name: string | null; role: string; regions: string[] }[] };
  const nameOf = new Map((people ?? []).map((p) => [p.user_id, p.full_name || p.user_id.slice(0, 8)]));

  const link = (over: Record<string, string | undefined>) => {
    const params = new URLSearchParams(Object.entries({ ...sp, ...over }).filter(([, v]) => v) as [string, string][]);
    return `/alerts?${params}`;
  };
  const sortLink = (k: SortKey) => link({ sort: k, dir: sort === k && !asc ? "asc" : undefined, page: undefined });
  const th = (k: SortKey, label: string) => (
    <th scope="col" className="px-3 py-2 text-left font-medium" aria-sort={sort === k ? (asc ? "ascending" : "descending") : "none"}>
      <Link href={sortLink(k)} className="hover:text-white">{label}{sort === k ? (asc ? " ↑" : " ↓") : ""}</Link>
    </th>
  );

  return (
    <AppShell viewer={viewer}>
      <Page title="Alerts" subtitle={`${count ?? 0} alerts${sp.status ? "" : " open"}`}
        actions={
          <form className="flex flex-wrap gap-2 text-sm" action="/alerts">
            <select name="level" defaultValue={sp.level ?? ""} aria-label="Level" className="rounded-md border border-ink-600 bg-ink-950 px-2 py-1.5">
              <option value="">All levels</option><option value="red">Red</option><option value="amber">Amber</option><option value="watch">Watch</option>
            </select>
            <select name="status" defaultValue={sp.status ?? ""} aria-label="Status" className="rounded-md border border-ink-600 bg-ink-950 px-2 py-1.5">
              <option value="">Open</option>{Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <input name="region" defaultValue={sp.region ?? ""} placeholder="Region" aria-label="Region" className="w-32 rounded-md border border-ink-600 bg-ink-950 px-2 py-1.5" />
            <label className="flex items-center gap-1.5 text-ink-300"><input type="checkbox" name="mine" value="1" defaultChecked={sp.mine === "1"} /> Mine</label>
            <button className="rounded-md bg-ink-700 px-3 py-1.5 text-white hover:bg-ink-600">Filter</button>
          </form>
        }>
        {error && <p className="mb-3 text-alert-red">Could not load alerts: {error.message}</p>}
        <form action={assignAlerts}>
          {staff && (
            <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
              <label htmlFor="assignee" className="text-ink-300">Assign selected to</label>
              <select id="assignee" name="assignee" className="rounded-md border border-ink-600 bg-ink-950 px-2 py-1.5">
                <option value="">— Unassigned —</option>
                {(people ?? []).filter((p) => p.role === "field_scout").map((p) => (
                  <option key={p.user_id} value={p.user_id}>{p.full_name || p.user_id.slice(0, 8)} ({p.regions.join(", ") || "no region"})</option>
                ))}
              </select>
              <button className="rounded-md bg-brand-500 px-3 py-1.5 font-medium text-white hover:bg-brand-400">Assign</button>
            </div>
          )}

          {/* Desktop table */}
          <div className="hidden overflow-x-auto rounded-xl border border-ink-700 md:block">
            <table className="w-full text-sm">
              <thead className="bg-ink-900 text-ink-300">
                <tr>
                  {staff && <th className="px-3 py-2"><span className="sr-only">Select</span></th>}
                  <th scope="col" className="px-3 py-2 text-left font-medium">Alert</th>
                  {th("score", "Score")}{th("area", "Size")}{th("date", "Last seen")}{th("region", "Place")}{th("status", "Status")}
                  <th scope="col" className="px-3 py-2 text-left font-medium">Assigned</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-800">
                {(alerts ?? []).map((a) => (
                  <tr key={a.id} className="hover:bg-ink-900/60">
                    {staff && <td className="px-3 py-2"><input type="checkbox" name="ids" value={a.id} aria-label={`Select alert ${a.id}`} /></td>}
                    <td className="px-3 py-2">
                      <Link href={`/alerts/${a.id}`} className="font-medium text-white hover:underline">#{a.id}</Link>{" "}
                      <Badge className={LEVEL_STYLE[a.level]}>{a.level}</Badge>
                    </td>
                    <td className="px-3 py-2 text-lg font-semibold tabular-nums text-white">{a.score}</td>
                    <td className="px-3 py-2 tabular-nums">{fmtHa(a.area_ha)}</td>
                    <td className="px-3 py-2">{fmtDate(a.last_seen)}</td>
                    <td className="px-3 py-2">{a.district ?? "—"}<span className="block text-xs text-ink-400">{a.region}{a.in_forest_reserve ? " · forest reserve" : ""}{a.river_distance_m != null && a.river_distance_m <= 100 ? " · river ≤100 m" : ""}</span></td>
                    <td className="px-3 py-2">{STATUS_LABEL[a.status]}</td>
                    <td className="px-3 py-2 text-ink-300">{a.assigned_to ? nameOf.get(a.assigned_to) ?? "Assigned" : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile cards */}
          <ul className="space-y-2 md:hidden">
            {(alerts ?? []).map((a) => (
              <li key={a.id} className="flex items-start gap-3 rounded-xl border border-ink-700 bg-ink-900 p-3">
                {staff && <input type="checkbox" name="ids" value={a.id} aria-label={`Select alert ${a.id}`} className="mt-1.5" />}
                <Link href={`/alerts/${a.id}`} className="flex-1">
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-white">#{a.id} <Badge className={LEVEL_STYLE[a.level]}>{a.level}</Badge></span>
                    <span className="text-2xl font-semibold tabular-nums text-white">{a.score}</span>
                  </div>
                  <p className="text-sm text-ink-300">{fmtHa(a.area_ha)} · {a.district ?? ""} {a.region} · {fmtDate(a.last_seen)}</p>
                  <p className="text-xs text-ink-400">{STATUS_LABEL[a.status]}</p>
                </Link>
              </li>
            ))}
          </ul>
          {!alerts?.length && <p className="rounded-xl border border-ink-700 p-6 text-center text-ink-300">No alerts match these filters.</p>}
        </form>

        {(count ?? 0) > PER && (
          <nav className="mt-4 flex gap-3 text-sm" aria-label="Pages">
            {page > 1 && <Link className="text-brand-300 hover:underline" href={link({ page: String(page - 1) })}>← Previous</Link>}
            <span className="text-ink-400">Page {page} of {Math.ceil((count ?? 0) / PER)}</span>
            {page * PER < (count ?? 0) && <Link className="text-brand-300 hover:underline" href={link({ page: String(page + 1) })}>Next →</Link>}
          </nav>
        )}
      </Page>
    </AppShell>
  );
}
