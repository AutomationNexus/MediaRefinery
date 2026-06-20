import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DashboardPage from "../pages/dashboard/DashboardPage";

const ADMIN = {
  user_id: "u1",
  email: "admin@x.invalid",
  name: "Admin",
  is_admin: true,
};
const NON_ADMIN = { ...ADMIN, user_id: "u2", email: "nope@x.invalid", is_admin: false };

interface InstalledRow {
  id: number;
  name: string;
  version: string;
  sha256: string;
  license: string | null;
  kind?: string;
  active_slot?: string;
  active: boolean;
  present_on_disk: boolean;
}
interface CatalogRow {
  id: string;
  name: string;
  kind: string;
  status: string;
  license: string;
  license_url: string | null;
  size_bytes: number;
  sha256: string;
  presets: string[];
  installed: boolean;
  installable: boolean;
}

interface MockState {
  installed: InstalledRow[];
  catalog: CatalogRow[];
}

function setupFetchMock(initial: Partial<MockState> = {}) {
  const state: MockState = {
    installed: [
      {
        id: 11,
        name: "ResNet50",
        version: "model-a",
        sha256: "abc123def456abc123def456",
        license: "MIT",
        active: true,
        present_on_disk: true,
      },
    ],
    catalog: [
      {
        id: "resnet50",
        name: "ResNet50",
        kind: "image",
        status: "available",
        license: "MIT",
        license_url: "https://example.invalid/license",
        size_bytes: 100 * 1024 * 1024,
        sha256: "abc123def456abc123def456",
        presets: [],
        installed: true,
        installable: true,
      },
      {
        id: "vit-b16",
        name: "ViT-B/16",
        kind: "image",
        status: "available",
        license: "Apache-2.0",
        license_url: null,
        size_bytes: 350 * 1024 * 1024,
        sha256: "fffaaa111222fffaaa111222",
        presets: [],
        installed: false,
        installable: true,
      },
    ],
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
        categories: {},
        active_model_sha256: null,
        last_seen_model_sha256: null,
        needs_reclassify: false,
      });
    }
    if (u.endsWith("/api/scans") && method === "GET") {
      return json({ scans: [] });
    }
    if (u.endsWith("/api/models") && method === "GET") {
      return json({ installed: state.installed });
    }
    if (u.endsWith("/api/models/catalog") && method === "GET") {
      return json({ models: state.catalog });
    }
    if (u.endsWith("/api/models/install") && method === "POST") {
      const body = JSON.parse((init?.body as string) ?? "{}");
      const entry = state.catalog.find((c) => c.id === body.model_id);
      if (!entry) return new Response("", { status: 404 });
      const nextId = state.installed.length + 100;
      state.installed = state.installed.map((m) => ({ ...m, active: false }));
      state.installed.push({
        id: nextId,
        name: entry.name,
        version: "model-a",
        sha256: entry.sha256,
        license: entry.license,
        active: true,
        present_on_disk: true,
      });
      state.catalog = state.catalog.map((c) =>
        c.id === entry.id ? { ...c, installed: true } : c,
      );
      return json({ id: nextId, model_id: "model-a" }, 201);
    }
    if (u.endsWith("/api/models/adult-subtype-profile") && method === "POST") {
      const body = JSON.parse((init?.body as string) ?? "{}");
      state.installed = state.installed.map((m) =>
        m.active_slot === "adult_subtype" ? { ...m, active: false } : m,
      );
      state.installed.push({
        id: 202,
        name: body.name || body.model_id,
        version: body.model_id,
        sha256: "subtype-sha",
        license: "user-supplied",
        kind: "adult_subtype_classifier",
        active_slot: "adult_subtype",
        active: true,
        present_on_disk: true,
      });
      return json({ id: 202, model_id: body.model_id, active_slot: "adult_subtype" }, 201);
    }
    const delMatch = u.match(/\/api\/models\/(\d+)$/);
    if (delMatch && method === "DELETE") {
      const id = Number(delMatch[1]);
      const target = state.installed.find((m) => m.id === id);
      state.installed = state.installed.filter((m) => m.id !== id);
      if (target) {
        state.catalog = state.catalog.map((c) =>
          c.sha256 === target.sha256 ? { ...c, installed: false } : c,
        );
      }
      return new Response(null, { status: 204 });
    }
    return new Response("", { status: 404 });
  };
  (globalThis as unknown as { fetch: typeof fetch }).fetch = vi.fn(
    handler,
  ) as unknown as typeof fetch;
  return { calls, state };
}

describe("ModelsTab — admin gating", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    document.cookie = "mr_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  it("hides the Models tab from non-admins", () => {
    setupFetchMock();
    render(<DashboardPage me={NON_ADMIN} onLoggedOut={() => undefined} />);
    expect(screen.queryByRole("tab", { name: "Models" })).toBeNull();
  });

  it("shows the Models tab for admins", () => {
    setupFetchMock();
    render(<DashboardPage me={ADMIN} onLoggedOut={() => undefined} />);
    expect(screen.getByRole("tab", { name: "Models" })).toBeInTheDocument();
  });
});

describe("ModelsTab — render and actions", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    document.cookie = "mr_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  async function openModels() {
    await userEvent.click(screen.getByRole("tab", { name: "Models" }));
  }

  it("lists installed and catalog entries with active marker", async () => {
    setupFetchMock();
    render(<DashboardPage me={ADMIN} onLoggedOut={() => undefined} />);
    await openModels();
    expect(await screen.findByText("Active")).toBeInTheDocument();
    expect(screen.getByText("ViT-B/16")).toBeInTheDocument();
    expect(screen.getByText("Already installed")).toBeInTheDocument();
  });

  it("install requires license acceptance and POSTs with CSRF + license_accepted=true", async () => {
    const { calls } = setupFetchMock();
    render(<DashboardPage me={ADMIN} onLoggedOut={() => undefined} />);
    await openModels();
    await screen.findByText("ViT-B/16");
    await userEvent.click(screen.getByRole("button", { name: "Install" }));
    const dialog = await screen.findByRole("dialog");
    const dialogConfirm = Array.from(dialog.querySelectorAll("button")).find(
      (b) => b.textContent === "Install",
    )!;
    expect(dialogConfirm).toBeDisabled();
    await userEvent.click(dialog.querySelector("input[type=checkbox]")!);
    expect(dialogConfirm).toBeEnabled();
    await userEvent.click(dialogConfirm);
    await waitFor(() =>
      expect(screen.getAllByText("Already installed").length).toBeGreaterThan(1),
    );
    const installCall = calls.find(
      (c) =>
        c.url.endsWith("/api/models/install") && c.init?.method === "POST",
    );
    expect(installCall).toBeDefined();
    const body = JSON.parse(installCall!.init!.body as string);
    expect(body).toEqual({ model_id: "vit-b16", license_accepted: true });
    expect(new Headers(installCall!.init!.headers).get("X-CSRF-Token")).toBe(
      "csrf-token",
    );
  });

  it("uninstall confirm dialog DELETEs the registry id with CSRF", async () => {
    const { calls } = setupFetchMock();
    render(<DashboardPage me={ADMIN} onLoggedOut={() => undefined} />);
    await openModels();
    await screen.findByText("Active");
    await userEvent.click(screen.getByRole("button", { name: "Uninstall" }));
    const dialog = await screen.findByRole("dialog");
    const confirm = Array.from(dialog.querySelectorAll("button")).find(
      (b) => b.textContent === "Uninstall",
    )!;
    await userEvent.click(confirm);
    await waitFor(() => {
      expect(
        calls.find(
          (c) =>
            c.url.endsWith("/api/models/11") && c.init?.method === "DELETE",
        ),
      ).toBeDefined();
    });
    const delCall = calls.find(
      (c) => c.url.endsWith("/api/models/11") && c.init?.method === "DELETE",
    );
    expect(new Headers(delCall!.init!.headers).get("X-CSRF-Token")).toBe(
      "csrf-token",
    );
  });

  it("renders catalog entry size and licence link", async () => {
    setupFetchMock();
    render(<DashboardPage me={ADMIN} onLoggedOut={() => undefined} />);
    await openModels();
    expect(await screen.findByText(/Download size: 100\.0 MB/)).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "View licence text" });
    expect(link).toHaveAttribute("href", "https://example.invalid/license");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("registers an acknowledged adult subtype profile with labels and thresholds", async () => {
    const { calls } = setupFetchMock();
    render(<DashboardPage me={ADMIN} onLoggedOut={() => undefined} />);
    await openModels();
    expect(await screen.findByText("Adult subtype profile")).toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("Model ID"));
    await userEvent.type(screen.getByLabelText("Model ID"), "local-subtypes");
    await userEvent.type(screen.getByLabelText("Display name"), "Local Subtypes");
    await userEvent.type(
      screen.getByLabelText("Server model path"),
      "D:\\models\\subtypes.onnx",
    );
    await userEvent.type(
      screen.getByLabelText("Output labels"),
      "custom_one, custom_two",
    );
    const thresholdInput = screen.getByLabelText("Thresholds JSON");
    await userEvent.clear(thresholdInput);
    fireEvent.change(thresholdInput, {
      target: { value: '{"custom_one":0.7,"custom_two":0.8}' },
    });
    await userEvent.click(
      screen.getByLabelText(/probabilistic, model-dependent/),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Register subtype profile" }),
    );
    await screen.findByText("Subtype profile registered.");
    const call = calls.find(
      (c) =>
        c.url.endsWith("/api/models/adult-subtype-profile") &&
        c.init?.method === "POST",
    );
    expect(call).toBeDefined();
    expect(JSON.parse(call!.init!.body as string)).toMatchObject({
      model_id: "local-subtypes",
      name: "Local Subtypes",
      model_path: "D:\\models\\subtypes.onnx",
      output_labels: ["custom_one", "custom_two"],
      thresholds: { custom_one: 0.7, custom_two: 0.8 },
      admin_acknowledgement: true,
    });
  });
});
