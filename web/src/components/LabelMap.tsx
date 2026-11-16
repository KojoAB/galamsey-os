"use client";

import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource } from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { BASEMAPS, GHANA_BOUNDS } from "./basemaps";

maplibregl.setWorkerUrl("/maplibre-gl-worker.mjs");

// 480 m square = one AI model patch (48 × 48 px at 10 m).
function patchAround(lon: number, lat: number): GeoJSON.Polygon {
  const dLat = 240 / 111_320;
  const dLon = 240 / (111_320 * Math.cos((lat * Math.PI) / 180));
  return { type: "Polygon", coordinates: [[[lon - dLon, lat - dLat], [lon + dLon, lat - dLat], [lon + dLon, lat + dLat], [lon - dLon, lat + dLat], [lon - dLon, lat - dLat]]] };
}

export default function LabelMap() {
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [pending, setPending] = useState<GeoJSON.Polygon | null>(null);
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [msg, setMsg] = useState("Click the map where you can see a mine, or a look-alike (sandbar, farm pond).");
  const [count, setCount] = useState<{ mine: number; not_mine: number }>({ mine: 0, not_mine: 0 });

  async function reload() {
    const { data } = await createClient().rpc("labels_geojson");
    const fc = (data ?? { type: "FeatureCollection", features: [] }) as GeoJSON.FeatureCollection;
    setCount({ mine: fc.features.filter((f) => f.properties?.label === "mine").length,
               not_mine: fc.features.filter((f) => f.properties?.label === "not_mine").length });
    (mapRef.current?.getSource("labels") as GeoJSONSource | undefined)?.setData(fc);
  }

  useEffect(() => {
    if (!ref.current) return;
    const map = new maplibregl.Map({ container: ref.current, style: BASEMAPS.satellite, bounds: GHANA_BOUNDS, attributionControl: { compact: true } });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.on("load", () => {
      map.addSource("labels", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "labels-line", type: "line", source: "labels",
        paint: { "line-color": ["match", ["get", "label"], "mine", "#ef4444", "#3fb588"], "line-width": 2 } });
      map.addSource("pending", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "pending-line", type: "line", source: "pending", paint: { "line-color": "#22d3ee", "line-width": 2, "line-dasharray": [2, 1] } });
      reload();
    });
    map.on("click", (e) => {
      const poly = patchAround(e.lngLat.lng, e.lngLat.lat);
      setPending(poly);
      (map.getSource("pending") as GeoJSONSource).setData({ type: "Feature", geometry: poly, properties: {} });
    });
    mapRef.current = map;
    return () => map.remove();
  }, []);

  async function save(label: "mine" | "not_mine") {
    if (!pending) return;
    const { error } = await createClient().rpc("add_label", { p_geojson: JSON.stringify(pending), p_label: label, p_image_date: date });
    setMsg(error ? `Could not save: ${error.message}` : `Saved as ${label === "mine" ? "mine" : "not a mine"}.`);
    if (!error) {
      setPending(null);
      (mapRef.current?.getSource("pending") as GeoJSONSource).setData({ type: "FeatureCollection", features: [] });
      reload();
    }
  }

  return (
    <div className="relative h-full w-full">
      <div ref={ref} className="h-full w-full" role="region" aria-label="Labelling map" />
      <div className="absolute inset-x-3 bottom-3 z-10 rounded-xl border border-ink-700 bg-ink-900/95 p-4 text-sm shadow-xl sm:inset-x-auto sm:left-3 sm:top-3 sm:bottom-auto sm:w-80">
        <h1 className="font-semibold text-white">Training labels</h1>
        <p className="mt-1 text-xs text-ink-300">{count.mine} mines · {count.not_mine} not-mines. Retraining starts at 300 confirmed mines.</p>
        <p className="mt-3 text-ink-200" aria-live="polite">{msg}</p>
        <label className="mt-3 block text-xs text-ink-300">Image date
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="mt-1 w-full rounded-md border border-ink-600 bg-ink-950 px-2 py-1.5 text-white" />
        </label>
        <div className="mt-3 grid grid-cols-2 gap-2">
          <button disabled={!pending} onClick={() => save("mine")} className="rounded-lg bg-alert-red px-3 py-2.5 font-semibold text-white disabled:opacity-40">Mine</button>
          <button disabled={!pending} onClick={() => save("not_mine")} className="rounded-lg bg-brand-500 px-3 py-2.5 font-semibold text-white disabled:opacity-40">Not a mine</button>
        </div>
      </div>
    </div>
  );
}
