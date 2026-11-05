import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import ForecastChart from "@/components/ForecastChart";
import type { Forecast } from "@/lib/api";

vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  ComposedChart: () => <div />,
  Area: () => null, Line: () => null, ReferenceDot: () => null, ReferenceLine: () => null,
  Tooltip: () => null, XAxis: () => null, YAxis: () => null,
}));

afterEach(cleanup);

export const FORECAST: Forecast = {
  today: "2026-10-10", status: "ok", start_cents: 155000, buffer_cents: 10000, horizon_days: 3, income: "paycheck",
  days: [{ date: "2026-10-11", expected_cents: 150000, low_cents: 145000, high_cents: 155000 },
    { date: "2026-10-13", expected_cents: 20000, low_cents: 500, high_cents: 41200 }],
  events: [{ date: "2026-10-13", label: "Landlord", cents: -150000, kind: "bill" }],
  dip: { date: "2026-10-13", days_ahead: 3, shortfall_cents: 9500, bill_label: "Landlord" },
  monthly_surplus_cents: 100000, surplus_source: "forecast", suggested_extra_cents: 23000, notes: [],
};

test("headlines a range, never the expected value alone", () => {
  render(<ForecastChart data={FORECAST} onSaveBuffer={vi.fn()} />);
  expect(screen.getByText("Your lowest point is likely Oct 13, with a balance between $5.00 and $412.00.")).toBeTruthy();
});

test("labels the chart with the lowest likely balance and the buffer", () => {
  render(<ForecastChart data={FORECAST} onSaveBuffer={vi.fn()} />);
  expect(screen.getByRole("img", { name: /Lowest likely balance \$5.00 on Oct 13; buffer \$100.00/ })).toBeTruthy();
});

test("lists every day in the table and names the dips bill", () => {
  render(<ForecastChart data={FORECAST} onSaveBuffer={vi.fn()} />);
  expect(screen.getByText("Show as a table")).toBeTruthy();
  expect(screen.getByText("Landlord")).toBeTruthy();
  expect(screen.getAllByRole("row")).toHaveLength(3);
});

test.each([
  ["no_checking", "Link a checking account to see your forecast."],
  ["no_history", "We need about a month of transactions to forecast."],
] as const)("explains %s", (status, text) => {
  render(<ForecastChart data={{ ...FORECAST, status, days: [] }} onSaveBuffer={vi.fn()} />);
  expect(screen.getByText(text)).toBeTruthy();
});

test("explains when no income is assumed", () => {
  render(<ForecastChart data={{ ...FORECAST, income: "none" }} onSaveBuffer={vi.fn()} />);
  expect(screen.getByText("No paycheck found, so no income is assumed.")).toBeTruthy();
});

test("rejects a buffer outside 0 to 10000 before calling the api", () => {
  const save = vi.fn(); render(<ForecastChart data={FORECAST} onSaveBuffer={save} />);
  fireEvent.change(screen.getByLabelText("Keep at least $"), { target: { value: "10001" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(screen.getByRole("alert").textContent).toBe("The buffer is $0 to $10,000.");
  expect(save).not.toHaveBeenCalled();
});

test("saves a valid buffer through the refresh callback", async () => {
  const save = vi.fn().mockResolvedValue(undefined); render(<ForecastChart data={FORECAST} onSaveBuffer={save} />);
  fireEvent.change(screen.getByLabelText("Keep at least $"), { target: { value: "250" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(save).toHaveBeenCalledWith(25000));
  await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeTruthy());
});
