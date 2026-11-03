import type { RecurringResponse, RecurringStream } from "@/lib/api";
import { formatCents } from "@/lib/format";

const FREQUENCIES = { weekly: "Weekly", biweekly: "Every two weeks", semi_monthly: "Twice a month", monthly: "Monthly" };
const day = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

function badge(stream: RecurringStream): string | null {
  if (stream.change === "new") return "New";
  if (stream.change === "income_change") return stream.status === "stale" ? "Income stopped" : "Income down";
  if (stream.change === "price_increase") return stream.previous_cents === null
    ? "Price up" : `Price up ${formatCents(stream.previous_cents)} to ${formatCents(stream.last_cents)}`;
  return null;
}

function Rows({ streams }: { streams: RecurringStream[] }) {
  return <ul className="divide-y divide-black/10 dark:divide-white/15">
    {streams.map((stream) => <li key={stream.id} className="flex flex-wrap items-center justify-between gap-2 py-3 text-sm">
      <div className="min-w-0">
        <p className="break-words font-medium">{stream.direction === "in" ? "Income" : stream.label}</p>
        <p className="text-neutral-500">{FREQUENCIES[stream.frequency]}</p>
        {badge(stream) && <span className="inline-block rounded bg-amber-100 px-1.5 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-100">{badge(stream)}</span>}
      </div>
      <div className="text-right tabular-nums">
        <p>{stream.amount_kind === "varies" ? "about " : ""}{formatCents(stream.typical_cents)}</p>
        <p className="text-neutral-500">{stream.status === "stale" ? "Stopped" : day.format(new Date(stream.next_date))}</p>
      </div>
    </li>)}
  </ul>;
}

export default function RecurringList({ data }: { data: RecurringResponse }) {
  const active = data.streams.filter((s) => s.status === "active");
  const stale = data.streams.filter((s) => s.status === "stale");
  return <div className="space-y-3">
    {data.streams.length === 0 && <p className="text-sm text-neutral-500">No repeating charges found yet.</p>}
    {data.as_of === null && <p className="text-sm text-neutral-500">We need about 3 months of transactions to spot repeating charges.</p>}
    {active.length > 0 && <Rows streams={active} />}
    {stale.length > 0 && <div className="text-neutral-500 opacity-70">
      <h3 className="text-sm font-medium">No longer seen</h3>
      <Rows streams={stale} />
    </div>}
  </div>;
}
