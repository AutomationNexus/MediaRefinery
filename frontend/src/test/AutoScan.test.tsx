import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AutoScanPanel from "../pages/dashboard/settings/AutoScanPanel";

interface MockState {
  get: {
    enabled: boolean;
    interval_minutes: number;
    last_seen_taken_at: string | null;
    last_run_at: string | null;
    last_status: "ok" | "error" | null;
    last_error_code: string | null;
  };
  put_status: number;
}

function setupFetchMock(overrides: Partial<MockState> = {}) {
  const state: MockState = {
    get: {
      enabled: false,
      interval_minutes: 30,
      last_seen_taken_at: null,
      last_run_at: null,
      last_status: null,
      last_error_code: null,
    },
    put_status: 200,
    ...overrides,
  };
  const calls: { url: string; init?: RequestInit }[] = [];
  document.cookie = "mr_csrf=csrf-token";
  const handler = async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = typeof url === "string" ? url : url.toString();
    calls.push({ url: u, init });
    const method = (init?.method ?? "GET").toUpperCase();
    if (u.endsWith("/api/me/auto-scan") && method === "GET") {
      return new Response(JSON.stringify(state.get), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (u.endsWith("/api/me/auto-scan") && method === "PUT") {
      const body = JSON.parse((init?.body as string) ?? "{}");
      const next = {
        ...state.get,
        enabled: body.enabled,
        interval_minutes: body.interval_minutes,
      };
      return new Response(JSON.stringify(next), {
        status: state.put_status,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response("", { status: 404 });
  };
  (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn(
    handler,
  ) as unknown as typeof fetch;
  return { calls, state };
}

describe("AutoScanPanel", () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    document.cookie = "mr_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  it("renders settings loaded from the server", async () => {
    setupFetchMock({
      get: {
        enabled: true,
        interval_minutes: 60,
        last_seen_taken_at: "2026-05-01T10:00:00Z",
        last_run_at: "2026-05-07T08:00:00Z",
        last_status: "ok",
        last_error_code: null,
      },
    });
    render(<AutoScanPanel />);
    await screen.findByRole("heading", { name: /Auto-scan on upload/ });
    const checkbox = (await screen.findByLabelText(
      /Enable scheduled polling/,
    )) as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    expect(screen.getByText(/2026-05-07T08:00:00Z/)).toBeInTheDocument();
  });

  it("PUTs with CSRF header and surfaces a saved flash", async () => {
    const { calls } = setupFetchMock();
    render(<AutoScanPanel />);
    const checkbox = (await screen.findByLabelText(
      /Enable scheduled polling/,
    )) as HTMLInputElement;
    fireEvent.click(checkbox);
    const select = screen.getByLabelText(/Poll every/) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "60" } });
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(screen.getByText("Saved.")).toBeInTheDocument(),
    );
    const put = calls.find(
      (c) =>
        c.url.endsWith("/api/me/auto-scan") && c.init?.method === "PUT",
    );
    expect(put).toBeDefined();
    expect(JSON.parse((put!.init!.body as string))).toEqual({
      enabled: true,
      interval_minutes: 60,
    });
    expect(new Headers(put!.init!.headers).get("X-CSRF-Token")).toBe(
      "csrf-token",
    );
  });

  it("surfaces a user-visible error when PUT fails", async () => {
    setupFetchMock({ put_status: 422 });
    render(<AutoScanPanel />);
    await screen.findByLabelText(/Enable scheduled polling/);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(
        screen.getByText("Could not save auto-scan settings."),
      ).toBeInTheDocument(),
    );
  });

  it("shows the disabled-by-server reason when the backend flipped enabled=FALSE on a 401", async () => {
    setupFetchMock({
      get: {
        enabled: false,
        interval_minutes: 30,
        last_seen_taken_at: null,
        last_run_at: "2026-05-07T08:00:00Z",
        last_status: "error",
        last_error_code: "upstream_session_expired",
      },
    });
    render(<AutoScanPanel />);
    expect(
      await screen.findByText(/Polling was paused because Immich rejected/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Immich session expired/)).toBeInTheDocument();
  });
});
