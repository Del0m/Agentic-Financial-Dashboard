"use client";

import { useEffect, useState } from "react";
import { errorMessage, getView, setView, type View } from "@/lib/api";

const OPTIONS: { view: View; label: string }[] = [
  { view: "demo", label: "Demo user" },
  { view: "plaid", label: "Plaid bank" },
];

// which user the app shows: the seeded demo user, or the user whose banks are linked through plaid. only there with
// the admin tools on and a demo user configured; anywhere else it renders nothing. a switch reloads the page, so
// every card reads the new user
export default function ViewSwitch({ reload = () => window.location.reload() }: { reload?: () => void }) {
  const [view, setCurrent] = useState<View | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getView()
      .then((v) => !cancelled && v.available && setCurrent(v.view))
      .catch(() => {}); // no admin tools: nothing to switch
    return () => {
      cancelled = true;
    };
  }, []);

  if (view === null) return null;

  async function choose(next: View) {
    if (next === view || busy) return;
    setBusy(true);
    setError(null);
    try {
      await setView(next);
      reload();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <div role="group" aria-label="Whose data to show" className="flex rounded-full border border-black/10 p-0.5 text-xs dark:border-white/15">
        {OPTIONS.map((o) => (
          <button key={o.view} type="button" aria-pressed={view === o.view} disabled={busy} onClick={() => choose(o.view)}
            className={`rounded-full px-2.5 py-1 disabled:opacity-60 ${view === o.view
              ? "bg-black text-white dark:bg-white dark:text-black" : "text-neutral-600 dark:text-neutral-300"}`}>
            {o.label}
          </button>
        ))}
      </div>
      {error && <span role="alert" className="text-xs text-red-600 dark:text-red-400">{error}</span>}
    </div>
  );
}
