import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import RecurringList from "@/components/RecurringList";
import type { RecurringStream } from "@/lib/api";

afterEach(cleanup);

function row(over: Partial<RecurringStream> = {}): RecurringStream {
  return { id: 1, direction: "out", label: "Streamly", frequency: "monthly", amount_kind: "fixed",
    typical_cents: 1549, last_cents: 1549, previous_cents: null, last_date: "2026-10-05", next_date: "2026-11-05",
    status: "active", change: null, first_seen: "2026-10-01", ...over };
}

function draw(streams: RecurringStream[], as_of: string | null = "2026-10-25") {
  render(<RecurringList data={{ as_of, streams }} />);
}

test("shows a monthly subscription with its next date", () => {
  draw([row()]);
  for (const text of ["Streamly", "Monthly", "$15.49", "Nov 5"]) expect(screen.getByText(text)).toBeTruthy();
});

test("prefixes about when the amount varies", () => {
  draw([row({ amount_kind: "varies", typical_cents: 10200 })]);
  expect(screen.getByText("about $102.00")).toBeTruthy();
});

test("badges a price increase with both prices", () => {
  draw([row({ change: "price_increase", previous_cents: 4000, last_cents: 4300 })]);
  expect(screen.getByText("Price up $40.00 to $43.00")).toBeTruthy();
});

test("shows an inflow as Income with no employer", () => {
  draw([row({ direction: "in", label: "Employer" })]);
  expect(screen.getByText("Income")).toBeTruthy();
  expect(screen.queryByText("Employer")).toBeNull();
});

test("groups stopped streams under No longer seen", () => {
  draw([row({ status: "stale" })]);
  expect(screen.getByText("No longer seen")).toBeTruthy();
  expect(screen.getByText("Stopped")).toBeTruthy();
});

test("shows the empty text and the too-little-history text", () => {
  draw([], null);
  expect(screen.getByText("No repeating charges found yet.")).toBeTruthy();
  expect(screen.getByText("We need about 3 months of transactions to spot repeating charges.")).toBeTruthy();
});

test("keeps price and income badges readable when the previous amount is unavailable", () => {
  draw([row({ change: "price_increase" }), row({ id: 2, direction: "in", change: "income_change" })]);
  expect(screen.getByText("Price up")).toBeTruthy();
  expect(screen.getByText("Income down")).toBeTruthy();
});
