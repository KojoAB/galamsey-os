import Link from "next/link";
import AppShell, { Badge, Card, DemoNotice, Page } from "@/components/AppShell";
import { requireViewer } from "@/lib/auth";
import { fmtDate, fmtDateTime } from "@/lib/format";
import { createAdminClient } from "@/lib/supabase/admin";
import { isDemo } from "@/lib/supabase/env";
import { createClient } from "@/lib/supabase/server";
import { addRecipient, decideTasking, inviteUser, removeRecipient, updateProfile, updateSetting } from "./actions";

const TABS = [["users", "Users"], ["settings", "Thresholds"], ["recipients", "Recipients"], ["connectors", "Data sources"], ["runs", "Pipeline runs"], ["paid", "Paid data"]] as const;
const input = "rounded-lg border border-ink-600 bg-ink-950 px-2 py-1.5 text-sm";
const btn = "rounded-lg bg-ink-700 px-3 py-1.5 text-sm text-white hover:bg-ink-600";
const STATUS_STYLE: Record<string, string> = { connected: "bg-brand-600/30 text-brand-300 ring-brand-500/40", stale: "bg-alert-amber/15 text-alert-amber ring-alert-amber/40", off: "bg-ink-800 text-ink-300 ring-ink-600" };

export default async function AdminPage({ searchParams }: { searchParams: Promise<{ tab?: string }> }) {
  if (isDemo) return <AppShell viewer={null} demo><Page title="Admin"><DemoNotice /></Page></AppShell>;
  const viewer = await requireViewer(["admin"]);
  const tab = (await searchParams).tab ?? "users";
  const supabase = await createClient();

  let body: React.ReactNode = null;
  if (tab === "users") {
    const { data: profiles } = await supabase.from("profiles").select("*").order("created_at");
    let emails = new Map<string, string>();
    try {
      const { data } = await createAdminClient().auth.admin.listUsers({ perPage: 1000 });
      emails = new Map(data.users.map((u) => [u.id, u.email ?? ""]));
    } catch { /* service key not configured: names only */ }
    body = (
      <div className="space-y-4">
        <Card title="Invite a user">
          <form action={inviteUser} className="flex flex-wrap gap-2">
            <input name="email" type="email" required placeholder="name@agency.gov.gh" aria-label="Email" className={`${input} min-w-64 flex-1`} />
            <button className={btn}>Send invite</button>
          </form>
          <p className="mt-2 text-xs text-ink-400">New users start as field scouts with no regions. Set their role below after they accept.</p>
        </Card>
        <Card title={`Users (${profiles?.length ?? 0})`}>
          <ul className="divide-y divide-ink-800">
            {(profiles ?? []).map((p) => (
              <li key={p.user_id} className="py-2">
                <form action={updateProfile} className="flex flex-wrap items-center gap-2">
                  <input type="hidden" name="user_id" value={p.user_id} />
                  <span className="w-56 truncate text-sm text-white">{emails.get(p.user_id) || p.user_id.slice(0, 8)}</span>
                  <input name="full_name" defaultValue={p.full_name ?? ""} placeholder="Name" aria-label="Name" className={`${input} w-40`} />
                  <select name="role" defaultValue={p.role} aria-label="Role" className={input}>
                    <option value="admin">Admin</option><option value="analyst">Analyst</option><option value="field_scout">Field scout</option>
                  </select>
                  <input name="regions" defaultValue={(p.regions ?? []).join(", ")} placeholder="Regions, comma separated" aria-label="Regions" className={`${input} min-w-48 flex-1`} />
                  <button className={btn}>Save</button>
                </form>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    );
  } else if (tab === "settings") {
    const { data: settings } = await supabase.from("settings").select("*").order("key");
    body = (
      <Card title="Thresholds and limits">
        <ul className="divide-y divide-ink-800">
          {(settings ?? []).map((s) => (
            <li key={s.key} className="py-3">
              <form action={updateSetting} className="flex flex-wrap items-start gap-2">
                <input type="hidden" name="key" value={s.key} />
                <div className="w-64"><p className="font-mono text-sm text-white">{s.key}</p><p className="text-xs text-ink-400">{s.description}</p></div>
                {typeof s.value === "object"
                  ? <textarea name="value" defaultValue={JSON.stringify(s.value, null, 1)} rows={4} aria-label={s.key} className={`${input} min-w-72 flex-1 font-mono`} />
                  : <input name="value" defaultValue={JSON.stringify(s.value)} aria-label={s.key} className={`${input} w-32 font-mono`} />}
                <button className={btn}>Save</button>
                <span className="text-xs text-ink-400">Updated {fmtDateTime(s.updated_at)}</span>
              </form>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-alert-amber">planet_enabled turns on the paid Planet module. Only switch it on after the spend is approved.</p>
      </Card>
    );
  } else if (tab === "recipients") {
    const { data: rec } = await supabase.from("recipients").select("*").order("region");
    body = (
      <div className="space-y-4">
        <Card title="Add recipient">
          <form action={addRecipient} className="flex flex-wrap items-center gap-2 text-sm">
            <input name="name" placeholder="Name" aria-label="Name" className={input} />
            <select name="channel" aria-label="Channel" className={input}><option value="whatsapp">WhatsApp</option><option value="sms">SMS</option><option value="email">Email</option></select>
            <input name="address" required placeholder="+233… or email" aria-label="Phone or email" className={input} />
            <input name="region" placeholder="Region (blank = national)" aria-label="Region" className={input} />
            {["red", "amber", "watch"].map((l) => <label key={l} className="flex items-center gap-1 capitalize text-ink-300"><input type="checkbox" name="levels" value={l} defaultChecked={l !== "watch"} />{l}</label>)}
            <label className="flex items-center gap-1 text-ink-300"><input type="checkbox" name="weekly" defaultChecked />Weekly digest</label>
            <button className={btn}>Add</button>
          </form>
        </Card>
        <Card title={`Recipients (${rec?.length ?? 0})`}>
          <table className="w-full text-sm">
            <thead className="text-left text-ink-400"><tr><th className="py-1 font-medium">Name</th><th className="font-medium">Channel</th><th className="font-medium">Address</th><th className="font-medium">Region</th><th className="font-medium">Levels</th><th /></tr></thead>
            <tbody>{(rec ?? []).map((r) => (
              <tr key={r.id} className="border-t border-ink-800">
                <td className="py-1.5 text-white">{r.name}</td><td>{r.channel}</td><td>{r.address}</td><td>{r.region ?? "National"}</td><td>{r.levels.join(", ")}</td>
                <td><form action={removeRecipient}><input type="hidden" name="id" value={r.id} /><button className="text-alert-red hover:underline">Remove</button></form></td>
              </tr>
            ))}</tbody>
          </table>
        </Card>
      </div>
    );
  } else if (tab === "connectors") {
    const { data: conns } = await supabase.from("connectors").select("*").order("agency");
    const { data: layers } = await supabase.from("reference_layer_summary").select("*");
    body = (
      <div className="space-y-4">
        <Card title="Government connectors">
          <ul className="divide-y divide-ink-800 text-sm">
            {(conns ?? []).map((c) => (
              <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span><span className="text-white">{c.agency}</span> · {c.type.replace(/_/g, " ")}<span className="block text-xs text-ink-400">{c.terms}{c.last_error ? ` · last error: ${c.last_error}` : ""}</span></span>
                <span className="flex items-center gap-2"><span className="text-xs text-ink-400">last success {fmtDateTime(c.last_success_at)}</span><Badge className={STATUS_STYLE[c.status]}>{c.status}</Badge></span>
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Reference layers">
          <table className="w-full text-sm">
            <thead className="text-left text-ink-400"><tr><th className="py-1 font-medium">Layer</th><th className="font-medium">Source</th><th className="font-medium">Features</th><th className="font-medium">Retrieved</th><th className="font-medium">Licence</th></tr></thead>
            <tbody>{(layers ?? []).map((l) => (
              <tr key={`${l.type}-${l.source}`} className="border-t border-ink-800"><td className="py-1.5 text-white">{l.type}</td><td>{l.source}{l.verified && <span className="ml-1 text-brand-300">✓ verified</span>}</td><td>{l.features}</td><td>{fmtDate(l.retrieved_at)}</td><td className="text-xs text-ink-400">{l.licence}</td></tr>
            ))}</tbody>
          </table>
        </Card>
      </div>
    );
  } else if (tab === "runs") {
    const { data: runs } = await supabase.from("pipeline_runs").select("*").order("started_at", { ascending: false }).limit(50);
    body = (
      <Card title="Last 50 runs">
        <table className="w-full text-sm">
          <thead className="text-left text-ink-400"><tr><th className="py-1 font-medium">Job</th><th className="font-medium">Started</th><th className="font-medium">Status</th><th className="font-medium">Tiles</th><th className="font-medium">Scenes</th><th className="font-medium">Alerts</th><th className="font-medium">Errors</th></tr></thead>
          <tbody>{(runs ?? []).map((r) => (
            <tr key={r.id} className="border-t border-ink-800 align-top">
              <td className="py-1.5 text-white">{r.job}</td><td>{fmtDateTime(r.started_at)}</td>
              <td className={r.status === "failed" ? "text-alert-red" : r.status === "partial" ? "text-alert-amber" : "text-brand-300"}>{r.status}</td>
              <td>{r.tiles_processed}</td><td>{r.scenes_used}</td><td>{r.alerts_created}</td>
              <td className="max-w-md text-xs text-ink-400">{(r.errors as { message: string }[]).slice(0, 3).map((e, i) => <div key={i}>{e.message}</div>)}</td>
            </tr>
          ))}</tbody>
        </table>
      </Card>
    );
  } else if (tab === "paid") {
    const [{ data: spend }, { data: reqs }] = await Promise.all([
      supabase.from("paid_spend").select("*").order("spent_on", { ascending: false }),
      supabase.from("tasking_requests").select("*").order("created_at", { ascending: false }).limit(50),
    ]);
    const byMonth = new Map<string, number>();
    (spend ?? []).forEach((s) => byMonth.set(s.spent_on.slice(0, 7), (byMonth.get(s.spent_on.slice(0, 7)) ?? 0) + Number(s.amount_usd)));
    body = (
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Spend per month (USD)">
          {byMonth.size ? <ul className="text-sm">{[...byMonth.entries()].map(([m, v]) => <li key={m} className="flex justify-between border-t border-ink-800 py-1.5"><span>{m}</span><span className="tabular-nums text-white">${v.toFixed(2)}</span></li>)}</ul>
            : <p className="text-sm text-ink-300">No paid data used. The Planet module is off by default.</p>}
        </Card>
        <Card title="High-resolution tasking requests">
          <ul className="divide-y divide-ink-800 text-sm">
            {(reqs ?? []).map((t) => (
              <li key={t.id} className="py-2">
                <div className="flex justify-between"><Link href={`/alerts/${t.alert_id}`} className="text-white hover:underline">Alert #{t.alert_id}</Link><span>{t.status.replace(/_/g, " ")} · {t.priority}</span></div>
                {t.status === "pending_approval" && (
                  <form action={decideTasking} className="mt-2 flex flex-wrap gap-2">
                    <input type="hidden" name="id" value={t.id} />
                    <input name="cost" type="number" step="0.01" min="0" placeholder="Estimated cost USD" aria-label="Estimated cost" className={`${input} w-44`} />
                    <button name="decision" value="approve" className="rounded-lg bg-alert-amber px-3 py-1.5 text-sm font-semibold text-ink-950">Approve spend</button>
                    <button name="decision" value="reject" className={btn}>Reject</button>
                  </form>
                )}
              </li>
            ))}
            {!reqs?.length && <li className="py-2 text-ink-300">No requests.</li>}
          </ul>
        </Card>
      </div>
    );
  }

  return (
    <AppShell viewer={viewer}>
      <Page title="Admin">
        <nav className="mb-4 flex flex-wrap gap-1 border-b border-ink-700" aria-label="Admin sections">
          {TABS.map(([k, label]) => (
            <Link key={k} href={`/admin?tab=${k}`} aria-current={tab === k ? "page" : undefined}
              className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === k ? "border-brand-400 text-white" : "border-transparent text-ink-300 hover:text-white"}`}>{label}</Link>
          ))}
        </nav>
        {body}
      </Page>
    </AppShell>
  );
}
