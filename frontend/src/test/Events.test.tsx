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

interface EventGroup {
  event_id: string;
  auto_key: string | null;
  title: string;
  status: string;
  sort_at: string | null;
  source: Record<string, unknown>;
  asset_count: number;
}

interface AssetRow {
  asset_id: string;
  media_type: string;
  last_action: string | null;
  last_run_id: number | null;
  last_seen_category: string | null;
  analysis: {
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
}

function setupFetchMock() {
  const events: EventGroup[] = [
    {
      event_id: "event-1",
      auto_key: "2026-01-01::berlin",
      title: "Berlin trip",
      status: "auto",
      sort_at: "2026-01-01",
      source: { day: "2026-01-01", place: "Berlin" },
      asset_count: 2,
    },
    {
      event_id: "event-2",
      auto_key: "2026-01-02::munich",
      title: "Munich visit",
      status: "auto",
      sort_at: "2026-01-02",
      source: { day: "2026-01-02", place: "Munich" },
      asset_count: 1,
    },
  ];
  const asset = (assetId: string): AssetRow => ({
    asset_id: assetId,
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
      event_key: "2026-01-01::berlin",
    },
    can_override: true,
  });
  const details: Record<string, AssetRow[]> = {
    "event-1": [asset("asset-1"), asset("asset-2")],
    "event-2": [asset("asset-3")],
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

    if (u.endsWith("/api/me/categories") && method === "GET") {
      return json({
        categories: {},
        active_model_sha256: "demo",
        last_seen_model_sha256: "demo",
        needs_reclassify: false,
      });
    }
    if (u.endsWith("/api/me/events") && method === "GET") {
      return json({ events });
    }
    const detailMatch = u.match(/\/api\/me\/events\/([^/?]+)$/);
    if (detailMatch && method === "GET") {
      const eventId = decodeURIComponent(detailMatch[1]);
      const event = events.find((item) => item.event_id === eventId);
      if (!event) return json({ detail: "not found" }, 404);
      return json({
        event: { ...event, asset_count: details[eventId]?.length ?? 0 },
        assets: details[eventId] ?? [],
        next_cursor: null,
      });
    }
    const renameMatch = u.match(/\/api\/me\/events\/([^/]+)\/rename$/);
    if (renameMatch && method === "POST") {
      const eventId = decodeURIComponent(renameMatch[1]);
      const title = JSON.parse((init?.body as string) ?? "{}").title as string;
      const event = events.find((item) => item.event_id === eventId)!;
      event.title = title;
      event.status = "manual";
      return json({ event });
    }
    if (u.endsWith("/api/me/events/merge") && method === "POST") {
      const body = JSON.parse((init?.body as string) ?? "{}") as {
        target_event_id: string;
        source_event_ids: string[];
      };
      const target = events.find((item) => item.event_id === body.target_event_id)!;
      for (const sourceId of body.source_event_ids) {
        details[target.event_id].push(...(details[sourceId] ?? []));
        delete details[sourceId];
        const idx = events.findIndex((item) => item.event_id === sourceId);
        if (idx >= 0) events.splice(idx, 1);
      }
      target.asset_count = details[target.event_id].length;
      target.status = "manual";
      return json({ event: target });
    }
    const splitMatch = u.match(/\/api\/me\/events\/([^/]+)\/split$/);
    if (splitMatch && method === "POST") {
      const sourceId = decodeURIComponent(splitMatch[1]);
      const body = JSON.parse((init?.body as string) ?? "{}") as {
        title: string;
        asset_ids: string[];
      };
      const splitEvent: EventGroup = {
        event_id: "event-split",
        auto_key: null,
        title: body.title,
        status: "manual",
        sort_at: null,
        source: {},
        asset_count: body.asset_ids.length,
      };
      const selected = (details[sourceId] ?? []).filter((row) =>
        body.asset_ids.includes(row.asset_id),
      );
      details[sourceId] = (details[sourceId] ?? []).filter(
        (row) => !body.asset_ids.includes(row.asset_id),
      );
      details[splitEvent.event_id] = selected;
      events.push(splitEvent);
      return json({ event: splitEvent });
    }
    const removeMatch = u.match(
      /\/api\/me\/events\/([^/]+)\/assets\/([^/]+)\/remove$/,
    );
    if (removeMatch && method === "POST") {
      const eventId = decodeURIComponent(removeMatch[1]);
      const assetId = decodeURIComponent(removeMatch[2]);
      details[eventId] = (details[eventId] ?? []).filter(
        (row) => row.asset_id !== assetId,
      );
      return json({ event_id: eventId, asset_id: assetId, removed: true });
    }
    if (u.match(/\/api\/me\/events\/([^/]+)\/reset$/) && method === "POST") {
      return json({ event_id: "event-1", reset_assets: 1 });
    }
    if (u.endsWith("/api/scans") && method === "GET") return json({ scans: [] });
    if (u.endsWith("/api/audit") && method === "GET") return json({ entries: [] });
    return new Response("", { status: 404 });
  };

  (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn(
    handler,
  ) as unknown as typeof fetch;
  return { calls };
}

async function openEventsTab() {
  await userEvent.click(screen.getByRole("tab", { name: "Events" }));
}

describe("EventsTab", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    document.cookie = "mr_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  it("lists event groups and loads event assets", async () => {
    setupFetchMock();
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    await openEventsTab();
    await screen.findByText("Berlin trip");
    await userEvent.click(screen.getByText("Berlin trip"));
    expect(await screen.findByText("asset-1")).toBeInTheDocument();
    expect(screen.getByText("asset-2")).toBeInTheDocument();
  });

  it("submits rename, merge, split, remove, and reset operations with CSRF", async () => {
    const { calls } = setupFetchMock();
    render(<DashboardPage me={ME} onLoggedOut={() => undefined} />);
    await openEventsTab();
    await userEvent.click(await screen.findByText("Berlin trip"));

    const renameInput = screen.getByLabelText("Event name");
    await userEvent.clear(renameInput);
    await userEvent.type(renameInput, "Winter Berlin");
    await userEvent.click(screen.getByRole("button", { name: "Rename" }));
    await screen.findByText("Event renamed.");

    await userEvent.selectOptions(
      screen.getByLabelText("Merge another event into this one"),
      "event-2",
    );
    await userEvent.click(screen.getByRole("button", { name: "Merge" }));
    await screen.findByText("Events merged.");

    await userEvent.click(screen.getByLabelText("Select asset-1"));
    await userEvent.type(
      screen.getByLabelText("New event name for selected assets"),
      "Museum stop",
    );
    await userEvent.click(screen.getByRole("button", { name: "Split 1 asset(s)" }));
    await screen.findByText("Assets split into a new event.");

    await userEvent.click(screen.getAllByRole("button", { name: "Remove" })[0]);
    await screen.findByText("Asset removed from event.");

    await userEvent.click(
      screen.getByRole("button", { name: "Reset automatic grouping" }),
    );
    await screen.findByText("Automatic grouping restored.");

    await waitFor(() => {
      const mutationCalls = calls.filter((call) => call.init?.method === "POST");
      expect(mutationCalls).toHaveLength(5);
      expect(
        mutationCalls.every((call) => {
          const headers = new Headers(call.init?.headers);
          return headers.get("X-CSRF-Token") === "csrf-token";
        }),
      ).toBe(true);
    });
  });
});
