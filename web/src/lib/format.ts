// Display helpers. Dates are stored in UTC; Ghana is UTC+0 all year, so en-GB in Africa/Accra.
export const fmtDate = (d?: string | null) =>
  d ? new Date(d).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "Africa/Accra" }) : "—";
export const fmtDateTime = (d?: string | null) =>
  d ? new Date(d).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Africa/Accra" }) : "—";
export const fmtHa = (n?: number | string | null) => (n == null ? "—" : `${Number(n).toLocaleString("en-GB", { maximumFractionDigits: 1 })} ha`);

export const LEVEL_STYLE: Record<string, string> = {
  red: "bg-alert-red/15 text-alert-red ring-alert-red/40",
  amber: "bg-alert-amber/15 text-alert-amber ring-alert-amber/40",
  watch: "bg-ink-700 text-ink-200 ring-ink-600",
};
export const STATUS_LABEL: Record<string, string> = {
  new: "New", reviewing: "Reviewing", confirmed: "Confirmed", rejected: "Rejected",
  field_verified: "Field verified", closed: "Closed",
};
export const SOURCE_LABEL: Record<string, string> = {
  gfw_glad_l: "GLAD-L (Landsat)", gfw_glad_s2: "GLAD-S2", gfw_radd: "RADD (radar)", gfw_dist: "DIST-ALERT",
  s2_change: "Sentinel-2 change", s1_change: "Sentinel-1 radar", s2_turbidity: "River turbidity",
  ai_classifier: "AI classifier", planet_change: "PlanetScope", gov_forestry: "Forestry Commission",
  gov_cersgis: "CERSGIS", gov_equipment: "Equipment GPS",
};
