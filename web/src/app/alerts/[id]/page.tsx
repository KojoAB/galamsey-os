import Link from "next/link";
import { notFound } from "next/navigation";
import AppShell, { Badge, Card, DemoNotice, Page } from "@/components/AppShell";
import BeforeAfter from "@/components/BeforeAfter";
import { isStaff, requireViewer } from "@/lib/auth";
import { LEVEL_STYLE, SOURCE_LABEL, STATUS_LABEL, fmtDate, fmtDateTime, fmtHa } from "@/lib/format";
import { isDemo } from "@/lib/supabase/env";
import { createClient } from "@/lib/supabase/server";
import { addEnforcement, assignAlerts, requestTasking, reviewAlert, setAlertStatus } from "../actions";

const FACTOR_LABEL: Record<string, string> = {
  base: "Detection passed the size filter", sources_agree: "Independent sources agree", ai_classifier: "AI mine classifier",
  size: "Size of clearing", persistence: "Seen again on a later pass", forest_reserve_or_river: "In forest reserve or ≤100 m from a river",
  river_500m: "Within 500 m of a river", outside_concession: "Outside any licensed concession",
  inside_concession: "Inside a licensed concession", low_confidence: "Cloud-affected or single low-confidence source",
  river_turbidity: "River turbidity jump",
};
const CHIP_SETS = [["s2_true", "Sentinel-2 true colour"], ["s2_false", "Sentinel-2 false colour (vegetation red)"], ["s1_vv", "Sentinel-1 radar"]] as const;

export default async function AlertDetail({ params }: { params: Promise<{ id: string }> }) {
  if (isDemo) return <AppShell viewer={null} demo><Page title="Alert"><DemoNotice /></Page></AppShell>;
  const viewer = await requireViewer();
  const id = Number((await params).id);
  if (!Number.isInteger(id)) notFound();
  const supabase = await createClient();
  const staff = isStaff(viewer);

  const { data: a } = await supabase.from("alerts").select("*").eq("id", id).single();
  if (!a) notFound();

  const [{ data: sources }, { data: history }, { data: reviews }, { data: tips }, { data: reports }, { data: actions }, { data: tasking }, { data: people }] =
    await Promise.all([
      supabase.from("alert_sources").select("detections(id, source, image_date, confidence, metrics)").eq("alert_id", id),
      supabase.from("alert_status_history").select("from_status, to_status, changed_at, changed_by").eq("alert_id", id).order("changed_at"),
      staff ? supabase.from("reviews").select("decision, notes, created_at, user_id").eq("alert_id", id).order("created_at") : Promise.resolve({ data: [] }),
      staff ? supabase.from("tips").select("reference_code, message, received_at, media").eq("linked_alert_id", id) : Promise.resolve({ data: [] }),
      supabase.from("field_reports").select("id, notes, photos, visited_at, user_id").eq("alert_id", id).order("visited_at"),
      supabase.from("enforcement_actions").select("agency, action, action_date, notes").eq("alert_id", id).order("action_date"),
      staff ? supabase.from("tasking_requests").select("id, status, priority, created_at").eq("alert_id", id) : Promise.resolve({ data: [] }),
      staff ? supabase.from("profiles").select("user_id, full_name, role") : Promise.resolve({ data: [] }),
    ]);
  const nameOf = new Map(((people ?? []) as { user_id: string; full_name: string | null }[]).map((p) => [p.user_id, p.full_name || p.user_id.slice(0, 8)]));

  // Short-lived signed URLs for private storage objects.
  const chips: Record<string, string> = {};
  for (const [k, path] of Object.entries((a.chips ?? {}) as Record<string, string>)) {
    const { data } = await supabase.storage.from("chips").createSignedUrl(path, 3600);
    if (data) chips[k] = data.signedUrl;
  }
  const photoUrls: Record<string, string> = {};
  for (const r of reports ?? []) for (const p of r.photos ?? []) {
    const { data } = await supabase.storage.from("field-photos").createSignedUrl(p, 3600);
    if (data) photoUrls[p] = data.signedUrl;
  }

  const [lon, lat] = (a.centroid?.coordinates ?? [0, 0]) as [number, number];
  const dets = (sources ?? []).flatMap((s) => (Array.isArray(s.detections) ? s.detections : [s.detections])).filter(Boolean) as
    { id: number; source: string; image_date: string; confidence: number; metrics: Record<string, unknown> }[];
  const breakdown = Object.entries((a.score_breakdown ?? {}) as Record<string, number>).filter(([, v]) => typeof v === "number");

  const timeline = [
    ...(history ?? []).map((h) => ({ at: h.changed_at, text: `Status ${h.from_status ? `${STATUS_LABEL[h.from_status]} → ` : ""}${STATUS_LABEL[h.to_status]}`, who: h.changed_by ? nameOf.get(h.changed_by) : "pipeline" })),
    ...(reviews ?? []).map((r: { created_at: string; decision: string; notes: string | null; user_id: string }) => ({ at: r.created_at, text: `Review: ${r.decision.replace(/_/g, " ")}${r.notes ? ` — “${r.notes}”` : ""}`, who: nameOf.get(r.user_id) })),
    ...(reports ?? []).map((r) => ({ at: r.visited_at, text: `Field visit${r.notes ? ` — “${r.notes}”` : ""}`, who: nameOf.get(r.user_id) })),
    ...(actions ?? []).map((e) => ({ at: e.action_date, text: `${e.agency}: ${e.action}${e.notes ? ` — ${e.notes}` : ""}`, who: e.agency })),
  ].sort((x, y) => String(x.at).localeCompare(String(y.at)));

  return (
    <AppShell viewer={viewer}>
      <Page
        title={`Alert #${a.id}`}
        subtitle={`${fmtHa(a.area_ha)} · ${a.district ?? "Unknown district"}, ${a.region ?? "Unknown region"} · first seen ${fmtDate(a.first_seen)}, last seen ${fmtDate(a.last_seen)}`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Badge className={LEVEL_STYLE[a.level]}>{a.level.toUpperCase()}</Badge>
            <Badge className="bg-ink-800 text-ink-200 ring-ink-600">{STATUS_LABEL[a.status]}</Badge>
            <span className="text-3xl font-semibold tabular-nums text-white" aria-label={`Score ${a.score}`}>{a.score}</span>
            {staff && ["confirmed", "field_verified", "closed"].includes(a.status) && (
              <a href={`/api/alerts/${a.id}/evidence`} className="rounded-lg bg-brand-500 px-3 py-2 text-sm font-semibold text-white hover:bg-brand-400">Evidence pack (PDF)</a>
            )}
          </div>
        }>
        <div className="grid gap-4 lg:grid-cols-3">
          <div className="space-y-4 lg:col-span-2">
            <Card title="Before / after">
              {Object.keys(chips).length ? (
                <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                  {CHIP_SETS.filter(([k]) => chips[`${k}_before`] && chips[`${k}_after`]).map(([k, label]) => (
                    <BeforeAfter key={k} before={chips[`${k}_before`]} after={chips[`${k}_after`]} label={label} />
                  ))}
                </div>
              ) : <p className="text-sm text-ink-300">No image chips yet. They are made by the 05:00 run when Earth Engine is configured.</p>}
            </Card>

            <div className="grid gap-4 sm:grid-cols-2">
              <Card title="Why this score">
                <ul className="space-y-1.5 text-sm">
                  {breakdown.map(([k, v]) => (
                    <li key={k} className="flex justify-between gap-3">
                      <span className="text-ink-200">{FACTOR_LABEL[k] ?? k}</span>
                      <span className={`tabular-nums font-medium ${v < 0 ? "text-alert-red" : "text-brand-300"}`}>{v > 0 ? `+${v}` : v}</span>
                    </li>
                  ))}
                  <li className="flex justify-between border-t border-ink-700 pt-1.5 font-semibold text-white"><span>Total (0–100)</span><span>{a.score}</span></li>
                </ul>
              </Card>
              <Card title="Location">
                <dl className="space-y-1.5 text-sm">
                  <div className="flex justify-between"><dt className="text-ink-300">Coordinates</dt><dd className="tabular-nums">{lat.toFixed(5)}, {lon.toFixed(5)}</dd></div>
                  <div className="flex justify-between"><dt className="text-ink-300">Forest reserve</dt><dd>{a.in_forest_reserve ? a.forest_reserve_name ?? "Yes" : "No"}</dd></div>
                  <div className="flex justify-between"><dt className="text-ink-300">Nearest major river</dt><dd>{a.river_distance_m != null ? `${a.river_distance_m} m (${a.river_name ?? "unnamed"})` : "> 5 km"}</dd></div>
                  <div className="flex justify-between"><dt className="text-ink-300">Licensed concession</dt><dd>{a.in_concession ? "Inside" : "Outside / no data"}</dd></div>
                  {a.ai_probability != null && <div className="flex justify-between"><dt className="text-ink-300">AI mine probability</dt><dd>{Math.round(a.ai_probability * 100)}%</dd></div>}
                </dl>
                <a className="mt-3 inline-block text-sm text-brand-300 hover:underline" target="_blank" rel="noopener noreferrer"
                   href={`https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}`}>Directions ↗</a>
              </Card>
            </div>

            <Card title={`Sources (${dets.length})`}>
              <ul className="divide-y divide-ink-800 text-sm">
                {dets.map((d) => (
                  <li key={d.id} className="flex flex-wrap justify-between gap-2 py-1.5">
                    <span className="text-white">{SOURCE_LABEL[d.source] ?? d.source}</span>
                    <span className="text-ink-300">{fmtDate(d.image_date)} · confidence {Math.round((d.confidence ?? 0) * 100)}%</span>
                  </li>
                ))}
              </ul>
            </Card>

            <Card title="Timeline">
              <ol className="space-y-2 text-sm">
                {timeline.map((t, i) => (
                  <li key={i} className="flex gap-3"><span className="w-32 shrink-0 text-ink-400">{fmtDateTime(t.at)}</span><span className="text-ink-200">{t.text}{t.who ? <span className="text-ink-400"> · {t.who}</span> : null}</span></li>
                ))}
              </ol>
            </Card>

            {(reports ?? []).some((r) => r.photos?.length) && (
              <Card title="Field photos">
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  {(reports ?? []).flatMap((r) => r.photos ?? []).map((p) => photoUrls[p] && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <a key={p} href={photoUrls[p]} target="_blank" rel="noopener noreferrer"><img src={photoUrls[p]} alt="Field photo" className="aspect-square w-full rounded-lg object-cover" /></a>
                  ))}
                </div>
              </Card>
            )}
          </div>

          <div className="space-y-4">
            {staff && ["new", "reviewing", "rejected"].includes(a.status) && (
              <Card title="Review">
                <form action={reviewAlert} className="space-y-3">
                  <input type="hidden" name="alert_id" value={a.id} />
                  <label className="block text-sm text-ink-300">Notes
                    <textarea name="notes" rows={3} maxLength={4000} className="mt-1 w-full rounded-lg border border-ink-600 bg-ink-950 p-2 text-white" />
                  </label>
                  <div className="grid grid-cols-2 gap-2">
                    <button name="decision" value="confirm" className="rounded-lg bg-alert-red px-3 py-2.5 font-semibold text-white hover:opacity-90">Confirm mining</button>
                    <button name="decision" value="reject" className="rounded-lg bg-ink-700 px-3 py-2.5 font-semibold text-white hover:bg-ink-600">Reject</button>
                  </div>
                  <button name="decision" value="needs_more_info" className="w-full rounded-lg border border-ink-600 px-3 py-2 text-sm text-ink-200 hover:bg-ink-800">Needs more info</button>
                  <p className="text-xs text-ink-400">Your decision is logged and becomes a training label for the AI model.</p>
                </form>
              </Card>
            )}

            {staff && (
              <Card title="Assign to field scout">
                <form action={assignAlerts} className="flex gap-2">
                  <input type="hidden" name="ids" value={a.id} />
                  <select name="assignee" defaultValue={a.assigned_to ?? ""} aria-label="Scout" className="min-w-0 flex-1 rounded-lg border border-ink-600 bg-ink-950 px-2 py-2 text-sm">
                    <option value="">— Unassigned —</option>
                    {((people ?? []) as { user_id: string; full_name: string | null; role: string }[]).filter((p) => p.role === "field_scout").map((p) => (
                      <option key={p.user_id} value={p.user_id}>{p.full_name || p.user_id.slice(0, 8)}</option>
                    ))}
                  </select>
                  <button className="rounded-lg bg-ink-700 px-3 text-sm text-white hover:bg-ink-600">Save</button>
                </form>
              </Card>
            )}

            {(a.status === "confirmed" || a.status === "field_verified") && (
              <Card title="Progress">
                <form action={setAlertStatus} className="space-y-2">
                  <input type="hidden" name="alert_id" value={a.id} />
                  {a.status === "confirmed" && (a.assigned_to === viewer.id || staff) &&
                    <button name="status" value="field_verified" className="w-full rounded-lg bg-brand-500 px-3 py-2 font-medium text-white hover:bg-brand-400">Mark field verified</button>}
                  {staff && <button name="status" value="closed" className="w-full rounded-lg border border-ink-600 px-3 py-2 text-sm text-ink-200 hover:bg-ink-800">Close alert</button>}
                </form>
                {a.assigned_to === viewer.id && <Link href={`/field?alert=${a.id}`} className="mt-2 block text-sm text-brand-300 hover:underline">Add field report →</Link>}
              </Card>
            )}

            {staff && (
              <Card title="Enforcement outcome">
                <form action={addEnforcement} className="space-y-2 text-sm">
                  <input type="hidden" name="alert_id" value={a.id} />
                  <div className="grid grid-cols-2 gap-2">
                    <input name="agency" defaultValue="NAIMOS" aria-label="Agency" className="rounded-lg border border-ink-600 bg-ink-950 px-2 py-2" />
                    <select name="action" aria-label="Action" className="rounded-lg border border-ink-600 bg-ink-950 px-2 py-2">
                      <option value="raided">Raided</option><option value="closed">Closed</option><option value="reclaimed">Reclaimed</option><option value="arrests">Arrests</option>
                    </select>
                  </div>
                  <input type="date" name="action_date" required aria-label="Date" className="w-full rounded-lg border border-ink-600 bg-ink-950 px-2 py-2" />
                  <input name="notes" placeholder="Notes" aria-label="Notes" className="w-full rounded-lg border border-ink-600 bg-ink-950 px-2 py-2" />
                  <button className="w-full rounded-lg bg-ink-700 px-3 py-2 text-white hover:bg-ink-600">Add to timeline</button>
                </form>
              </Card>
            )}

            {staff && (tips ?? []).length > 0 && (
              <Card title="Linked tips">
                <ul className="space-y-2 text-sm">
                  {(tips as { reference_code: string; message: string | null; received_at: string }[]).map((t) => (
                    <li key={t.reference_code}><span className="font-medium text-white">{t.reference_code}</span> · <span className="text-ink-400">{fmtDateTime(t.received_at)}</span><p className="text-ink-200">{t.message}</p></li>
                  ))}
                </ul>
              </Card>
            )}

            {staff && a.level === "red" && (
              <Card title="High-resolution imagery (paid)">
                {(tasking ?? []).length ? (
                  <ul className="text-sm">{(tasking as { id: number; status: string; priority: string }[]).map((t) => <li key={t.id}>Request #{t.id}: {t.status.replace(/_/g, " ")} ({t.priority})</li>)}</ul>
                ) : (
                  <form action={requestTasking} className="space-y-2">
                    <input type="hidden" name="alert_id" value={a.id} />
                    <select name="priority" aria-label="Priority" className="w-full rounded-lg border border-ink-600 bg-ink-950 px-2 py-2 text-sm">
                      <option value="standard">Standard</option><option value="urgent">Urgent</option>
                    </select>
                    <button className="w-full rounded-lg border border-alert-amber/50 px-3 py-2 text-sm text-alert-amber hover:bg-alert-amber/10">Draft tasking request</button>
                    <p className="text-xs text-ink-400">Costs money. Nothing is ordered until an admin approves it.</p>
                  </form>
                )}
              </Card>
            )}
          </div>
        </div>
      </Page>
    </AppShell>
  );
}
