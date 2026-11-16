"use client";

import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, Map as MLMap } from "maplibre-gl";
import { useCallback, useEffect, useRef, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { BASEMAPS, GHANA_BOUNDS, type BasemapKey } from "./basemaps";
import { LAYERS, type LayerDef, type LayerKey } from "./layers";

// MapLibre's web worker is served from /public (copied there by `npm run copy-worker`),
// because bundlers rewrite the library's own worker path.
maplibregl.setWorkerUrl("/maplibre-gl-worker.mjs");

type FC = GeoJSON.FeatureCollection;
type LayerMeta = { count: number; source?: string; verified?: boolean };
type Status = "loading" | "ready" | "empty" | "error";

const REGIONS = ["Ashanti", "Western", "Western North", "Eastern", "Central", "Bono", "Bono East", "Ahafo",
  "Greater Accra", "Volta", "Oti", "Northern", "Savannah", "North East", "Upper East", "Upper West"];
const SOURCES: [string, string][] = [["", "All sources"], ["gfw_radd", "RADD"], ["gfw_glad_s2", "GLAD-S2"],
  ["gfw_glad_l", "GLAD-L"], ["gfw_dist", "DIST-ALERT"], ["s2_change", "Sentinel-2"], ["s1_change", "Sentinel-1"],
  ["s2_turbidity", "Turbidity"], ["gov_equipment", "Equipment GPS"], ["gov_forestry", "Forestry Comm."], ["gov_cersgis", "CERSGIS"]];
const LEVEL_COLOUR = ["match", ["get", "level"], "red", "#ef4444", "amber", "#f59e0b", "#9ca3af"];

type Filters = { days: number; levels: string[]; statuses: string[]; region: string; source: string };
const DEFAULT_FILTERS: Filters = { days: 30, levels: ["red", "amber", "watch"], statuses: ["new", "reviewing", "confirmed", "field_verified"], region: "", source: "" };

async function fetchLayer(key: LayerKey, demo: boolean): Promise<FC> {
  if (demo) {
    const res = await fetch(`/demo/${key}.geojson`);
    if (res.status === 404) return { type: "FeatureCollection", features: [] };
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }
  const supabase = createClient();
  const { data, error } =
    key === "tiles" ? await supabase.rpc("tiles_geojson")
    : key === "tips" ? await supabase.rpc("tips_geojson")
    : await supabase.rpc("layer_geojson", { layer: key });
  if (error) throw new Error(error.message);
  return data as FC;
}

async function fetchAlerts(f: Filters, demo: boolean): Promise<FC> {
  if (demo) return { type: "FeatureCollection", features: [] };
  const from = new Date(Date.now() - f.days * 86_400_000).toISOString().slice(0, 10);
  const { data, error } = await createClient().rpc("alerts_geojson", {
    p_from: from, p_to: new Date().toISOString().slice(0, 10),
    p_levels: f.levels.length ? f.levels : null, p_statuses: f.statuses.length ? f.statuses : null,
    p_region: f.region || null, p_source: f.source || null,
  });
  if (error) throw new Error(error.message);
  return data as FC;
}

function addRefLayer(map: MLMap, def: LayerDef, data: FC) {
  const id = `ref-${def.key}`;
  if (map.getSource(id)) { (map.getSource(id) as GeoJSONSource).setData(data); return; }
  map.addSource(id, { type: "geojson", data });
  if (def.key === "tips") {
    map.addLayer({ id: `${id}-line`, type: "circle", source: id,
      paint: { "circle-radius": 5, "circle-color": def.color, "circle-stroke-color": "#fff", "circle-stroke-width": 1 } });
    return;
  }
  if (def.kind === "fill") map.addLayer({ id: `${id}-fill`, type: "fill", source: id, paint: { "fill-color": def.color, "fill-opacity": def.fillOpacity ?? 0.2 } });
  if (def.key === "tiles") map.addLayer({ id: `${id}-fill`, type: "fill", source: id,
    paint: { "fill-color": "#22d3ee", "fill-opacity": ["case", ["==", ["get", "priority"], "high"], 0.1, 0] } });
  map.addLayer({ id: `${id}-line`, type: "line", source: id,
    paint: { "line-color": def.color, "line-width": def.width ?? 1, "line-opacity": 0.9, ...(def.dashed ? { "line-dasharray": [2, 1.5] } : {}) } });
}

function setAlerts(map: MLMap, data: FC) {
  if (map.getSource("alerts")) { (map.getSource("alerts") as GeoJSONSource).setData(data); return; }
  map.addSource("alerts", { type: "geojson", data });
  // Polygons when zoomed in; a dot marks each alert at national scale so small sites stay visible.
  map.addLayer({ id: "alerts-fill", type: "fill", source: "alerts", paint: { "fill-color": LEVEL_COLOUR as never, "fill-opacity": 0.45 } });
  map.addLayer({ id: "alerts-line", type: "line", source: "alerts", paint: { "line-color": LEVEL_COLOUR as never, "line-width": 2 } });
  map.addLayer({ id: "alerts-dot", type: "circle", source: "alerts", maxzoom: 12,
    paint: { "circle-radius": ["interpolate", ["linear"], ["get", "score"], 30, 4, 100, 8], "circle-color": LEVEL_COLOUR as never,
             "circle-stroke-color": "#0a0f0d", "circle-stroke-width": 1.5 } });
}

export default function MapView({ demo, staff = false }: { demo: boolean; staff?: boolean }) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const cache = useRef<Partial<Record<LayerKey, FC>>>({});
  const alertsRef = useRef<FC | null>(null);
  const [ready, setReady] = useState(false);
  const [basemap, setBasemap] = useState<BasemapKey>("satellite");
  const layers = LAYERS.filter((l) => !l.staffOnly || staff);
  const [on, setOn] = useState<Record<string, boolean>>(() => Object.fromEntries(LAYERS.map((l) => [l.key, l.defaultOn])));
  const [status, setStatus] = useState<Partial<Record<LayerKey, Status>>>({});
  const [meta, setMeta] = useState<Partial<Record<LayerKey, LayerMeta>>>({});
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [alertInfo, setAlertInfo] = useState<{ n: number; state: "loading" | "ready" | "error" }>({ n: 0, state: "loading" });
  const [panelOpen, setPanelOpen] = useState(() => typeof window === "undefined" || window.innerWidth >= 640);

  useEffect(() => {
    if (!container.current) return;
    const map = new maplibregl.Map({
      container: container.current, style: BASEMAPS.satellite, bounds: GHANA_BOUNDS,
      fitBoundsOptions: { padding: 24 }, attributionControl: { compact: true }, maxBounds: [[-8, 2], [6, 14]],
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.GeolocateControl({ positionOptions: { enableHighAccuracy: true } }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
    map.on("load", () => setReady(true));
    map.on("click", (e) => {
      const hits = map.queryRenderedFeatures(e.point);
      const hit = hits.find((f) => f.layer.id.startsWith("alerts-")) ?? hits.find((f) => f.layer.id.startsWith("ref-"));
      if (!hit) return;
      const p = hit.properties as Record<string, unknown>;
      // Build the popup with DOM nodes, never innerHTML: names come from external data.
      const el = document.createElement("div");
      const title = document.createElement("strong");
      const sub = document.createElement("div");
      sub.style.cssText = "font-size:12px;opacity:.8;margin-top:2px";
      if (hit.layer.id.startsWith("alerts-")) {
        title.textContent = `${String(p.level).toUpperCase()} #${p.id} · score ${p.score}`;
        sub.textContent = `${Number(p.area_ha).toFixed(1)} ha · ${p.district ?? ""} ${p.region ?? ""} · ${p.status}`;
        const a = document.createElement("a");
        a.href = `/alerts/${Number(p.id)}`;
        a.textContent = "Open alert →";
        a.style.cssText = "display:block;margin-top:6px;color:#7fd1ae";
        el.append(title, sub, a);
      } else {
        title.textContent = String(p.name ?? p.reference_code ?? p.id ?? "Feature");
        sub.textContent = p.source ? `${p.source}${p.verified ? " · verified" : ""}` : p.priority ? `Priority: ${p.priority}` : String(p.status ?? "");
        el.append(title, sub);
      }
      new maplibregl.Popup({ maxWidth: "280px" }).setLngLat(e.lngLat).setDOMContent(el).addTo(map);
    });
    for (const id of ["alerts-fill", "alerts-dot"]) {
      map.on("mouseenter", id, () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", id, () => { map.getCanvas().style.cursor = ""; });
    }
    mapRef.current = map;
    return () => { map.remove(); mapRef.current = null; };
  }, []);

  const ensureLayer = useCallback(async (def: LayerDef) => {
    let data = cache.current[def.key];
    if (!data) {
      setStatus((s) => ({ ...s, [def.key]: "loading" }));
      try {
        data = await fetchLayer(def.key, demo);
        cache.current[def.key] = data;
        const p = (data.features[0]?.properties ?? {}) as Record<string, unknown>;
        setMeta((m) => ({ ...m, [def.key]: { count: data!.features.length, source: p.source as string | undefined, verified: p.verified as boolean | undefined } }));
        setStatus((s) => ({ ...s, [def.key]: data!.features.length ? "ready" : "empty" }));
      } catch {
        setStatus((s) => ({ ...s, [def.key]: "error" }));
        return;
      }
    }
    if (mapRef.current) addRefLayer(mapRef.current, def, data);
  }, [demo]);

  const applyVisibility = useCallback(async () => {
    const map = mapRef.current;
    if (!map) return;
    for (const def of [...layers].reverse()) {
      if (on[def.key]) await ensureLayer(def);
      for (const suffix of ["-fill", "-line"]) {
        const id = `ref-${def.key}${suffix}`;
        if (map.getLayer(id)) { map.setLayoutProperty(id, "visibility", on[def.key] ? "visible" : "none"); map.moveLayer(id); }
      }
    }
    if (alertsRef.current) setAlerts(map, alertsRef.current);
    for (const id of ["alerts-fill", "alerts-line", "alerts-dot"]) if (map.getLayer(id)) map.moveLayer(id);   // alerts always on top
  }, [layers, on, ensureLayer]);

  useEffect(() => { if (ready) applyVisibility(); }, [ready, applyVisibility]);

  // Alerts reload whenever a filter changes.
  useEffect(() => {
    if (!ready) return;
    let stale = false;
    fetchAlerts(filters, demo).then((fc) => {
      if (stale || !mapRef.current) return;
      alertsRef.current = fc;
      setAlerts(mapRef.current, fc);
      setAlertInfo({ n: fc.features.length, state: "ready" });
    }).catch(() => !stale && setAlertInfo({ n: 0, state: "error" }));
    return () => { stale = true; };
  }, [filters, ready, demo]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    map.setStyle(BASEMAPS[basemap]);
    map.once("style.load", () => applyVisibility());
  }, [basemap]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  return (
    <div className="relative h-full w-full">
      {/* maplibre sets position:relative on this element, so size it with h-full, not absolute insets */}
      <div ref={container} className="h-full w-full" aria-label="Map of Ghana" role="region" />

      <button onClick={() => setPanelOpen((v) => !v)} aria-expanded={panelOpen} aria-controls="layer-panel"
        className="absolute left-3 top-3 z-10 rounded-lg border border-ink-700 bg-ink-900/95 px-3 py-2 text-sm font-medium text-white shadow-lg sm:hidden">
        {panelOpen ? "Hide panel" : "Filters & layers"}
      </button>

      <aside id="layer-panel"
        className={`${panelOpen ? "block" : "hidden"} absolute inset-x-3 bottom-3 z-10 max-h-[60dvh] overflow-y-auto rounded-xl border border-ink-700 bg-ink-900/95 p-4 shadow-2xl backdrop-blur sm:bottom-auto sm:left-3 sm:right-auto sm:top-3 sm:block sm:max-h-[calc(100dvh-7rem)] sm:w-80`}>
        <h2 className="text-sm font-semibold uppercase tracking-wider text-ink-300">Alerts</h2>
        <p className="mt-0.5 text-xs text-ink-400" aria-live="polite">
          {demo ? "Demo: no alerts without the database" : alertInfo.state === "loading" ? "Loading…" : alertInfo.state === "error" ? "Could not load alerts" : `${alertInfo.n} shown`}
        </p>
        <div className="mt-3 space-y-3 text-sm">
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="Alert level">
            {(["red", "amber", "watch"] as const).map((l) => (
              <button key={l} aria-pressed={filters.levels.includes(l)} onClick={() => setFilters((f) => ({ ...f, levels: toggle(f.levels, l) }))}
                className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs capitalize ${filters.levels.includes(l) ? "border-ink-500 bg-ink-800 text-white" : "border-ink-700 text-ink-400"}`}>
                <i className={`h-2.5 w-2.5 rounded-full ${l === "red" ? "bg-alert-red" : l === "amber" ? "bg-alert-amber" : "bg-alert-watch"}`} />{l}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs text-ink-300">Period
              <select value={filters.days} onChange={(e) => setFilters((f) => ({ ...f, days: Number(e.target.value) }))}
                className="mt-1 w-full rounded-md border border-ink-600 bg-ink-950 px-2 py-1.5 text-sm text-white">
                {[[7, "7 days"], [30, "30 days"], [90, "90 days"], [365, "1 year"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
            <label className="text-xs text-ink-300">Region
              <select value={filters.region} onChange={(e) => setFilters((f) => ({ ...f, region: e.target.value }))}
                className="mt-1 w-full rounded-md border border-ink-600 bg-ink-950 px-2 py-1.5 text-sm text-white">
                <option value="">All</option>{REGIONS.map((r) => <option key={r}>{r}</option>)}
              </select>
            </label>
            <label className="text-xs text-ink-300">Source
              <select value={filters.source} onChange={(e) => setFilters((f) => ({ ...f, source: e.target.value }))}
                className="mt-1 w-full rounded-md border border-ink-600 bg-ink-950 px-2 py-1.5 text-sm text-white">
                {SOURCES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
            <label className="text-xs text-ink-300">Status
              <select value={filters.statuses.length === 1 ? filters.statuses[0] : ""} onChange={(e) =>
                setFilters((f) => ({ ...f, statuses: e.target.value ? [e.target.value] : DEFAULT_FILTERS.statuses }))}
                className="mt-1 w-full rounded-md border border-ink-600 bg-ink-950 px-2 py-1.5 text-sm text-white">
                <option value="">Open</option>
                {["new", "reviewing", "confirmed", "rejected", "field_verified", "closed"].map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
              </select>
            </label>
          </div>
        </div>

        <div className="mt-4 flex items-center justify-between gap-2 border-t border-ink-700 pt-3">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-ink-300">Layers</h2>
          <div className="flex rounded-lg border border-ink-700 p-0.5 text-xs" role="group" aria-label="Basemap">
            {(["satellite", "streets"] as const).map((b) => (
              <button key={b} onClick={() => setBasemap(b)} aria-pressed={basemap === b}
                className={`rounded-md px-2.5 py-1.5 capitalize ${basemap === b ? "bg-brand-500 text-white" : "text-ink-300 hover:text-white"}`}>{b}</button>
            ))}
          </div>
        </div>
        <ul className="mt-2 space-y-0.5">
          {layers.map((def) => {
            const st = status[def.key];
            const m = meta[def.key];
            return (
              <li key={def.key}>
                <label className="flex cursor-pointer items-start gap-3 rounded-lg px-2 py-1.5 hover:bg-ink-800">
                  <input type="checkbox" checked={on[def.key]} onChange={(e) => setOn((o) => ({ ...o, [def.key]: e.target.checked }))}
                    className="mt-1 h-4 w-4 accent-[var(--color-brand-500)]" />
                  <span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: def.swatch }} aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-medium text-white">{def.label}</span>
                    <span className="block text-xs text-ink-400">
                      {st === "loading" && "Loading…"}
                      {st === "error" && <span className="text-alert-red">Could not load</span>}
                      {st === "empty" && "Not loaded yet"}
                      {st === "ready" && m && (<>{m.count.toLocaleString()} features
                        {m.source && <> · <span title={m.source}>{m.source.split(/[ (]/)[0].replace(/:$/, "")}</span></>}
                        {m.verified ? <span className="ml-1 rounded bg-brand-600/40 px-1 text-brand-300">verified</span> : null}</>)}
                      {!st && def.hint}
                    </span>
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      </aside>
    </div>
  );
}
