"use client";

import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

// Colour-blind-safe categorical palette for regions (distinct from alert red/amber).
const PALETTE = ["#3fb588", "#60a5fa", "#a78bfa", "#22d3ee", "#f472b6", "#facc15", "#fb923c", "#94a3b8"];
const axis = { stroke: "#7f8f89", fontSize: 12 };
const tip = { contentStyle: { background: "#111816", border: "1px solid #24302c", borderRadius: 8 }, labelStyle: { color: "#fff" } };

export function HectaresByRegion({ rows }: { rows: { week: string; region: string; hectares: number }[] }) {
  const weeks = [...new Set(rows.map((r) => r.week))].sort();
  const totals = new Map<string, number>();
  rows.forEach((r) => totals.set(r.region, (totals.get(r.region) ?? 0) + Number(r.hectares)));
  const top = [...totals.entries()].sort((a, b) => b[1] - a[1]).slice(0, 7).map(([r]) => r);
  const data = weeks.map((w) => {
    const row: Record<string, string | number> = { week: w.slice(5) };
    for (const r of rows.filter((x) => x.week === w)) {
      const key = top.includes(r.region) ? r.region : "Other";
      row[key] = Number(row[key] ?? 0) + Number(r.hectares);
    }
    return row;
  });
  const keys = [...top, ...(totals.size > top.length ? ["Other"] : [])];
  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={data}>
        <CartesianGrid stroke="#24302c" vertical={false} />
        <XAxis dataKey="week" {...axis} /><YAxis {...axis} unit=" ha" width={64} />
        <Tooltip {...tip} /><Legend wrapperStyle={{ fontSize: 12 }} />
        {keys.map((k, i) => <Bar key={k} dataKey={k} stackId="a" fill={PALETTE[i % PALETTE.length]} />)}
      </BarChart>
    </ResponsiveContainer>
  );
}

export function Accuracy({ rows }: { rows: { month: string; confirmed: number; rejected: number }[] }) {
  const data = rows.map((r) => ({ month: r.month.slice(0, 7), Confirmed: r.confirmed, Rejected: r.rejected,
    "Precision %": r.confirmed + r.rejected ? Math.round((100 * r.confirmed) / (r.confirmed + r.rejected)) : null }));
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={data}>
        <CartesianGrid stroke="#24302c" vertical={false} />
        <XAxis dataKey="month" {...axis} /><YAxis {...axis} width={40} />
        <Tooltip {...tip} /><Legend wrapperStyle={{ fontSize: 12 }} />
        <Bar dataKey="Confirmed" fill="#3fb588" /><Bar dataKey="Rejected" fill="#7f8f89" />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function ResponseTime({ rows }: { rows: { week: string; median_hours: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={rows.map((r) => ({ week: r.week.slice(5), hours: Number(r.median_hours) }))}>
        <CartesianGrid stroke="#24302c" vertical={false} />
        <XAxis dataKey="week" {...axis} /><YAxis {...axis} unit=" h" width={48} />
        <Tooltip {...tip} /><Line dataKey="hours" name="Median hours to decision" stroke="#60a5fa" strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
