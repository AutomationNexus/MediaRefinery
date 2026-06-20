import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DashboardPage from "../pages/dashboard/DashboardPage";

const ME = {
  user_id: "u1",
  email: "demo@x.invalid",
  name: "Demo",
  is_admin: true,
};

interface AssetsState {
  pages: Record<string, { assets: AssetRow[]; next_cursor: string | null }>;
  categories: Record<string, unknown>;
  details: Record<string, Record<string, unknown>>;
}

interface AssetRow {
  asset_id: string;
  media_type: string;
  last_action: string | null;
  last_run_id: number | null;
  last_seen_category: string | null;
  analysis: null | {
    media_kind: string | null;
    safety_label: string | null;
    safety_confidence: number | null;
    document_type: string | null;
    quality_flags: string[];
    review_queues: string[];
    people_count: number;
    ocr_available: boolean;
    event_key: string | null;
  };
  can_override: boolean;
  search_source?: string | null;
  search_score?: number | null;
}

function setupFetchMock(initial?: Partial<AssetsState>) {
  const page1Assets: AssetRow[] = [
    {
      asset_id: "asset-1",
      media_type: "image",
      last_action: "tag",
      last_run_id: 1,
      last_seen_category: null,
      analysis: {
        media_kind: "image",
        safety_label: "sfw",
        safety_confidence: 0.96,
        document_type: "none",
        quality_flags: [],
        review_queues: ["sfw"],
        people_count: 0,
        ocr_available: false,
        event_key: "2026-01-01::camera-roll",
      },
      can_override: true,
    },
    {
      asset_id: "asset-2",
      media_type: "image",
      last_action: "tag",
      last_run_id: 1,
      last_seen_category: "pets",
      analysis: null,
      can_override: true,
    },
    {
      asset_id: "asset-3",
      media_type: "image",
      last_action: "tag",
      last_run_id: 2,
      last_seen_category: null,
      analysis: null,
      can_override: true,
    },
  ];
  const page2Assets: AssetRow[] = [
    {
      asset_id: "asset-4",
      media_type: "image",
      last_action: "tag",
      last_run_id: 3,
      last_seen_category: null,
      analysis: null,
      can_override: true,
    },
  ];

  const state: AssetsState = {
    pages: {
      "": { assets: page1Assets, next_cursor: "asset-3" },
      "asset-3": { assets: page2Assets, next_cursor: null },
    },
    categories: { pets: { enabled: true }, landscape: { enabled: true } },
    details: {
      "asset-1": {
        ocr: {
          available: true,
          status: "local",
          text: "Invoice number 123\nTotal tax amount due",
          confidence: 0.91,
        },
        document: {
          type: "invoice",
          reasons: ["keyword"],
          confidence: 0.72,
        },
      },
    },
    ...initial,
  };
  const calls: { url: string; init?: RequestInit }[] = [];
  document.cookie = "mr_csrf=csrf-token";

  const json = (body: unknown, status = 200) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });

  const handler = async (url: RequestInfo | URL, init?: RequestInit) => {
    const u = typeof url === "string" ? url : url.toString();
    calls.push({ url: u, init });
    const method = (init?.method ?? "GET").toUpperCase();

    const detailMatch = u.match(/\/api\/me\/assets\/([^/?]+)$/);
    if (detailMatch && method === "GET") {
      const assetId = decodeURIComponent(detailMatch[1]);
      return json({
        asset_id: assetId,
        analysis: state.details[assetId] ?? {},
      });
    }
    if (u.includes("/api/me/assets") && !u.includes("/category") && method === "GET") {
      const m = u.match(/cursor=([^&]+)/);
      const cursor = m ? decodeURIComponent(m[1]) : "";
      const page = state.pages[cursor] ?? { assets: [], next_cursor: null };
      return json(page);
    }
    const overrideMatch = u.match(/\/api\/me\/assets\/([^/]+)\/category$/);
    if (overrideMatch && method === "POST") {
      return json({
        asset_id: decodeURIComponent(overrideMatch[1]),
        category_id: JSON.parse((init?.body as string) ?? "{}").category_id,
        before: null,
      });
    }
    if (u.endsWith("/api/me/categories") && method === "GET") {
      return json({
        categories: state.categories,
        active_model_sha256: "demo",
        last_seen_model_sha256: "demo",
        needs_reclassify: false,
      });
    }
    if (u.endsWith("/api/me/policies") && method === "GET") {
      return json({ policies: {} });
    }
    if (u.endsWith("/api/scans") && method === "GET") {
      return json({ scans: [] });
    }
    if (u.endsWith("/api/audit") && method === "GET") {
      return json({ entries: [] });
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

async function openAssetsTab() {
  await userEvent.click(screen.getByRole("tab", { name: "Assets" }));
}

describe("AssetsList", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    document.cookie = "mr_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  it("renders the assets tab with three asset cards", async () => {
    setupFetchMock();
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    await openAssetsTab();
    expect(await screen.findByText("asset-1")).toBeInTheDocument();
    expect(screen.getByText("asset-2")).toBeInTheDocument();
    expect(screen.getByText("asset-3")).toBeInTheDocument();
  });

  it("each card has an <img> pointing at the preview proxy URL", async () => {
    setupFetchMock();
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    await openAssetsTab();
    await screen.findByText("asset-1");
    const imgs = screen.getAllByRole("img");
    const srcs = imgs.map((i) => i.getAttribute("src"));
    expect(srcs).toContain("/api/assets/asset-1/preview");
    expect(srcs).toContain("/api/assets/asset-2/preview");
    expect(srcs).toContain("/api/assets/asset-3/preview");
  });

  it("override select POSTs with asset_id, category, and CSRF", async () => {
    const { calls } = setupFetchMock();
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    await openAssetsTab();
    await screen.findByText("asset-1");
    const selects = screen.getAllByLabelText("Override category");
    await userEvent.selectOptions(selects[0], "pets");
    await waitFor(() => {
      const overrideCall = calls.find(
        (c) =>
          c.url.endsWith("/api/me/assets/asset-1/category") &&
          c.init?.method === "POST",
      );
      expect(overrideCall).toBeDefined();
      const headers = new Headers(overrideCall!.init!.headers);
      expect(headers.get("X-CSRF-Token")).toBe("csrf-token");
      expect(JSON.parse(overrideCall!.init!.body as string).category_id).toBe(
        "pets",
      );
    });
  });

  it("pagination next/prev calls list API with the right cursor", async () => {
    const { calls } = setupFetchMock();
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    await openAssetsTab();
    await screen.findByText("asset-1");
    await userEvent.click(screen.getByText("Next"));
    await screen.findByText("asset-4");
    expect(
      calls.some((c) => c.url.includes("/api/me/assets?cursor=asset-3")),
    ).toBe(true);
    await userEvent.click(screen.getByText("Previous"));
    await screen.findByText("asset-1");
    // After prev, last list call should be the unparameterised page.
    const listCalls = calls.filter(
      (c) => c.url.includes("/api/me/assets") && !c.url.includes("/category"),
    );
    expect(listCalls[listCalls.length - 1].url).not.toContain("cursor=");
  });

  it("never persists asset state to localStorage / sessionStorage", async () => {
    setupFetchMock();
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    await openAssetsTab();
    await screen.findByText("asset-1");
    const selects = screen.getAllByLabelText("Override category");
    await userEvent.selectOptions(selects[0], "pets");
    await screen.findByText("Saved.");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });

  it("loads asset detail and displays OCR text with document reasons", async () => {
    setupFetchMock();
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    await openAssetsTab();
    await screen.findByText("asset-1");
    await userEvent.click(screen.getAllByText("Details")[0]);
    expect(await screen.findByText(/Invoice number 123/)).toBeInTheDocument();
    expect(screen.getByText("keyword")).toBeInTheDocument();
  });

  it("submits semantic search mode and displays source score", async () => {
    const { calls, state } = setupFetchMock();
    state.pages[""].assets = [
      {
        ...state.pages[""].assets[0],
        search_source: "immich_smart_search",
        search_score: 0.876,
      },
    ];
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    await openAssetsTab();
    await screen.findByText("asset-1");

    await userEvent.type(screen.getByLabelText("Search"), "snow vacation");
    await userEvent.click(screen.getByRole("button", { name: "Semantic" }));
    await userEvent.click(screen.getByText("Apply filters"));

    await waitFor(() => {
      const listCalls = calls.filter((c) => c.url.includes("/api/me/assets"));
      const listCall = listCalls[listCalls.length - 1];
      expect(listCall?.url).toContain("q=snow+vacation");
      expect(listCall?.url).toContain("search_mode=semantic");
    });
    expect(
      await screen.findByText("Search: Immich Smart Search (0.876)"),
    ).toBeInTheDocument();
  });
});
