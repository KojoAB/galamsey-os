import { createHmac } from "node:crypto";
import { describe, expect, it } from "vitest";
import { hashPhone, inGhana, isSpam, parseWebhook, referenceCode, verifySignature } from "../tips";

const SALT = "test-salt-0123456789abcdef";

describe("tip line", () => {
  it("hashes phone numbers deterministically and never returns the number", () => {
    const h = hashPhone("+233 24 123 4567", SALT);
    expect(h).toBe(hashPhone("233241234567", SALT));
    expect(h).not.toContain("241234567");
    expect(hashPhone("233241234567", SALT + "x")).not.toBe(h);
    expect(() => hashPhone("233", "short")).toThrow();
  });

  it("verifies Meta webhook signatures", () => {
    const body = '{"entry":[]}';
    const sig = "sha256=" + createHmac("sha256", "secret").update(body).digest("hex");
    expect(verifySignature(body, sig, "secret")).toBe(true);
    expect(verifySignature(body + " ", sig, "secret")).toBe(false);
    expect(verifySignature(body, null, "secret")).toBe(false);
    expect(verifySignature(body, sig, "")).toBe(false);
  });

  it("makes readable reference codes", () => {
    expect(referenceCode()).toMatch(/^GW-[A-HJ-NP-Z2-9]{6}$/);
  });

  it("filters obvious spam but keeps real reports", () => {
    expect(isSpam("They are digging by the Birim river near Kibi, 3 excavators")).toBe(false);
    expect(isSpam("http://a.example http://b.example http://c.example")).toBe(true);
    expect(isSpam("win http://x.example")).toBe(true);
    expect(isSpam("a".repeat(40))).toBe(true);
    expect(isSpam(undefined)).toBe(false);
  });

  it("parses text, image and location messages", () => {
    const body = { entry: [{ changes: [{ value: { messages: [
      { from: "233241234567", id: "m1", timestamp: "1", type: "text", text: { body: "Galamsey here" } },
      { from: "233241234567", id: "m2", timestamp: "2", type: "image", image: { id: "media9", caption: "pit" } },
      { from: "233241234567", id: "m3", timestamp: "3", type: "location", location: { latitude: 6.2, longitude: -0.58 } },
    ] } }] }] };
    const m = parseWebhook(body);
    expect(m.map((x) => x.type)).toEqual(["text", "image", "location"]);
    expect(m[0].text).toBe("Galamsey here");
    expect(m[1].mediaId).toBe("media9");
    expect([m[2].lat, m[2].lon]).toEqual([6.2, -0.58]);
    expect(parseWebhook({ nonsense: true })).toEqual([]);
  });

  it("accepts locations only inside Ghana", () => {
    expect(inGhana(6.2, -0.58)).toBe(true);
    expect(inGhana(51.5, -0.12)).toBe(false);
    expect(inGhana(undefined, undefined)).toBe(false);
  });
});
