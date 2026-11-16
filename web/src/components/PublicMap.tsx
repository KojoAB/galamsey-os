"use client";

import * as maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { BASEMAPS, GHANA_BOUNDS } from "./basemaps";

maplibregl.setWorkerUrl("/maplibre-gl-worker.mjs");

// Public, no login: confirmed sites only, delayed (see public_sites_geojson in the database).
export default function PublicMap({ demo }: { demo: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const [info, setInfo] = useState<{ sites: number; ha: number } | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!ref.current) return;
    const map = new maplibregl.Map({ container: ref.current, style: BASEMAPS.satellite, bounds: GHANA_BOUNDS,
      fitBoundsOptions: { padding: 24 }, attributionControl: { compact: true } });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.on("load", async () => {
      let fc: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
      if (!demo) {
        const { data, error } = await createClient().rpc("public_sites_geojson");
        if (error) { setError(true); return; }
        fc = data as GeoJSON.FeatureCollection;
      }
      setInfo({ sites: fc.features.length, ha: fc.features.reduce((s, f) => s + Number(f.properties?.area_ha ?? 0), 0) });
      map.addSource("sites", { type: "geojson", data: fc });
      map.addLayer({ id: "sites-fill", type: "fill", source: "sites", paint: { "fill-color": "#ef4444", "fill-opacity": 0.5 } });
      map.addLayer({ id: "sites-dot", type: "circle", source: "sites", maxzoom: 12,
        paint: { "circle-radius": 5, "circle-color": "#ef4444", "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });
      map.on("click", (e) => {
        const f = map.queryRenderedFeatures(e.point, { layers: ["sites-fill", "sites-dot"] })[0];
        if (!f) return;
        const p = f.properties as Record<string, unknown>;
        const el = document.createElement("div");
        el.textContent = `${Number(p.area_ha).toFixed(1)} ha · ${p.district ?? ""}, ${p.region ?? ""} · first seen ${String(p.first_seen).slice(0, 7)}` +
          (p.forest_reserve_name ? ` · ${p.forest_reserve_name}` : "") + (p.river_name ? ` · near ${p.river_name}` : "");
        new maplibregl.Popup().setLngLat(e.lngLat).setDOMContent(el).addTo(map);
      });
    });
    return () => map.remove();
  }, [demo]);

  return (
    <div className="relative h-full w-full">
      <div ref={ref} className="h-full w-full" role="region" aria-label="Public map of confirmed mining sites" />
      <div className="absolute left-3 top-3 z-10 max-w-xs rounded-xl border border-ink-700 bg-ink-900/95 p-4 text-sm shadow-xl">
        <h1 className="text-base font-semibold text-white">Confirmed illegal mining sites</h1>
        <p className="mt-1 text-xs text-ink-300">Sites reviewed and confirmed by our analysts. Shown after a delay and without exact dates.</p>
        {error ? <p className="mt-3 text-alert-red">The map could not load right now.</p> : info && (
          <dl className="mt-3 grid grid-cols-2 gap-2">
            <div><dt className="text-xs text-ink-400">Sites</dt><dd className="text-2xl font-semibold text-white">{info.sites.toLocaleString()}</dd></div>
            <div><dt className="text-xs text-ink-400">Hectares</dt><dd className="text-2xl font-semibold text-white">{Math.round(info.ha).toLocaleString()}</dd></div>
          </dl>
        )}
        {demo && <p className="mt-2 text-xs text-alert-amber">Demo mode — no data connected.</p>}
      </div>
    </div>
  );
}
