"use client";

import { useState } from "react";

// Before/after comparison: drag the slider (or use arrow keys) to reveal the "after" image.
export default function BeforeAfter({ before, after, label }: { before: string; after: string; label: string }) {
  const [pos, setPos] = useState(50);
  return (
    <figure className="select-none">
      <div className="relative aspect-square w-full overflow-hidden rounded-lg bg-ink-950">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={before} alt={`${label} before`} className="absolute inset-0 h-full w-full object-cover" />
        <div className="absolute inset-0" style={{ clipPath: `inset(0 0 0 ${pos}%)` }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={after} alt={`${label} after`} className="h-full w-full object-cover" />
        </div>
        <div className="pointer-events-none absolute inset-y-0 w-0.5 bg-white/90 shadow" style={{ left: `${pos}%` }} aria-hidden />
        <span className="absolute bottom-2 left-2 rounded bg-black/60 px-1.5 text-xs text-white">Before</span>
        <span className="absolute bottom-2 right-2 rounded bg-black/60 px-1.5 text-xs text-white">After</span>
      </div>
      <input type="range" min={0} max={100} value={pos} onChange={(e) => setPos(Number(e.target.value))}
        aria-label={`${label}: slide between before and after`} className="mt-2 w-full accent-[var(--color-brand-500)]" />
      <figcaption className="text-xs text-ink-400">{label}</figcaption>
    </figure>
  );
}
