import { PDFDocument, StandardFonts, rgb, type PDFFont, type PDFPage } from "pdf-lib";
import { NextResponse, type NextRequest } from "next/server";
import { getViewer, isStaff } from "@/lib/auth";
import { STATUS_LABEL, fmtDate, fmtDateTime } from "@/lib/format";
import { createClient } from "@/lib/supabase/server";

// One-click evidence pack (M9) for a confirmed site: location map, imagery timeline,
// area growth, nearby rivers/reserves, field photos and the audit trail.
// Built from the caller's own session, so RLS limits it to what they may see.

const A4: [number, number] = [595.28, 841.89];
const M = 48;
const INK = rgb(0.07, 0.09, 0.08), GREY = rgb(0.4, 0.43, 0.42), RED = rgb(0.86, 0.2, 0.2), GREEN = rgb(0.05, 0.53, 0.37);

type Ctx = { doc: PDFDocument; page: PDFPage; y: number; font: PDFFont; bold: PDFFont };

function newPage(c: Ctx) { c.page = c.doc.addPage(A4); c.y = A4[1] - M; }
function ensure(c: Ctx, h: number) { if (c.y - h < M + 20) newPage(c); }
// pdf-lib standard fonts only cover WinAnsi; replace anything else so a stray emoji can't crash the export.
const safe = (s: string) => s.replace(/[–—]/g, "-").replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/[^\x20-\x7E\xA0-\xFF]/g, "?");

function text(c: Ctx, s: string, opts: { size?: number; bold?: boolean; color?: ReturnType<typeof rgb>; x?: number } = {}) {
  const size = opts.size ?? 10, font = opts.bold ? c.bold : c.font, maxW = A4[0] - 2 * M - ((opts.x ?? M) - M);
  const words = safe(s).split(/\s+/);
  let line = "";
  const lines: string[] = [];
  for (const w of words) {
    const t = line ? `${line} ${w}` : w;
    if (font.widthOfTextAtSize(t, size) > maxW && line) { lines.push(line); line = w; } else line = t;
  }
  if (line) lines.push(line);
  for (const l of lines) {
    ensure(c, size + 4);
    c.page.drawText(l, { x: opts.x ?? M, y: c.y - size, size, font, color: opts.color ?? INK });
    c.y -= size + 4;
  }
}
function heading(c: Ctx, s: string) { ensure(c, 40); c.y -= 10; text(c, s, { size: 13, bold: true, color: GREEN }); c.y -= 2; }

async function embedImage(c: Ctx, url: string) {
  const res = await fetch(url);
  if (!res.ok) return null;
  const bytes = new Uint8Array(await res.arrayBuffer());
  try { return bytes[0] === 0x89 ? await c.doc.embedPng(bytes) : await c.doc.embedJpg(bytes); } catch { return null; }
}

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const viewer = await getViewer();
  if (!viewer || !isStaff(viewer)) return new NextResponse("Forbidden", { status: 403 });
  const id = Number((await params).id);
  if (!Number.isInteger(id)) return new NextResponse("Not found", { status: 404 });
  const supabase = await createClient();
  const { data: a } = await supabase.from("alerts").select("*").eq("id", id).single();
  if (!a) return new NextResponse("Not found", { status: 404 });
  if (!["confirmed", "field_verified", "closed"].includes(a.status)) return new NextResponse("Evidence packs are only for confirmed sites", { status: 409 });

  const [{ data: ctx }, { data: history }, { data: reviews }, { data: reports }, { data: actions }, { data: srcs }] = await Promise.all([
    supabase.rpc("evidence_context", { p_alert: id }),
    supabase.from("alert_status_history").select("from_status, to_status, changed_at").eq("alert_id", id).order("changed_at"),
    supabase.from("reviews").select("decision, notes, created_at").eq("alert_id", id).order("created_at"),
    supabase.from("field_reports").select("notes, photos, visited_at").eq("alert_id", id).order("visited_at"),
    supabase.from("enforcement_actions").select("agency, action, action_date, notes").eq("alert_id", id).order("action_date"),
    supabase.from("alert_sources").select("detections(source, image_date, confidence)").eq("alert_id", id),
  ]);
  const ev = (ctx ?? {}) as { growth?: { image_date: string; cumulative_ha: number }[]; nearby?: { type: string; name: string | null; distance_m: number }[]; outline?: GeoJSON.Polygon | GeoJSON.MultiPolygon };

  const doc = await PDFDocument.create();
  doc.setTitle(`Galamsey Watch evidence pack - alert ${id}`);
  doc.setProducer("Galamsey Watch");
  const c: Ctx = { doc, page: doc.addPage(A4), y: A4[1] - M, font: await doc.embedFont(StandardFonts.Helvetica), bold: await doc.embedFont(StandardFonts.HelveticaBold) };
  const [lon, lat] = (a.centroid?.coordinates ?? [0, 0]) as [number, number];

  text(c, "GALAMSEY WATCH - EVIDENCE PACK", { size: 9, bold: true, color: GREEN });
  text(c, `Alert #${id}: ${Number(a.area_ha).toFixed(2)} ha ${a.in_forest_reserve ? `in ${a.forest_reserve_name ?? "a forest reserve"}` : "of new clearing"}`, { size: 18, bold: true });
  text(c, `${a.district ?? "Unknown district"}, ${a.region ?? "Unknown region"} · ${lat.toFixed(5)}, ${lon.toFixed(5)} · Status: ${STATUS_LABEL[a.status]} · Generated ${fmtDateTime(new Date().toISOString())} GMT`, { color: GREY });

  // Location map: Ghana outline with the site marked.
  heading(c, "Location");
  const box = { x: M, y: c.y - 220, w: 170, h: 220 };
  const [minx, miny, maxx, maxy] = [-3.35, 4.6, 1.3, 11.25];
  const px = (x: number) => box.x + ((x - minx) / (maxx - minx)) * box.w;
  const py = (y: number) => box.y + ((y - miny) / (maxy - miny)) * box.h;
  c.page.drawRectangle({ x: box.x, y: box.y, width: box.w, height: box.h, borderColor: GREY, borderWidth: 0.5 });
  const polys = ev.outline ? (ev.outline.type === "Polygon" ? [ev.outline.coordinates] : ev.outline.coordinates) : [];
  for (const poly of polys) {
    const ring = poly[0];
    for (let i = 1; i < ring.length; i++) c.page.drawLine({ start: { x: px(ring[i - 1][0]), y: py(ring[i - 1][1]) }, end: { x: px(ring[i][0]), y: py(ring[i][1]) }, thickness: 0.7, color: GREY });
  }
  c.page.drawCircle({ x: px(lon), y: py(lat), size: 4, color: RED });
  const facts = [
    `First seen: ${fmtDate(a.first_seen)}    Last seen: ${fmtDate(a.last_seen)}`,
    `Score: ${a.score}/100 (${a.level.toUpperCase()})`,
    `Forest reserve: ${a.in_forest_reserve ? a.forest_reserve_name ?? "yes" : "no"}`,
    `Nearest major river: ${a.river_distance_m != null ? `${a.river_distance_m} m (${a.river_name ?? "unnamed"})` : "more than 5 km"}`,
    `Licensed concession: ${a.in_concession ? "inside a licensed area" : "outside any licensed area on record"}`,
    `Detections: ${(srcs ?? []).length} from ${(a.sources ?? []).join(", ")}`,
  ];
  const yTop = c.y;
  facts.forEach((f, i) => c.page.drawText(safe(f), { x: box.x + box.w + 20, y: yTop - 14 - i * 16, size: 10, font: c.font, color: INK }));
  c.y = box.y - 10;

  heading(c, "Nearby (within 5 km)");
  for (const n of ev.nearby ?? []) text(c, `${n.type.replace("_", " ")}: ${n.name ?? "unnamed"} - ${n.distance_m} m`);
  if (!ev.nearby?.length) text(c, "No reference features within 5 km.", { color: GREY });

  heading(c, "Area growth");
  for (const g of ev.growth ?? []) text(c, `${fmtDate(g.image_date)}: ${Number(g.cumulative_ha).toFixed(2)} ha cumulative`);

  heading(c, "Imagery timeline");
  for (const [k, label] of [["s2_true", "Sentinel-2 true colour"], ["s2_false", "Sentinel-2 false colour"], ["s1_vv", "Sentinel-1 radar"]]) {
    const pair = await Promise.all(["before", "after"].map(async (w) => {
      const path = (a.chips ?? {})[`${k}_${w}`];
      if (!path) return null;
      const { data } = await supabase.storage.from("chips").createSignedUrl(path, 300);
      return data ? embedImage(c, data.signedUrl) : null;
    }));
    if (!pair[0] && !pair[1]) continue;
    ensure(c, 250);
    text(c, label, { bold: true });
    const size = (A4[0] - 2 * M - 12) / 2;
    pair.forEach((img, i) => img && c.page.drawImage(img, { x: M + i * (size + 12), y: c.y - size, width: size, height: size }));
    c.y -= size + 8;
  }

  if ((reports ?? []).length) {
    heading(c, "Field verification");
    for (const r of reports ?? []) {
      text(c, `${fmtDateTime(r.visited_at)}: ${r.notes ?? ""}`);
      for (const p of (r.photos ?? []).slice(0, 4)) {
        const { data } = await supabase.storage.from("field-photos").createSignedUrl(p, 300);
        const img = data && (await embedImage(c, data.signedUrl));
        if (!img) continue;
        const w = 240, h = (img.height / img.width) * w;
        ensure(c, h + 8);
        c.page.drawImage(img, { x: M, y: c.y - h, width: w, height: h });
        c.y -= h + 8;
      }
    }
  }

  heading(c, "Audit trail");
  const trail = [
    ...(history ?? []).map((h) => ({ at: h.changed_at, t: `Status ${h.from_status ? `${STATUS_LABEL[h.from_status]} -> ` : ""}${STATUS_LABEL[h.to_status]}` })),
    ...(reviews ?? []).map((r) => ({ at: r.created_at, t: `Analyst review: ${r.decision.replace(/_/g, " ")}${r.notes ? ` - ${r.notes}` : ""}` })),
    ...(actions ?? []).map((e) => ({ at: e.action_date, t: `${e.agency}: ${e.action}${e.notes ? ` - ${e.notes}` : ""}` })),
  ].sort((x, y) => String(x.at).localeCompare(String(y.at)));
  for (const t of trail) text(c, `${fmtDateTime(t.at)}  ${t.t}`);

  c.y -= 10;
  text(c, "Method: detections from free satellite data (Copernicus Sentinel-1/2, Global Forest Watch alerts) were fused and scored automatically, then reviewed and confirmed by a Galamsey Watch analyst. Contains modified Copernicus Sentinel data. Reviewer identities are withheld from this document.", { size: 8, color: GREY });

  const bytes = await doc.save();
  return new NextResponse(Buffer.from(bytes), {
    headers: { "Content-Type": "application/pdf", "Content-Disposition": `attachment; filename="galamsey-watch-alert-${id}.pdf"`, "Cache-Control": "private, no-store" },
  });
}
