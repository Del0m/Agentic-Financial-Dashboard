import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import ViewSwitch from "@/components/ViewSwitch";
import { ApiError, getView, setView } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getView: vi.fn(),
  setView: vi.fn(),
}));

afterEach(cleanup);
beforeEach(() => vi.resetAllMocks());

describe("ViewSwitch", () => {
  test("shows which user is on, and switching saves then reloads", async () => {
    vi.mocked(getView).mockResolvedValue({ view: "demo", available: true });
    vi.mocked(setView).mockResolvedValue({ view: "plaid", available: true });
    const reload = vi.fn();
    render(<ViewSwitch reload={reload} />);
    const demo = await screen.findByRole("button", { name: "Demo user" });
    expect(demo.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Plaid bank" }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(setView).toHaveBeenCalledWith("plaid");
  });

  test("clicking the current view does nothing", async () => {
    vi.mocked(getView).mockResolvedValue({ view: "plaid", available: true });
    const reload = vi.fn();
    render(<ViewSwitch reload={reload} />);
    fireEvent.click(await screen.findByRole("button", { name: "Plaid bank" }));
    expect(setView).not.toHaveBeenCalled();
    expect(reload).not.toHaveBeenCalled();
  });

  test("renders nothing when there's nothing to switch", async () => {
    vi.mocked(getView).mockResolvedValue({ view: "demo", available: false });
    render(<ViewSwitch />);
    await waitFor(() => expect(getView).toHaveBeenCalled());
    expect(screen.queryByRole("group")).toBeNull();
  });

  test("renders nothing without the admin tools", async () => {
    vi.mocked(getView).mockRejectedValue(new ApiError(404, { detail: "Not found." }));
    render(<ViewSwitch />);
    await waitFor(() => expect(getView).toHaveBeenCalled());
    expect(screen.queryByRole("group")).toBeNull();
  });

  test("a failed switch says why and stays put", async () => {
    vi.mocked(getView).mockResolvedValue({ view: "demo", available: true });
    vi.mocked(setView).mockRejectedValue(new ApiError(500, { detail: "Server down." }));
    const reload = vi.fn();
    render(<ViewSwitch reload={reload} />);
    fireEvent.click(await screen.findByRole("button", { name: "Plaid bank" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/Server down/);
    expect(reload).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Demo user" }).getAttribute("aria-pressed")).toBe("true");
  });
});
