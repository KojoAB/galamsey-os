"use client";

import dynamic from "next/dynamic";

// MapLibre needs the browser (WebGL), so load it client-side only.
const MapView = dynamic(() => import("./MapView"), {
  ssr: false,
  loading: () => <div className="flex h-full items-center justify-center text-sm text-ink-400">Loading map…</div>,
});

export default function MapLoader({ demo }: { demo: boolean }) {
  return <MapView demo={demo} />;
}
