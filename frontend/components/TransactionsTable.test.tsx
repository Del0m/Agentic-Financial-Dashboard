import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import TransactionsTable from "@/components/TransactionsTable";
import type { Txn } from "@/lib/api";

afterEach(cleanup);

const LINES = [
  { category: "FOOD_AND_DRINK", name: "Groceries" },
  { category: "GENERAL_MERCHANDISE", name: "Shopping" },
  { category: "FREE", name: "Free" },
];

function txn(over: Partial<Txn> = {}): Txn {
  return { id: 1, date: "2026-10-12", merchant: "Costco", amount_cents: 21000, category: "GENERAL_MERCHANDISE",
           line_name: "Shopping", source: "plaid", ...over };
}

function draw(rows: Txn[], onCorrect = vi.fn(), onRemembered = vi.fn()) {
  render(<TransactionsTable transactions={rows} lines={LINES} onCorrect={onCorrect} onRemembered={onRemembered} />);
  return { onCorrect, onRemembered };
}

describe("TransactionsTable", () => {
  test("renders rows with their line", () => {
    draw([txn(), txn({ id: 2, merchant: "Employer", amount_cents: -420000, category: "INCOME", line_name: null })]);
    expect(screen.getByText("Costco")).toBeTruthy();
    expect(screen.getByText("$210.00")).toBeTruthy();
    const select = screen.getByRole("combobox", { name: "Line for Costco" }) as HTMLSelectElement;
    expect(select.value).toBe("GENERAL_MERCHANDISE");
    expect(screen.queryByRole("combobox", { name: "Line for Employer" })).toBeNull();   // income: no line
  });

  test("picking a line corrects the row", async () => {
    const onCorrect = vi.fn().mockResolvedValue(txn({ category: "FOOD_AND_DRINK", line_name: "Groceries",
                                                       source: "user" }));
    draw([txn()], onCorrect);
    await act(async () => {
      fireEvent.change(screen.getByRole("combobox", { name: "Line for Costco" }), { target: { value: "FOOD_AND_DRINK" } });
    });
    expect(onCorrect).toHaveBeenCalledWith(1, "FOOD_AND_DRINK", false);
    expect((screen.getByRole("combobox", { name: "Line for Costco" }) as HTMLSelectElement).value).toBe("FOOD_AND_DRINK");
  });

  test('offers "Always for Costco?" and remembers', async () => {
    const corrected = txn({ category: "FOOD_AND_DRINK", line_name: "Groceries", source: "user" });
    const onCorrect = vi.fn().mockResolvedValue(corrected);
    const { onRemembered } = draw([txn()], onCorrect);
    await act(async () => {
      fireEvent.change(screen.getByRole("combobox", { name: "Line for Costco" }), { target: { value: "FOOD_AND_DRINK" } });
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Always for Costco?" }));
    });
    expect(onCorrect).toHaveBeenLastCalledWith(1, "FOOD_AND_DRINK", true);
    expect(onRemembered).toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Always for Costco?" })).toBeNull();
  });

  test("marks rule rows", () => {
    draw([txn({ source: "rule" })]);
    expect(screen.getByText("rule")).toBeTruthy();
  });

  test("spending without a line shows Free", () => {
    draw([txn({ category: "TRAVEL", line_name: "Free" })]);
    expect((screen.getByRole("combobox", { name: "Line for Costco" }) as HTMLSelectElement).value).toBe("FREE");
  });

  test("goal lines aren't offered as a category", () => {
    const lines = [...LINES, { category: "GOAL_DEBT_PAYOFF", name: "Debt payoff", group: "goal" }];
    render(<TransactionsTable transactions={[txn()]} lines={lines} onCorrect={vi.fn()} onRemembered={vi.fn()} />);
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Groceries", "Shopping", "Free"]);
  });
});
