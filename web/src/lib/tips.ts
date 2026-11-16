import { createHmac, randomInt, timingSafeEqual } from "node:crypto";

// Pure helpers for the WhatsApp tip line, kept separate so they can be unit-tested.

/** Salted HMAC of the sender's number. The raw number is never stored or logged. */
export function hashPhone(phone: string, salt: string): string {
  if (!salt || salt.length < 16) throw new Error("TIP_HASH_SALT must be set (16+ random characters)");
  return createHmac("sha256", salt).update(phone.replace(/\D/g, "")).digest("hex");
}

/** Meta signs every webhook body with the app secret: header X-Hub-Signature-256: sha256=<hex>. */
export function verifySignature(rawBody: string, header: string | null, appSecret: string): boolean {
  if (!header?.startsWith("sha256=") || !appSecret) return false;
  const expected = Buffer.from(createHmac("sha256", appSecret).update(rawBody).digest("hex"));
  const got = Buffer.from(header.slice(7));
  return got.length === expected.length && timingSafeEqual(got, expected);
}

const ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";   // no 0/O/1/I confusion
export function referenceCode(): string {
  let s = "";
  for (let i = 0; i < 6; i++) s += ALPHABET[randomInt(ALPHABET.length)];
  return `GW-${s}`;
}

/** Simple spam filter: link-only messages, very long messages, or repeated characters. */
export function isSpam(text: string | undefined): boolean {
  if (!text) return false;
  const t = text.trim();
  if (t.length > 3000) return true;
  const links = t.match(/https?:\/\/\S+/g) ?? [];
  if (links.length >= 3) return true;
  if (links.length && t.replace(/https?:\/\/\S+/g, "").trim().length < 10) return true;
  if (/(.)\1{30,}/.test(t)) return true;
  return false;
}

export type IncomingMessage = {
  from: string; id: string; timestamp: string;
  type: "text" | "image" | "location" | "video" | "audio" | "document" | string;
  text?: string; mediaId?: string; caption?: string; lat?: number; lon?: number;
};

/** Flatten Meta's webhook payload into simple messages. Unknown shapes are ignored. */
export function parseWebhook(body: unknown): IncomingMessage[] {
  const out: IncomingMessage[] = [];
  const entries = (body as { entry?: unknown[] })?.entry ?? [];
  for (const e of entries as { changes?: { value?: { messages?: Record<string, unknown>[] } }[] }[]) {
    for (const ch of e.changes ?? []) {
      for (const m of ch.value?.messages ?? []) {
        const msg = m as Record<string, Record<string, unknown> | string>;
        const base = { from: String(msg.from), id: String(msg.id), timestamp: String(msg.timestamp), type: String(msg.type) };
        const t = msg.type as string;
        if (t === "text") out.push({ ...base, text: String((msg.text as Record<string, unknown>)?.body ?? "") });
        else if (t === "image" || t === "video") out.push({ ...base, mediaId: String((msg[t] as Record<string, unknown>)?.id), caption: (msg[t] as Record<string, unknown>)?.caption as string | undefined });
        else if (t === "location") {
          const l = msg.location as Record<string, unknown>;
          out.push({ ...base, lat: Number(l?.latitude), lon: Number(l?.longitude) });
        } else out.push(base);
      }
    }
  }
  return out;
}

/** Only accept locations inside (a generous box around) Ghana. */
export function inGhana(lat?: number, lon?: number): boolean {
  return lat != null && lon != null && Number.isFinite(lat) && Number.isFinite(lon) && lat > 4.5 && lat < 11.3 && lon > -3.4 && lon < 1.4;
}
