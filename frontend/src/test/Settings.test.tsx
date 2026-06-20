import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DashboardPage from "../pages/dashboard/DashboardPage";

const ME = {
  user_id: "u1",
  email: "demo@x.invalid",
  name: "Demo",
  is_admin: true,
};

interface ScanRow {
  run_id: number;
  status: string;
  started_at: string | null;
  ended_at: string | null;
}
interface ActionRow {
  run_id: number;
  action_name: string;
  asset_id: string;
  success: boolean | null;
  error_code: string | null;
}

interface MockState {
  scans: ScanRow[];
  actions: ActionRow[];
  unlock: {
    status: number;
    body: unknown;
  };
  delete_status: number;
}

function setupFetchMock(overrides: Partial<MockState> = {}) {
  const state: MockState = {
    scans: [
      {
        run_id: 1,
        status: "completed",
        started_at: "2026-05-01T10:00:00Z",
        ended_at: "2026-05-01T10:00:30Z",
      },
      {
        run_id: 2,
        status: "completed",
        started_at: "2026-05-02T10:00:00Z",
        ended_at: "2026-05-02T10:00:30Z",
      },
    ],
    actions: [
      {
        run_id: 1,
        action_name: "move_to_locked_folder",
        asset_id: "asset-locked-1",
        success: true,
        error_code: null,
      },
      {
        run_id: 2,
        action_name: "tag",
        asset_id: "asset-tag-1",
        success: true,
        error_code: null,
      },
    ],
    unlock: { status: 200, body: { run_id: 1, reverted: 1, failed_asset_ids: [] } },
    delete_status: 204,
    ...overrides,
  };
  const calls: { url: string; init?: RequestInit }[] = [];
  document.cookie = "mr_csrf=csrf-token";

  const handler = async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = typeof url === "string" ? url : url.toString();
    calls.push({ url: u, init });
    const method = (init?.method ?? "GET").toUpperCase();
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      });

    if (u.endsWith("/api/me/categories") && method === "GET") {
      return json({
        categories: {},
        active_model_sha256: null,
        last_seen_model_sha256: null,
        needs_reclassify: false,
      });
    }
    if (u.endsWith("/api/me/categories") && method === "PUT") {
      return json({ categories: {} });
    }
    if (u.endsWith("/api/me/policies") && method === "GET") {
      return json({ policies: {} });
    }
    if (u.endsWith("/api/scans") && method === "GET") {
      return json({ scans: state.scans });
    }
    const detailMatch = u.match(/\/api\/scans\/(\d+)$/);
    if (detailMatch && method === "GET") {
      const id = Number(detailMatch[1]);
      const run = state.scans.find((s) => s.run_id === id);
      if (!run) return new Response(JSON.stringify({}), { status: 404 });
      return json({
        ...run,
        summary_json: null,
        actions: state.actions
          .filter((a) => a.run_id === id)
          .map(({ run_id: _r, ...rest }) => rest),
      });
    }
    if (u.endsWith("/api/me/locked-folder/unlock") && method === "POST") {
      return json(state.unlock.body, state.unlock.status);
    }
    if (u.endsWith("/api/me") && method === "DELETE") {
      return new Response(null, { status: state.delete_status });
    }
    if (u.endsWith("/api/audit") && method === "GET") {
      return json({ entries: [] });
    }
    return new Response("", { status: 404 });
  };
  (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn(
    handler,
  ) as unknown as typeof fetch;
  return { calls, state };
}

async function openSettings() {
  await userEvent.click(screen.getByRole("tab", { name: "Settings" }));
}

describe("Settings — UnlockPanel", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    document.cookie = "mr_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  it("shows empty-state copy when no run has a locked-folder action", async () => {
    setupFetchMock({
      actions: [
        {
          run_id: 1,
          action_name: "tag",
          asset_id: "asset-1",
          success: true,
          error_code: null,
        },
      ],
    });
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    await openSettings();
    expect(
      await screen.findByText(/No completed runs include Locked Folder moves/),
    ).toBeInTheDocument();
  });

  it("lists a run with move_to_locked_folder and opens the PIN dialog", async () => {
    setupFetchMock();
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    await openSettings();
    expect(await screen.findByText("Run #1")).toBeInTheDocument();
    expect(screen.queryByText("Run #2")).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Reverse Locked-Folder moves" }),
    );
    expect(
      await screen.findByText("Enter your Locked Folder PIN"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Locked Folder PIN")).toBeInTheDocument();
  });

  it("submits the PIN with CSRF and surfaces the reverted count", async () => {
    const { calls } = setupFetchMock();
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    await openSettings();
    await screen.findByText("Run #1");
    await userEvent.click(
      screen.getByRole("button", { name: "Reverse Locked-Folder moves" }),
    );
    const pinInput = (await screen.findByLabelText(
      "Locked Folder PIN",
    )) as HTMLInputElement;
    fireEvent.change(pinInput, { target: { value: "1234" } });
    await userEvent.click(screen.getByRole("button", { name: "Reverse moves" }));
    await waitFor(() =>
      expect(screen.getByText(/Reverted 1 asset/)).toBeInTheDocument(),
    );
    const call = calls.find(
      (c) =>
        c.url.endsWith("/api/me/locked-folder/unlock") &&
        c.init?.method === "POST",
    );
    expect(call).toBeDefined();
    const body = JSON.parse((call!.init!.body as string));
    expect(body).toEqual({ run_id: 1, pin: "1234" });
    expect(new Headers(call!.init!.headers).get("X-CSRF-Token")).toBe(
      "csrf-token",
    );
  });

  it("never persists the PIN to storage and clears it from state, never logs it", async () => {
    setupFetchMock();
    const PIN = "secret-pin-9182";
    const spies = {
      log: vi.spyOn(console, "log").mockImplementation(() => undefined),
      info: vi.spyOn(console, "info").mockImplementation(() => undefined),
      warn: vi.spyOn(console, "warn").mockImplementation(() => undefined),
      error: vi.spyOn(console, "error").mockImplementation(() => undefined),
    };
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    await openSettings();
    await screen.findByText("Run #1");
    await userEvent.click(
      screen.getByRole("button", { name: "Reverse Locked-Folder moves" }),
    );
    const pinInput = (await screen.findByLabelText(
      "Locked Folder PIN",
    )) as HTMLInputElement;
    fireEvent.change(pinInput, { target: { value: PIN } });
    await userEvent.click(screen.getByRole("button", { name: "Reverse moves" }));
    await waitFor(() =>
      expect(screen.getByText(/Reverted 1 asset/)).toBeInTheDocument(),
    );

    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    for (const k of ["log", "info", "warn", "error"] as const) {
      for (const call of spies[k].mock.calls) {
        for (const arg of call) {
          expect(typeof arg === "string" ? arg : "").not.toContain(PIN);
        }
      }
    }
    expect(pinInput.value).toBe("");
  });
});

describe("Settings — DangerZone", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    document.cookie = "mr_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  it("confirm button is disabled until typed email matches exactly", async () => {
    setupFetchMock();
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    await openSettings();
    await userEvent.click(
      await screen.findByRole("button", { name: "Delete my account" }),
    );
    const input = (await screen.findByLabelText(
      /Type demo@x\.invalid to confirm/,
    )) as HTMLInputElement;
    const confirm = screen.getByRole("button", { name: "Delete account" });
    expect(confirm).toBeDisabled();
    fireEvent.change(input, { target: { value: "demo@x.invali" } });
    expect(confirm).toBeDisabled();
    fireEvent.change(input, { target: { value: "demo@x.invalid" } });
    expect(confirm).toBeEnabled();
  });

  it("DELETE /me with CSRF and notifies onLoggedOut on 204", async () => {
    const onLoggedOut = vi.fn();
    const { calls } = setupFetchMock();
    render(<DashboardPage me={ME} onLoggedOut={onLoggedOut} />);
    await openSettings();
    await userEvent.click(
      await screen.findByRole("button", { name: "Delete my account" }),
    );
    const input = (await screen.findByLabelText(
      /Type demo@x\.invalid to confirm/,
    )) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "demo@x.invalid" } });
    await userEvent.click(screen.getByRole("button", { name: "Delete account" }));
    await waitFor(() => expect(onLoggedOut).toHaveBeenCalledTimes(1));
    const call = calls.find(
      (c) => c.url.endsWith("/api/me") && c.init?.method === "DELETE",
    );
    expect(call).toBeDefined();
    expect(new Headers(call!.init!.headers).get("X-CSRF-Token")).toBe(
      "csrf-token",
    );
  });
});
