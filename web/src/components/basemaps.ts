import type { StyleSpecification } from "maplibre-gl";

export const GHANA_BOUNDS: [[number, number], [number, number]] = [[-3.35, 4.6], [1.3, 11.25]];
export type BasemapKey = "satellite" | "streets";

// Two free basemaps. Satellite: Esri World Imagery. Streets: OpenStreetMap, darkened to match the UI.
export const BASEMAPS: Record<BasemapKey, StyleSpecification> = {
  satellite: {
    version: 8,
    sources: { base: { type: "raster", tileSize: 256, maxzoom: 19,
      tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
      attribution: "Imagery © Esri, Maxar, Earthstar Geographics" } },
    layers: [{ id: "base", type: "raster", source: "base", paint: { "raster-brightness-max": 0.8 } }],
  },
  streets: {
    version: 8,
    sources: { base: { type: "raster", tileSize: 256, maxzoom: 19,
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], attribution: "© OpenStreetMap contributors" } },
    layers: [{ id: "base", type: "raster", source: "base", paint: { "raster-saturation": -0.85, "raster-brightness-max": 0.45, "raster-contrast": 0.1 } }],
  },
};
