"use client";

import { useCallback, useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";

// Offline-first field reports. Each report (with photos) is saved in the phone's
// IndexedDB first, then uploaded when there is a connection. Retries are safe:
// the same client_id never creates two reports.
type Queued = { client_id: string; alert_id: number | null; notes: string; lon: number | null; lat: number | null; visited_at: string; photos: Blob[] };

const DB = "gw-field", STORE = "queue";
function idb(): Promise<IDBDatabase> {
  return new Promise((res, rej) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE, { keyPath: "client_id" });
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
}
async function tx<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await idb();
  return new Promise((res, rej) => { const req = fn(db.transaction(STORE, mode).objectStore(STORE)); req.onsuccess = () => res(req.result); req.onerror = () => rej(req.error); });
}

// Shrink photos to ≤1600 px JPEG so they upload on weak connections.
async function compress(file: File): Promise<Blob> {
  const bmp = await createImageBitmap(file);
  const scale = Math.min(1, 1600 / Math.max(bmp.width, bmp.height));
  const c = document.createElement("canvas");
  c.width = Math.round(bmp.width * scale); c.height = Math.round(bmp.height * scale);
  c.getContext("2d")!.drawImage(bmp, 0, 0, c.width, c.height);
  return new Promise((res) => c.toBlob((b) => res(b ?? file), "image/jpeg", 0.8));
}

export default function FieldReport({ userId, alerts, defaultAlert }: { userId: string; alerts: { id: number; label: string }[]; defaultAlert?: number }) {
  const [alertId, setAlertId] = useState<number | "">(defaultAlert ?? "");
  const [notes, setNotes] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [gps, setGps] = useState<{ lat: number; lon: number; acc: number } | null>(null);
  const [queued, setQueued] = useState(0);
  const [msg, setMsg] = useState("");

  const refreshCount = useCallback(async () => setQueued(await tx("readonly", (s) => s.count())), []);

  const sync = useCallback(async () => {
    if (!navigator.onLine) { await refreshCount(); return; }
    const supabase = createClient();
    const items = await tx<Queued[]>("readonly", (s) => s.getAll() as IDBRequest<Queued[]>);
    for (const item of items) {
      try {
        const paths: string[] = [];
        for (let i = 0; i < item.photos.length; i++) {
          const path = `${userId}/${item.client_id}-${i}.jpg`;
          const { error } = await supabase.storage.from("field-photos").upload(path, item.photos[i], { contentType: "image/jpeg", upsert: true });
          if (error) throw error;
          paths.push(path);
        }
        const { error } = await supabase.rpc("add_field_report", { p_client_id: item.client_id, p_alert: item.alert_id, p_photos: paths,
          p_notes: item.notes, p_lon: item.lon, p_lat: item.lat, p_visited_at: item.visited_at });
        if (error) throw error;
        await tx("readwrite", (s) => s.delete(item.client_id));
        setMsg("Report uploaded.");
      } catch {
        setMsg("Saved on this phone. Will upload when the connection is better.");
        break;
      }
    }
    refreshCount();
  }, [userId, refreshCount]);

  useEffect(() => {
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
    const first = setTimeout(sync, 0);          // upload anything left from last time
    window.addEventListener("online", sync);
    return () => { clearTimeout(first); window.removeEventListener("online", sync); };
  }, [sync, refreshCount]);

  function locate() {
    if (!navigator.geolocation) { setMsg("This phone cannot share its location."); return; }
    setMsg("Getting GPS…");
    navigator.geolocation.getCurrentPosition(
      (p) => { setGps({ lat: p.coords.latitude, lon: p.coords.longitude, acc: Math.round(p.coords.accuracy) }); setMsg(""); },
      () => setMsg("Could not get GPS. Check location permission."), { enableHighAccuracy: true, timeout: 20000 });
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const photos = await Promise.all(files.map(compress));
    const item: Queued = { client_id: crypto.randomUUID(), alert_id: alertId === "" ? null : alertId, notes, lon: gps?.lon ?? null, lat: gps?.lat ?? null, visited_at: new Date().toISOString(), photos };
    await tx("readwrite", (s) => s.put(item));
    setNotes(""); setFiles([]); setMsg("Saved. Uploading…");
    await refreshCount();
    sync();
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <label className="block text-sm text-ink-300">Alert
        <select value={alertId} onChange={(e) => setAlertId(e.target.value ? Number(e.target.value) : "")}
          className="mt-1 w-full rounded-lg border border-ink-600 bg-ink-950 px-3 py-3 text-base text-white">
          <option value="">Not linked to an alert</option>
          {alerts.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
        </select>
      </label>
      <div>
        <button type="button" onClick={locate} className="w-full rounded-lg border border-ink-600 px-3 py-3 text-base text-white hover:bg-ink-800">
          {gps ? `GPS: ${gps.lat.toFixed(5)}, ${gps.lon.toFixed(5)} (±${gps.acc} m)` : "Record my GPS position"}
        </button>
      </div>
      <label className="block text-sm text-ink-300">Photos
        <input type="file" accept="image/*" capture="environment" multiple onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          className="mt-1 block w-full text-base text-ink-200 file:mr-3 file:rounded-lg file:border-0 file:bg-ink-700 file:px-3 file:py-2.5 file:text-white" />
        {files.length > 0 && <span className="mt-1 block text-xs text-ink-400">{files.length} photo(s) selected</span>}
      </label>
      <label className="block text-sm text-ink-300">What did you see?
        <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={4} maxLength={4000}
          className="mt-1 w-full rounded-lg border border-ink-600 bg-ink-950 p-3 text-base text-white" />
      </label>
      <button className="w-full rounded-lg bg-brand-500 px-4 py-3.5 text-base font-semibold text-white hover:bg-brand-400">Save report</button>
      <p className="text-sm text-ink-300" aria-live="polite">{msg}{queued > 0 ? ` · ${queued} waiting to upload` : ""}</p>
    </form>
  );
}
