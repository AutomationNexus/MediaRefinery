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

interface MockState {
  needs_reclassify: boolean;
  scans: {
    run_id: number;
    status: string;
    started_at: string | null;
    ended_at: string | null;
  }[];
  actions: {
    run_id: number;
    action_name: string;
    asset_id: string;
    success: boolean | null;
    error_code: string | null;
  }[];
  audit: {
    id: number;
    at: string;
    action: string;
    target_asset_id: string | null;
    run_id: number | null;
  }[];
  categories: Record<string, unknown>;
  policies: Record<string, unknown>;
}

function setupFetchMock(initial: Partial<MockState> = {}) {
  const state: MockState = {
    needs_reclassify: false,
    scans: [
      {
        run_id: 1,
        status: "completed",
        started_at: "2026-05-02T10:00:00Z",
        ended_at: "2026-05-02T10:00:30Z",
      },
    ],
    actions: [
      {
        run_id: 1,
        action_name: "tag",
        asset_id: "asset-1",
        success: true,
        error_code: null,
      },
    ],
    audit: [
      {
        id: 1,
        at: "2026-05-02T10:00:00Z",
        action: "scan.start",
        target_asset_id: null,
        run_id: 1,
      },
    ],
    categories: { pets: { enabled: true, threshold: 0.7 } },
    policies: { pets: { image: { on_match: "tag" } } },
    ...initial,
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
        categories: state.categories,
        active_model_sha256: "demo",
        last_seen_model_sha256: state.needs_reclassify ? "old" : "demo",
        needs_reclassify: state.needs_reclassify,
      });
    }
    if (u.endsWith("/api/me/categories") && method === "PUT") {
      const body = JSON.parse((init?.body as string) ?? "{}");
      state.categories = body.categories;
      return json({ categories: state.categories });
    }
    if (u.endsWith("/api/me/policies") && method === "GET") {
      return json({ policies: state.policies });
    }
    if (u.endsWith("/api/me/policies") && method === "PUT") {
      const body = JSON.parse((init?.body as string) ?? "{}");
      state.policies = body.policies;
      return json({ policies: state.policies });
    }
    if (u.endsWith("/api/scans") && method === "GET") {
      return json({ scans: state.scans });
    }
    if (u.endsWith("/api/scans") && method === "POST") {
      const next = state.scans.length + 1;
      state.scans = [
        ...state.scans,
        { run_id: next, status: "running", started_at: null, ended_at: null },
      ];
      return json({ run_id: next, status: "running" }, 202);
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
          .map(({ run_id: _, ...rest }) => rest),
      });
    }
    const undoMatch = u.match(/\/api\/scans\/(\d+)\/undo$/);
    if (undoMatch && method === "POST") {
      const id = Number(undoMatch[1]);
      const reverted = state.actions.filter((a) => a.run_id === id && a.success).length;
      return json({ run_id: id, reverted });
    }
    if (u.endsWith("/api/audit") && method === "GET") {
      return json({ entries: state.audit });
    }
    if (u.endsWith("/api/auth/logout") && method === "POST") {
      return new Response(null, { status: 204 });
    }
    return new Response("", { status: 404 });
  };

  (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn(
    handler,
  ) as unknown as typeof fetch;
  return { calls, state };
}

describe("DashboardPage", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    document.cookie = "mr_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  it("renders the runs list and walks into run detail", async () => {
    setupFetchMock();
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    expect(await screen.findByText("#1")).toBeInTheDocument();
    expect(screen.getByText("Open")).toBeInTheDocument();
    await userEvent.click(screen.getByText("Open"));
    expect(await screen.findByText("Run #1")).toBeInTheDocument();
    expect(screen.getByText("tag")).toBeInTheDocument();
    expect(screen.getByText("asset-1")).toBeInTheDocument();
  });

  it("undo dispatches with the CSRF header and reports reverted count", async () => {
    const { calls } = setupFetchMock();
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    await screen.findByText("#1");
    await userEvent.click(screen.getByText("Undo"));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(
      Array.from(dialog.querySelectorAll("button")).find(
        (b) => b.textContent === "Undo",
      )!,
    );
    await waitFor(() =>
      expect(screen.getByText(/Reverted 1 action/)).toBeInTheDocument(),
    );
    const undoCall = calls.find(
      (c) => c.url.endsWith("/api/scans/1/undo") && c.init?.method === "POST",
    );
    expect(undoCall).toBeDefined();
    const headers = new Headers(undoCall!.init!.headers);
    expect(headers.get("X-CSRF-Token")).toBe("csrf-token");
    expect((undoCall!.init as RequestInit).credentials).toBe("include");
  });

  it("renders the audit table when the audit tab is selected", async () => {
    setupFetchMock();
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    await userEvent.click(screen.getByRole("tab", { name: "Audit" }));
    expect(await screen.findByText("scan.start")).toBeInTheDocument();
  });

  it("categories editor PUTs the parsed JSON with CSRF", async () => {
    const { calls } = setupFetchMock();
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    await userEvent.click(screen.getByRole("tab", { name: "Settings" }));
    const ta = (await screen.findByLabelText("Categories (JSON)")) as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: JSON.stringify({ pets: { enabled: false } }) } });
    await userEvent.click(screen.getByText("Save categories"));
    await waitFor(() =>
      expect(screen.getAllByText("Saved.").length).toBeGreaterThan(0),
    );
    const putCall = calls.find(
      (c) => c.url.endsWith("/api/me/categories") && c.init?.method === "PUT",
    );
    expect(putCall).toBeDefined();
    expect(JSON.parse((putCall!.init!.body as string)).categories).toEqual({
      pets: { enabled: false },
    });
    expect(new Headers(putCall!.init!.headers).get("X-CSRF-Token")).toBe(
      "csrf-token",
    );
  });

  it("re-scan banner appears when needs_reclassify=true and triggers POST /scans", async () => {
    const { calls } = setupFetchMock({ needs_reclassify: true });
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    expect(
      await screen.findByText("Active model has changed"),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByText("Start re-scan"));
    await waitFor(() =>
      expect(screen.getByText(/Re-scan started — run #/)).toBeInTheDocument(),
    );
    const scanCall = calls.find(
      (c) => c.url.endsWith("/api/scans") && c.init?.method === "POST",
    );
    expect(scanCall).toBeDefined();
    expect(new Headers(scanCall!.init!.headers).get("X-CSRF-Token")).toBe(
      "csrf-token",
    );
  });

  it("logout button calls /auth/logout and notifies the parent", async () => {
    const onLoggedOut = vi.fn();
    setupFetchMock();
    render(<DashboardPage me={ME} onLoggedOut={onLoggedOut} />);
    await userEvent.click(screen.getByText("Sign out"));
    await waitFor(() => expect(onLoggedOut).toHaveBeenCalledTimes(1));
  });

  it("never persists wizard- or dashboard-shaped state to localStorage / sessionStorage", async () => {
    setupFetchMock({ needs_reclassify: true });
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    await screen.findByText("Active model has changed");
    await userEvent.click(screen.getByRole("tab", { name: "Audit" }));
    await screen.findByText("scan.start");
    await userEvent.click(screen.getByRole("tab", { name: "Settings" }));
    await screen.findByLabelText("Categories (JSON)");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});
