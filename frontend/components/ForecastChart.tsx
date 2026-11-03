"use client";

import { useState } from "react";
import { Area, ComposedChart, Line, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { errorMessage, type Forecast } from "@/lib/api";
import { formatCents } from "@/lib/format";

const day = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const label = (iso: string) => day.format(new Date(iso));

export default function ForecastChart({ data, onSaveBuffer }: { data: Forecast; onSaveBuffer: (cents: number) => Promise<void> }) {
  const [dollars, setDollars] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lowest = data.days.reduce<Forecast["days"][number] | null>((a, b) => !a || b.low_cents < a.low_cents ? b : a, null);
  const rows = data.days.map((d) => ({ date: d.date, expected: d.expected_cents / 100, band: [d.low_cents / 100, d.high_cents / 100] }));
  const aria = lowest && data.days.length ? `Cash forecast, ${label(data.days[0].date)} to ${label(data.days[data.days.length - 1].date)}. Lowest likely balance ${formatCents(lowest.low_cents)} on ${label(lowest.date)}; buffer ${formatCents(data.buffer_cents)}.` : "Cash forecast";

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const text = dollars ?? String(data.buffer_cents / 100);
    const value = Number(text);
    if (!text.trim() || !Number.isFinite(value) || value < 0 || value > 10000) {
      setError("The buffer is $0 to $10,000."); return;
    }
    setSaving(true); setError(null);
    try { await onSaveBuffer(Math.round(value * 100)); setDollars(null); }
    catch (err) { setError(errorMessage(err)); }
    finally { setSaving(false); }
  }

  return <div className="min-w-0 space-y-3">
    {data.status === "no_checking" ? <p className="text-sm text-neutral-500">Link a checking account to see your forecast.</p>
      : data.status === "no_history" ? <p className="text-sm text-neutral-500">We need about a month of transactions to forecast.</p>
      : lowest && <>
        <p>Your typical range is between {formatCents(lowest.low_cents)} and {formatCents(lowest.high_cents)} on {label(lowest.date)}.</p>
        <div role="img" aria-label={aria} className="min-w-0">
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={rows} accessibilityLayer margin={{ left: 0, right: 12, top: 20, bottom: 0 }}>
              <XAxis dataKey="date" tickFormatter={label} minTickGap={32} tick={{ fontSize: 11 }} />
              <YAxis width={48} tickFormatter={(v: number) => Math.abs(v) >= 1000 ? `$${(v / 1000).toFixed(1)}k` : `$${v}`} tick={{ fontSize: 11 }} />
              <Tooltip labelFormatter={(v) => label(String(v))} />
              <Area dataKey="band" stroke="none" fill="#2563eb" fillOpacity={0.2} name="Typical range" />
              <Line dataKey="expected" stroke="#2563eb" dot={false} name="Expected" />
              <ReferenceLine y={data.buffer_cents / 100} stroke="#b45309" strokeDasharray="5 4" />
              {data.events.filter((e) => e.kind === "bill" || e.kind === "card_min").map((e, i) => {
                const d = data.days.find((d) => d.date === e.date);
                return d && <ReferenceDot key={`${e.date}-${i}`} x={e.date} y={d.expected_cents / 100} r={3} fill="#b45309"
                  label={e.label === data.dip?.bill_label && e.date === data.dip.date ? { value: e.label, position: "top", fontSize: 11 } : undefined} />;
              })}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
        <details>
          <summary className="cursor-pointer text-sm underline">Show as a table</summary>
          <div className="mt-2 max-w-full overflow-x-auto">
            <table className="w-full text-left text-xs tabular-nums">
              <caption className="sr-only">Cash forecast daily ranges and events</caption>
              <thead><tr>{["Date", "Low", "Expected", "High", "Events"].map((h) => <th key={h} className="p-1">{h}</th>)}</tr></thead>
              <tbody>{data.days.map((d) => <tr key={d.date}>
                <th scope="row" className="p-1 font-normal">{label(d.date)}</th>
                <td className="p-1">{formatCents(d.low_cents)}</td><td className="p-1">{formatCents(d.expected_cents)}</td>
                <td className="p-1">{formatCents(d.high_cents)}</td><td className="p-1">{data.events.filter((e) => e.date === d.date).map((e) => e.label).join(", ")}</td>
              </tr>)}</tbody>
            </table>
          </div>
        </details>
      </>}
    {data.income === "none" && <p className="text-sm text-neutral-500">No paycheck found, so no income is assumed.</p>}
    <form onSubmit={save} className="flex flex-wrap items-end gap-2 text-sm" noValidate>
      <label className="space-y-1">Keep at least $<input aria-label="Keep at least $" type="number" min={0} max={10000} step="0.01"
        value={dollars ?? data.buffer_cents / 100} onChange={(e) => setDollars(e.target.value)}
        className="ml-2 w-28 rounded border border-black/20 bg-transparent px-2 py-1 dark:border-white/25" /></label>
      <button type="submit" disabled={saving} className="rounded border border-black/20 px-3 py-1 disabled:opacity-50 dark:border-white/25">{saving ? "Saving…" : "Save"}</button>
    </form>
    {error && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p>}
  </div>;
}
