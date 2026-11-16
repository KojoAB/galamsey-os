// Reference layers shown on the map. `key` matches reference_layer_type in the database.
// Colours: alerts own red/amber/grey, so reference layers use green, blue, violet and cyan.

export type LayerKey =
  | "admin1" | "admin2" | "forest_reserve" | "river" | "river_buffer_100m"
  | "river_buffer_500m" | "concession" | "priority_zone" | "tiles";

export type LayerDef = {
  key: LayerKey;
  label: string;
  hint: string;
  swatch: string;
  defaultOn: boolean;
  kind: "line" | "fill";
  color: string;
  fillOpacity?: number;
  dashed?: boolean;
  width?: number;
};

export const LAYERS: LayerDef[] = [
  { key: "priority_zone", label: "Mining-belt priority zones", hint: "Processed first, every day", swatch: "#22d3ee", defaultOn: true, kind: "line", color: "#22d3ee", dashed: true, width: 2 },
  { key: "forest_reserve", label: "Forest reserves", hint: "Protected forest", swatch: "#3fb588", defaultOn: true, kind: "fill", color: "#3fb588", fillOpacity: 0.22, width: 1 },
  { key: "river_buffer_500m", label: "River buffer 500 m", hint: "Major rivers", swatch: "#60a5fa", defaultOn: false, kind: "fill", color: "#60a5fa", fillOpacity: 0.12 },
  { key: "river_buffer_100m", label: "River buffer 100 m", hint: "Highest-risk zone", swatch: "#3b82f6", defaultOn: true, kind: "fill", color: "#3b82f6", fillOpacity: 0.35 },
  { key: "river", label: "Major rivers", hint: "Pra, Ankobra, Offin, Birim, Tano, Densu…", swatch: "#93c5fd", defaultOn: true, kind: "line", color: "#93c5fd", width: 1.5 },
  { key: "concession", label: "Licensed concessions", hint: "Minerals Commission", swatch: "#a78bfa", defaultOn: true, kind: "fill", color: "#a78bfa", fillOpacity: 0.18, width: 1 },
  { key: "admin1", label: "Regions", hint: "16 regions", swatch: "#e5e7eb", defaultOn: true, kind: "line", color: "#e5e7eb", width: 1.2 },
  { key: "admin2", label: "Districts", hint: "District boundaries", swatch: "#9ca3af", defaultOn: false, kind: "line", color: "#9ca3af", width: 0.5 },
  { key: "tiles", label: "Processing tiles (10 km)", hint: "Highlighted = high priority", swatch: "#7f8f89", defaultOn: false, kind: "line", color: "#7f8f89", width: 0.4 },
];
