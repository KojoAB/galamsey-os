import { NextResponse, type NextRequest } from "next/server";
import { createAdminClient } from "@/lib/supabase/admin";
import { hashPhone, inGhana, isSpam, parseWebhook, referenceCode, verifySignature, type IncomingMessage } from "@/lib/tips";

// WhatsApp Cloud API webhook for the public tip line (M7).
// Env: WHATSAPP_VERIFY_TOKEN, WHATSAPP_APP_SECRET, WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, TIP_HASH_SALT, SUPABASE_SERVICE_ROLE_KEY.
const GRAPH = "https://graph.facebook.com/v21.0";
const GROUP_MINUTES = 30;   // messages from the same sender within 30 min join one tip

// Meta calls this once to confirm we own the URL.
export function GET(req: NextRequest) {
  const p = req.nextUrl.searchParams;
  if (p.get("hub.mode") === "subscribe" && p.get("hub.verify_token") === process.env.WHATSAPP_VERIFY_TOKEN && process.env.WHATSAPP_VERIFY_TOKEN) {
    return new NextResponse(p.get("hub.challenge") ?? "", { status: 200 });
  }
  return new NextResponse("Forbidden", { status: 403 });
}

async function reply(to: string, text: string) {
  // Replies inside the 24 h window opened by the tipster can be free text.
  if (!process.env.WHATSAPP_TOKEN || !process.env.WHATSAPP_PHONE_NUMBER_ID) return;
  await fetch(`${GRAPH}/${process.env.WHATSAPP_PHONE_NUMBER_ID}/messages`, {
    method: "POST", headers: { Authorization: `Bearer ${process.env.WHATSAPP_TOKEN}`, "Content-Type": "application/json" },
    body: JSON.stringify({ messaging_product: "whatsapp", to, type: "text", text: { body: text } }),
  }).catch(() => undefined);
}

async function storeMedia(tipId: number, mediaId: string): Promise<string | null> {
  const token = process.env.WHATSAPP_TOKEN;
  if (!token) return null;
  const meta = await fetch(`${GRAPH}/${encodeURIComponent(mediaId)}`, { headers: { Authorization: `Bearer ${token}` } });
  if (!meta.ok) return null;
  const { url, mime_type } = (await meta.json()) as { url: string; mime_type: string };
  // Only download from Meta's own media host (prevents being tricked into fetching other URLs).
  if (!/^https:\/\/[a-z0-9.-]+\.(fbcdn\.net|whatsapp\.net|facebook\.com)\//.test(url)) return null;
  const file = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
  if (!file.ok) return null;
  const buf = await file.arrayBuffer();
  if (buf.byteLength > 16 * 1024 * 1024) return null;
  const ext = (mime_type?.split("/")[1] ?? "bin").replace(/[^a-z0-9]/g, "").slice(0, 5);
  const path = `${tipId}/${mediaId.replace(/[^A-Za-z0-9_-]/g, "")}.${ext}`;
  const { error } = await createAdminClient().storage.from("tips").upload(path, buf, { contentType: mime_type, upsert: true });
  return error ? null : path;
}

async function handle(m: IncomingMessage) {
  const db = createAdminClient();
  const hash = hashPhone(m.from, process.env.TIP_HASH_SALT ?? "");
  const { data: limitRow } = await db.from("settings").select("value").eq("key", "tip_rate_limit_per_hour").single();
  const { data: allowed } = await db.rpc("tip_rate_hit", { p_hash: hash, p_limit: Number(limitRow?.value ?? 10) });
  if (!allowed) return;
  const text = m.text ?? m.caption;
  if (isSpam(text)) return;

  // Continue the sender's recent tip, or start a new one.
  const since = new Date(Date.now() - GROUP_MINUTES * 60_000).toISOString();
  const { data: open } = await db.from("tips").select("id, reference_code, message, media, geom")
    .eq("phone_hash", hash).eq("status", "new").gte("last_message_at", since).order("id", { ascending: false }).limit(1).maybeSingle();

  let tip = open;
  if (!tip) {
    for (let i = 0; i < 3 && !tip; i++) {
      const { data } = await db.from("tips").insert({ reference_code: referenceCode(), channel: "whatsapp", phone_hash: hash })
        .select("id, reference_code, message, media, geom").single();
      tip = data;
    }
    if (!tip) return;
    await reply(m.from, `Thank you. Your report reference is ${tip.reference_code}. ` +
      "You are anonymous: we do not store your phone number. You can send photos and your location pin (📎 → Location) now.");
  }

  const update: Record<string, unknown> = { last_message_at: new Date().toISOString() };
  if (text) update.message = [tip.message, text].filter(Boolean).join("\n").slice(0, 8000);
  if (m.mediaId) {
    const path = await storeMedia(tip.id, m.mediaId);
    if (path) update.media = [...(tip.media ?? []), path].slice(0, 20);
  }
  if (m.type === "location" && inGhana(m.lat, m.lon)) update.geom = `SRID=4326;POINT(${m.lon} ${m.lat})`;
  await db.from("tips").update(update).eq("id", tip.id);

  if (update.geom) {
    const { data: linked } = await db.rpc("link_tip", { p_tip: tip.id });
    await reply(m.from, linked ? `Location received for ${tip.reference_code}. Our analysts are already looking at this area. Thank you.`
                               : `Location received for ${tip.reference_code}. An analyst will check the satellite images. Thank you.`);
  }
}

export async function POST(req: NextRequest) {
  const raw = await req.text();
  if (!verifySignature(raw, req.headers.get("x-hub-signature-256"), process.env.WHATSAPP_APP_SECRET ?? "")) {
    return new NextResponse("Invalid signature", { status: 401 });
  }
  let body: unknown;
  try { body = JSON.parse(raw); } catch { return new NextResponse("Bad request", { status: 400 }); }
  for (const m of parseWebhook(body).slice(0, 20)) {
    try { await handle(m); } catch (e) { console.error("tip handling failed", (e as Error).message); }   // never log the phone number
  }
  return NextResponse.json({ ok: true });   // always 200 so Meta doesn't retry endlessly
}
