"use client";

import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, Map as MLMap, StyleSpecification } from "maplibre-gl";
import { useCallback, useEffect, useRef, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { LAYERS, type LayerDef, type LayerKey } from "./layers";

type FC = GeoJSON.FeatureCollection;
type LayerMeta = { count: number; source?: string; verified?: boolean; retrievedAt?: string };
type Status = "idle" | "loading" | "ready" | "empty" | "error";

// MapLibre's web worker is served from /public (copied there by `npm run copy-worker`),
// because bundlers rewrite the library's own worker path.
maplibregl.setWorkerUrl("/maplibre-gl-worker.mjs");

const GHANA_BOUNDS: [[number, number], [number, number]] = [[-3.35, 4.6], [1.3, 11.25]];

// Two free basemaps. Satellite: Esri World Imagery. Streets: OpenStreetMap, darkened to match the UI.
const BASEMAPS: Record<"satellite" | "streets", StyleSpecification> = {
  satellite: {
    version: 8,
    sources: {
      base: {
        type: "raster", tileSize: 256, maxzoom: 19,
        tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
        attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
      },
    },
    layers: [{ id: "base", type: "raster", source: "base", paint: { "raster-brightness-max": 0.8 } }],
  },
  streets: {
    version: 8,
    sources: {
      base: {
        type: "raster", tileSize: 256, maxzoom: 19,
        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        attribution: "© OpenStreetMap contributors",
      },
    },
    layers: [{
      id: "base", type: "raster", source: "base",
      paint: { "raster-saturation": -0.85, "raster-brightness-max": 0.45, "raster-contrast": 0.1 },
    }],
  },
};

async function fetchLayer(key: LayerKey, demo: boolean): Promise<FC> {
  if (demo) {
    const res = await fetch(`/demo/${key}.geojson`);
    if (res.status === 404) return { type: "FeatureCollection", features: [] };
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }
  const supabase = createClient();
  const { data, error } = key === "tiles"
    ? await supabase.rpc("tiles_geojson")
    : await supabase.rpc("layer_geojson", { layer: key });
  if (error) throw new Error(error.message);
  return data as FC;
}

function addLayerToMap(map: MLMap, def: LayerDef, data: FC) {
  const id = `ref-${def.key}`;
  if (map.getSource(id)) {
    (map.getSource(id) as GeoJSONSource).setData(data);
    return;
  }
  map.addSource(id, { type: "geojson", data });
  if (def.kind === "fill") {
    map.addLayer({ id: `${id}-fill`, type: "fill", source: id, paint: { "fill-color": def.color, "fill-opacity": def.fillOpacity ?? 0.2 } });
  }
  if (def.key === "tiles") {
    map.addLayer({
      id: `${id}-fill`, type: "fill", source: id,
      paint: { "fill-color": "#22d3ee", "fill-opacity": ["case", ["==", ["get", "priority"], "high"], 0.1, 0] },
    });
  }
  map.addLayer({
    id: `${id}-line`, type: "line", source: id,
    paint: {
      "line-color": def.color, "line-width": def.width ?? 1, "line-opacity": 0.9,
      ...(def.dashed ? { "line-dasharray": [2, 1.5] } : {}),
    },
  });
}

export default function MapView({ demo }: { demo: boolean }) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const cache = useRef<Partial<Record<LayerKey, FC>>>({});
  const [ready, setReady] = useState(false);
  const [basemap, setBasemap] = useState<"satellite" | "streets">("satellite");
  const [on, setOn] = useState<Record<LayerKey, boolean>>(
    () => Object.fromEntries(LAYERS.map((l) => [l.key, l.defaultOn])) as Record<LayerKey, boolean>,
  );
  const [status, setStatus] = useState<Partial<Record<LayerKey, Status>>>({});
  const [meta, setMeta] = useState<Partial<Record<LayerKey, LayerMeta>>>({});
  // Panel starts open on desktop, closed on phones so the map is visible first.
  const [panelOpen, setPanelOpen] = useState(() => typeof window === "undefined" || window.innerWidth >= 640);

  // Create the map once.
  useEffect(() => {
    if (!container.current) return;
    const map = new maplibregl.Map({
      container: container.current,
      style: BASEMAPS.satellite,
      bounds: GHANA_BOUNDS,
      fitBoundsOptions: { padding: 24 },
      attributionControl: { compact: true },
      maxBounds: [[-8, 2], [6, 14]],
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
    map.on("load", () => setReady(true));
    map.on("click", (e) => {
      const hit = map.queryRenderedFeatures(e.point).find((f) => f.layer.id.startsWith("ref-"));
      if (!hit) return;
      const p = hit.properties as Record<string, unknown>;
      const title = String(p.name ?? p.id ?? "Feature");
      const sub = p.source ? `${p.source}${p.verified ? " · verified" : ""}` : p.priority ? `Priority: ${p.priority}` : "";
      const el = document.createElement("div");
      const strong = document.createElement("strong");
      strong.textContent = title;           // textContent, not innerHTML: names come from external data
      const small = document.createElement("div");
      small.style.cssText = "font-size:12px;opacity:.75;margin-top:2px";
      small.textContent = sub;
      el.append(strong, small);
      new maplibregl.Popup({ closeButton: true, maxWidth: "260px" }).setLngLat(e.lngLat).setDOMContent(el).addTo(map);
    });
    mapRef.current = map;
    return () => { map.remove(); mapRef.current = null; };
  }, []);

  const ensureLayer = useCallback(async (def: LayerDef) => {
    const map = mapRef.current;
    if (!map) return;
    let data = cache.current[def.key];
    if (!data) {
      setStatus((s) => ({ ...s, [def.key]: "loading" }));
      try {
        data = await fetchLayer(def.key, demo);
        cache.current[def.key] = data;
        const p = (data.features[0]?.properties ?? {}) as Record<string, unknown>;
        setMeta((m) => ({ ...m, [def.key]: {
          count: data!.features.length,
          source: p.source as string | undefined,
          verified: p.verified as boolean | undefined,
          retrievedAt: p.retrieved_at as string | undefined,
        } }));
        setStatus((s) => ({ ...s, [def.key]: data!.features.length ? "ready" : "empty" }));
      } catch {
        setStatus((s) => ({ ...s, [def.key]: "error" }));
        return;
      }
    }
    if (!mapRef.current) return;
    addLayerToMap(mapRef.current, def, data);
  }, [demo]);

  // Show/hide layers. Draw order follows LAYERS from the bottom of the list up.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    (async () => {
      for (const def of [...LAYERS].reverse()) {
        if (on[def.key]) await ensureLayer(def);
        for (const suffix of ["-fill", "-line"]) {
          const id = `ref-${def.key}${suffix}`;
          if (map.getLayer(id)) {
            map.setLayoutProperty(id, "visibility", on[def.key] ? "visible" : "none");
            map.moveLayer(id);
          }
        }
      }
    })();
  }, [on, ready, ensureLayer]);

  // Switching basemap replaces the style; re-add our layers once it has loaded.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    map.setStyle(BASEMAPS[basemap]);
    map.once("style.load", () => setOn((o) => ({ ...o })));
  }, [basemap, ready]);

  return (
    <div className="relative h-full w-full">
      {/* maplibre sets position:relative on this element, so size it with h-full, not absolute insets */}
      <div ref={container} className="h-full w-full" aria-label="Map of Ghana" role="region" />

      <button
        onClick={() => setPanelOpen((v) => !v)}
        aria-expanded={panelOpen}
        aria-controls="layer-panel"
        className="absolute left-3 top-3 z-10 rounded-lg border border-ink-700 bg-ink-900/95 px-3 py-2 text-sm font-medium text-white shadow-lg sm:hidden"
      >
        {panelOpen ? "Hide layers" : "Layers"}
      </button>

      <aside
        id="layer-panel"
        className={`${panelOpen ? "block" : "hidden"} absolute inset-x-3 bottom-3 z-10 max-h-[55dvh] overflow-y-auto rounded-xl border border-ink-700 bg-ink-900/95 p-4 shadow-2xl backdrop-blur sm:bottom-auto sm:left-3 sm:right-auto sm:top-3 sm:block sm:max-h-[calc(100dvh-7rem)] sm:w-80`}
      >
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-ink-300">Layers</h2>
          <div className="flex rounded-lg border border-ink-700 p-0.5 text-xs" role="group" aria-label="Basemap">
            {(["satellite", "streets"] as const).map((b) => (
              <button
                key={b}
                onClick={() => setBasemap(b)}
                aria-pressed={basemap === b}
                className={`rounded-md px-2.5 py-1.5 capitalize ${basemap === b ? "bg-brand-500 text-white" : "text-ink-300 hover:text-white"}`}
              >
                {b}
              </button>
            ))}
          </div>
        </div>

        <ul className="space-y-1">
          {LAYERS.map((def) => {
            const st = status[def.key];
            const m = meta[def.key];
            return (
              <li key={def.key}>
                <label className="flex cursor-pointer items-start gap-3 rounded-lg px-2 py-2 hover:bg-ink-800">
                  <input
                    type="checkbox"
                    checked={on[def.key]}
                    onChange={(e) => setOn((o) => ({ ...o, [def.key]: e.target.checked }))}
                    className="mt-1 h-4 w-4 accent-[var(--color-brand-500)]"
                  />
                  <span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: def.swatch }} aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium text-white">{def.label}</span>
                    <span className="block text-xs text-ink-400">
                      {st === "loading" && "Loading…"}
                      {st === "error" && <span className="text-alert-red">Could not load</span>}
                      {st === "empty" && "Not loaded yet"}
                      {st === "ready" && m && (
                        <>
                          {m.count.toLocaleString()} features
                          {m.source && <> · <span title={m.source}>{m.source.split(/[ (]/)[0].replace(/:$/, "")}</span></>}
                          {m.verified ? <span className="ml-1 rounded bg-brand-600/40 px-1 text-brand-300">verified</span> : null}
                        </>
                      )}
                      {!st && def.hint}
                    </span>
                  </span>
                </label>
              </li>
            );
          })}
        </ul>

        <div className="mt-4 border-t border-ink-700 pt-3">
          <p className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-ink-400">Alert levels (from Milestone 2)</p>
          <div className="flex gap-3 text-xs">
            <span className="flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-full bg-alert-red" />Red</span>
            <span className="flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-full bg-alert-amber" />Amber</span>
            <span className="flex items-center gap-1.5"><i className="h-2.5 w-2.5 rounded-full bg-alert-watch" />Watch</span>
          </div>
        </div>
      </aside>
    </div>
  );
}
