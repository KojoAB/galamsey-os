"use client";

import dynamic from "next/dynamic";

// MapLibre needs the browser (WebGL), so load it client-side only.
const loading = () => <div className="flex h-full items-center justify-center text-sm text-ink-400">Loading map…</div>;
const MapView = dynamic(() => import("./MapView"), { ssr: false, loading });
const PublicMap = dynamic(() => import("./PublicMap"), { ssr: false, loading });
const LabelMap = dynamic(() => import("./LabelMap"), { ssr: false, loading });

export default function MapLoader({ demo, staff }: { demo: boolean; staff?: boolean }) {
  return <MapView demo={demo} staff={staff} />;
}
export function PublicMapLoader({ demo }: { demo: boolean }) {
  return <PublicMap demo={demo} />;
}
export function LabelMapLoader() {
  return <LabelMap />;
}
