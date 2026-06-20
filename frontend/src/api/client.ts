// Thin fetch wrapper for the MediaRefinery service API.
//
// Privacy contract:
//   - We never persist the password or any other credential to
//     localStorage / sessionStorage / IndexedDB. The only state the
//     browser keeps after login is the cookies the server sets
//     (signed session + CSRF), which is correct for HttpOnly auth.
//   - Every request sends `credentials: "include"` so cookies travel
//     on same-origin and the dev-proxy origin.
//   - State-changing requests echo the CSRF cookie value into the
//     X-CSRF-Token header (double-submit pattern matched by the
//     backend require_csrf dependency).

export interface LoginPayload {
  immich_url: string;
  username: string;
  password: string;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: "invalid" | "unreachable" | "generic",
    message: string,
  ) {
    super(message);
  }
}

function readCookie(name: string): string | null {
  const prefix = `${name}=`;
  for (const part of document.cookie.split(";")) {
    const trimmed = part.trim();
    if (trimmed.startsWith(prefix)) {
      return decodeURIComponent(trimmed.slice(prefix.length));
    }
  }
  return null;
}

export async function login(payload: LoginPayload): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch("/api/auth/login", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (err) {
    throw new ApiError(0, "unreachable", (err as Error).message);
  }
  if (resp.ok) return;
  if (resp.status === 401 || resp.status === 400) {
    throw new ApiError(resp.status, "invalid", "invalid credentials");
  }
  throw new ApiError(resp.status, "generic", `unexpected status ${resp.status}`);
}

export function csrfHeader(): Record<string, string> {
  const token = readCookie("mr_csrf");
  return token ? { "X-CSRF-Token": token } : {};
}

// Single state-changing fetch wrapper. Always credentials:include, always
// echoes the mr_csrf cookie via X-CSRF-Token (double-submit pattern).
// Reused by every wizard mutation so the CSRF rule has one home.
export async function csrfFetch(
  url: string,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  for (const [k, v] of Object.entries(csrfHeader())) {
    headers.set(k, v);
  }
  return fetch(url, { ...init, credentials: "include", headers });
}

export interface BootstrapStatus {
  terms_accepted: boolean;
  users_exist: boolean;
  admin_present: boolean;
  ready: boolean;
}

export async function getBootstrap(): Promise<BootstrapStatus> {
  const r = await fetch("/api/setup/bootstrap", {
    credentials: "include",
  });
  if (!r.ok) throw new ApiError(r.status, "generic", "bootstrap status failed");
  return (await r.json()) as BootstrapStatus;
}

export async function postBootstrap(): Promise<void> {
  const r = await csrfFetch("/api/setup/bootstrap", {
    method: "POST",
    body: JSON.stringify({ accept_terms: true }),
  });
  if (!r.ok && r.status !== 409) {
    throw new ApiError(r.status, "generic", `bootstrap failed (${r.status})`);
  }
}

export interface MeResponse {
  user_id: string;
  email: string;
  name: string | null;
  is_admin: boolean;
}

export async function getMe(): Promise<MeResponse | null> {
  const r = await fetch("/api/me", { credentials: "include" });
  if (r.status === 401) return null;
  if (!r.ok) throw new ApiError(r.status, "generic", `me failed (${r.status})`);
  return (await r.json()) as MeResponse;
}

export interface CatalogModel {
  id: string;
  name: string;
  kind: string;
  task?: string;
  status: string;
  description?: string | null;
  license: string;
  license_url: string | null;
  size_bytes: number;
  sha256: string;
  presets: string[];
  installed: boolean;
  installable: boolean;
}

export async function getCatalog(): Promise<CatalogModel[]> {
  const r = await fetch("/api/models/catalog", { credentials: "include" });
  if (!r.ok) throw new ApiError(r.status, "generic", `catalog failed (${r.status})`);
  const data = (await r.json()) as { models: CatalogModel[] };
  return data.models;
}

export interface InstalledModel {
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

export async function getInstalledModels(): Promise<InstalledModel[]> {
  const r = await fetch("/api/models", { credentials: "include" });
  if (!r.ok) throw new ApiError(r.status, "generic", `models failed (${r.status})`);
  const data = (await r.json()) as { installed: InstalledModel[] };
  return data.installed;
}

export async function installModel(modelId: string): Promise<void> {
  const r = await csrfFetch("/api/models/install", {
    method: "POST",
    body: JSON.stringify({ model_id: modelId, license_accepted: true }),
  });
  if (!r.ok) {
    throw new ApiError(r.status, "generic", `install failed (${r.status})`);
  }
}

export interface AdultSubtypeProfilePayload {
  model_id: string;
  name?: string | null;
  model_path: string;
  output_labels: string[];
  thresholds: Record<string, number>;
  admin_acknowledgement: boolean;
  input_size?: number;
  input_mean?: [number, number, number] | null;
  input_std?: [number, number, number] | null;
  input_name?: string | null;
  output_name?: string | null;
}

export async function registerAdultSubtypeProfile(
  payload: AdultSubtypeProfilePayload,
): Promise<void> {
  const r = await csrfFetch("/api/models/adult-subtype-profile", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    throw new ApiError(
      r.status,
      "generic",
      `adult subtype profile failed (${r.status})`,
    );
  }
}

export async function uninstallModel(registryId: number): Promise<void> {
  const r = await csrfFetch(`/api/models/${registryId}`, { method: "DELETE" });
  if (r.status === 204) return;
  if (!r.ok) {
    throw new ApiError(r.status, "generic", `uninstall failed (${r.status})`);
  }
}

export interface ApiKeySummary {
  id: number;
  label: string | null;
  created_at: string;
}

export interface ApiKeyStatus {
  api_keys: ApiKeySummary[];
  required_for_scans: boolean;
}

export async function getApiKeyStatus(): Promise<ApiKeyStatus> {
  const r = await fetch("/api/me/api-key", { credentials: "include" });
  if (!r.ok) throw new ApiError(r.status, "generic", `api key list failed (${r.status})`);
  const data = (await r.json()) as Partial<ApiKeyStatus> & {
    api_keys: ApiKeySummary[];
  };
  return {
    api_keys: data.api_keys,
    required_for_scans: data.required_for_scans ?? true,
  };
}

export async function listApiKeys(): Promise<ApiKeySummary[]> {
  return (await getApiKeyStatus()).api_keys;
}

export async function storeApiKey(
  apiKey: string,
  label = "primary",
  validate = true,
): Promise<void> {
  const r = await csrfFetch("/api/me/api-key", {
    method: "POST",
    body: JSON.stringify({ api_key: apiKey, label, validate_api_key: validate }),
  });
  if (!r.ok) {
    throw new ApiError(r.status, "generic", `api key store failed (${r.status})`);
  }
}

export interface ScanResponse {
  run_id: number;
  status: string;
}

export async function startScan(): Promise<ScanResponse> {
  const r = await csrfFetch("/api/scans", { method: "POST" });
  if (!r.ok) {
    if (r.status === 409) {
      try {
        const body = (await r.json()) as { detail?: string };
        if (body.detail === "api_key_required") {
          throw new ApiError(r.status, "generic", "Immich API key required");
        }
      } catch (err) {
        if (err instanceof ApiError) throw err;
      }
    }
    throw new ApiError(r.status, "generic", `scan failed (${r.status})`);
  }
  return (await r.json()) as ScanResponse;
}

export interface ScanSummary {
  run_id: number;
  status: string;
  started_at: string | null;
  ended_at: string | null;
}

export async function listScans(): Promise<ScanSummary[]> {
  const r = await fetch("/api/scans", { credentials: "include" });
  if (!r.ok) throw new ApiError(r.status, "generic", `list scans failed (${r.status})`);
  const data = (await r.json()) as { scans: ScanSummary[] };
  return data.scans;
}

export interface ScanAction {
  action_name: string;
  asset_id: string;
  success: boolean | null;
  error_code: string | null;
}

export interface ScanDetail extends ScanSummary {
  summary_json: string | null;
  actions: ScanAction[];
}

export async function getScan(runId: number): Promise<ScanDetail> {
  const r = await fetch(`/api/scans/${runId}`, { credentials: "include" });
  if (!r.ok) throw new ApiError(r.status, "generic", `get scan failed (${r.status})`);
  return (await r.json()) as ScanDetail;
}

export async function undoScan(runId: number): Promise<number> {
  const r = await csrfFetch(`/api/scans/${runId}/undo`, { method: "POST" });
  if (!r.ok) throw new ApiError(r.status, "generic", `undo failed (${r.status})`);
  const data = (await r.json()) as { reverted: number };
  return data.reverted;
}

export interface AuditEntry {
  id: number;
  at: string;
  action: string;
  target_asset_id: string | null;
  run_id: number | null;
}

export async function listAudit(): Promise<AuditEntry[]> {
  const r = await fetch("/api/audit", { credentials: "include" });
  if (!r.ok) throw new ApiError(r.status, "generic", `audit failed (${r.status})`);
  const data = (await r.json()) as { entries: AuditEntry[] };
  return data.entries;
}

export interface CategoriesResponse {
  categories: Record<string, unknown>;
  active_model_sha256: string | null;
  last_seen_model_sha256: string | null;
  needs_reclassify: boolean;
}

export async function getCategories(): Promise<CategoriesResponse> {
  const r = await fetch("/api/me/categories", { credentials: "include" });
  if (!r.ok) throw new ApiError(r.status, "generic", `categories failed (${r.status})`);
  return (await r.json()) as CategoriesResponse;
}

export async function putCategories(
  categories: Record<string, unknown>,
): Promise<void> {
  const r = await csrfFetch("/api/me/categories", {
    method: "PUT",
    body: JSON.stringify({ categories }),
  });
  if (!r.ok) throw new ApiError(r.status, "generic", `put categories (${r.status})`);
}

export async function getPolicies(): Promise<Record<string, unknown>> {
  const r = await fetch("/api/me/policies", { credentials: "include" });
  if (!r.ok) throw new ApiError(r.status, "generic", `policies failed (${r.status})`);
  const data = (await r.json()) as { policies: Record<string, unknown> };
  return data.policies;
}

export async function putPolicies(
  policies: Record<string, unknown>,
): Promise<void> {
  const r = await csrfFetch("/api/me/policies", {
    method: "PUT",
    body: JSON.stringify({ policies }),
  });
  if (!r.ok) throw new ApiError(r.status, "generic", `put policies (${r.status})`);
}

export interface AssetRow {
  asset_id: string;
  media_type: string;
  last_action: string | null;
  last_run_id: number | null;
  last_seen_category: string | null;
  analysis?: AssetAnalysisSummary | null;
  event_id?: string | null;
  event_title?: string | null;
  can_override: boolean;
  search_source?: string | null;
  search_score?: number | null;
}

export interface AssetAnalysisSummary {
  primary_category_id: string | null;
  media_kind: string | null;
  mime_type: string | null;
  safety_label: string | null;
  safety_confidence: number | null;
  review_needed: boolean;
  sfw_facets: string[];
  custom_categories: string[];
  review_queues: string[];
  people_count: number;
  quality_flags: string[];
  duplicate_key: string | null;
  document_type: string | null;
  ocr_available: boolean;
  event_key: string | null;
  adult_subtype_status?: string | null;
  adult_subtype_top_label?: string | null;
  adult_subtype_review_needed?: boolean;
}

export interface AssetListResponse {
  assets: AssetRow[];
  next_cursor: string | null;
  search_mode?: "metadata" | "semantic";
  search_source?: string | null;
  search_unavailable_reason?: string | null;
}

export async function listAssets(
  cursor?: string | null,
  filters: {
    queue?: string;
    media_kind?: string;
    event_id?: string;
    q?: string;
    search_mode?: "metadata" | "semantic";
  } = {},
): Promise<AssetListResponse> {
  const params = new URLSearchParams();
  if (cursor) params.set("cursor", cursor);
  if (filters.queue) params.set("queue", filters.queue);
  if (filters.media_kind) params.set("media_kind", filters.media_kind);
  if (filters.event_id) params.set("event_id", filters.event_id);
  if (filters.q) params.set("q", filters.q);
  if (filters.search_mode) params.set("search_mode", filters.search_mode);
  const qs = params.toString() ? `?${params.toString()}` : "";
  const r = await fetch(`/api/me/assets${qs}`, { credentials: "include" });
  if (!r.ok) throw new ApiError(r.status, "generic", `assets failed (${r.status})`);
  return (await r.json()) as AssetListResponse;
}

export async function getAssetDetail(assetId: string): Promise<{
  asset_id: string;
  analysis: Record<string, unknown>;
}> {
  const r = await fetch(`/api/me/assets/${encodeURIComponent(assetId)}`, {
    credentials: "include",
  });
  if (!r.ok) throw new ApiError(r.status, "generic", `asset detail failed (${r.status})`);
  return (await r.json()) as { asset_id: string; analysis: Record<string, unknown> };
}

export async function setAssetCategory(
  assetId: string,
  categoryId: string | null,
): Promise<void> {
  const r = await csrfFetch(
    `/api/me/assets/${encodeURIComponent(assetId)}/category`,
    {
      method: "POST",
      body: JSON.stringify({ category_id: categoryId }),
    },
  );
  if (!r.ok) throw new ApiError(r.status, "generic", `override failed (${r.status})`);
}

export interface EventGroup {
  event_id: string;
  auto_key: string | null;
  title: string;
  status: string;
  sort_at: string | null;
  source: Record<string, unknown>;
  created_at?: string | null;
  updated_at?: string | null;
  asset_count: number;
}

export interface EventDetailResponse {
  event: EventGroup;
  assets: AssetRow[];
  next_cursor: string | null;
}

export async function listEventGroups(): Promise<EventGroup[]> {
  const r = await fetch("/api/me/events", { credentials: "include" });
  if (!r.ok) throw new ApiError(r.status, "generic", `events failed (${r.status})`);
  const data = (await r.json()) as { events: EventGroup[] };
  return data.events;
}

export async function getEventGroup(
  eventId: string,
  cursor?: string | null,
): Promise<EventDetailResponse> {
  const params = new URLSearchParams();
  if (cursor) params.set("cursor", cursor);
  const qs = params.toString() ? `?${params.toString()}` : "";
  const r = await fetch(`/api/me/events/${encodeURIComponent(eventId)}${qs}`, {
    credentials: "include",
  });
  if (!r.ok) throw new ApiError(r.status, "generic", `event detail failed (${r.status})`);
  return (await r.json()) as EventDetailResponse;
}

export async function renameEventGroup(
  eventId: string,
  title: string,
): Promise<EventGroup> {
  const r = await csrfFetch(
    `/api/me/events/${encodeURIComponent(eventId)}/rename`,
    {
      method: "POST",
      body: JSON.stringify({ title }),
    },
  );
  if (!r.ok) throw new ApiError(r.status, "generic", `event rename failed (${r.status})`);
  const data = (await r.json()) as { event: EventGroup };
  return data.event;
}

export async function mergeEventGroups(
  targetEventId: string,
  sourceEventIds: string[],
): Promise<EventGroup> {
  const r = await csrfFetch("/api/me/events/merge", {
    method: "POST",
    body: JSON.stringify({
      target_event_id: targetEventId,
      source_event_ids: sourceEventIds,
    }),
  });
  if (!r.ok) throw new ApiError(r.status, "generic", `event merge failed (${r.status})`);
  const data = (await r.json()) as { event: EventGroup };
  return data.event;
}

export async function splitEventGroup(
  eventId: string,
  title: string,
  assetIds: string[],
): Promise<EventGroup> {
  const r = await csrfFetch(
    `/api/me/events/${encodeURIComponent(eventId)}/split`,
    {
      method: "POST",
      body: JSON.stringify({ title, asset_ids: assetIds }),
    },
  );
  if (!r.ok) throw new ApiError(r.status, "generic", `event split failed (${r.status})`);
  const data = (await r.json()) as { event: EventGroup };
  return data.event;
}

export async function removeAssetFromEvent(
  eventId: string,
  assetId: string,
): Promise<void> {
  const r = await csrfFetch(
    `/api/me/events/${encodeURIComponent(eventId)}/assets/${encodeURIComponent(assetId)}/remove`,
    { method: "POST" },
  );
  if (!r.ok) throw new ApiError(r.status, "generic", `event asset remove failed (${r.status})`);
}

export async function resetEventGroup(eventId: string): Promise<void> {
  const r = await csrfFetch(
    `/api/me/events/${encodeURIComponent(eventId)}/reset`,
    { method: "POST" },
  );
  if (!r.ok) throw new ApiError(r.status, "generic", `event reset failed (${r.status})`);
}

export interface UnlockResponse {
  run_id: number;
  reverted: number;
  failed_asset_ids: string[];
}

export async function unlockLockedFolder(
  runId: number,
  pin: string,
): Promise<UnlockResponse> {
  const r = await csrfFetch("/api/me/locked-folder/unlock", {
    method: "POST",
    body: JSON.stringify({ run_id: runId, pin }),
  });
  if (!r.ok) {
    let code: "pin" | "empty" | "upstream" | "notfound" | "generic" = "generic";
    if (r.status === 401) code = "pin";
    else if (r.status === 400) code = "empty";
    else if (r.status === 502) code = "upstream";
    else if (r.status === 404) code = "notfound";
    throw new ApiError(r.status, "generic", code);
  }
  return (await r.json()) as UnlockResponse;
}

export async function deleteMe(): Promise<void> {
  const r = await csrfFetch("/api/me", { method: "DELETE" });
  if (r.status === 204) return;
  if (!r.ok) throw new ApiError(r.status, "generic", `delete me failed (${r.status})`);
}

export interface AutoScanSettings {
  enabled: boolean;
  interval_minutes: number;
  last_seen_taken_at: string | null;
  last_run_at: string | null;
  last_status: "ok" | "error" | null;
  last_error_code: string | null;
}

export async function getAutoScan(): Promise<AutoScanSettings> {
  const r = await fetch("/api/me/auto-scan", { credentials: "include" });
  if (!r.ok) throw new ApiError(r.status, "generic", `auto-scan failed (${r.status})`);
  return (await r.json()) as AutoScanSettings;
}

export async function putAutoScan(
  enabled: boolean,
  interval_minutes: number,
): Promise<AutoScanSettings> {
  const r = await csrfFetch("/api/me/auto-scan", {
    method: "PUT",
    body: JSON.stringify({ enabled, interval_minutes }),
  });
  if (!r.ok) throw new ApiError(r.status, "generic", `put auto-scan (${r.status})`);
  return (await r.json()) as AutoScanSettings;
}

export async function logout(): Promise<void> {
  const r = await csrfFetch("/api/auth/logout", { method: "POST" });
  if (!r.ok && r.status !== 204) {
    throw new ApiError(r.status, "generic", `logout failed (${r.status})`);
  }
}

export interface SystemConfig {
  immich_base_url?: string;
  base_url?: string;
  trusted_proxies?: string;
  session_ttl_seconds?: number;
  revalidate_interval_seconds?: number;
  login_rate_per_min?: number;
  auto_scan_enabled?: boolean;
  demo_mode?: boolean;
  media_sampling?: {
    enabled?: boolean;
    max_original_bytes?: number;
    max_duration_seconds?: number;
    max_frames?: number;
    extraction_timeout_seconds?: number;
    ffmpeg_path?: string;
  };
  ocr?: {
    enabled?: boolean;
    max_inputs?: number;
    max_text_chars?: number;
  };
}

export async function getSystemConfig(): Promise<SystemConfig> {
  const r = await fetch("/api/admin/config", { credentials: "include" });
  if (!r.ok) {
    throw new ApiError(r.status, "generic", `system config failed (${r.status})`);
  }
  return (await r.json()) as SystemConfig;
}

export async function patchSystemConfig(
  keyPath: string,
  value: unknown,
): Promise<void> {
  const r = await csrfFetch(`/api/admin/config/${encodeURIComponent(keyPath)}`, {
    method: "PATCH",
    body: JSON.stringify({ value }),
  });
  if (!r.ok) {
    throw new ApiError(r.status, "generic", `patch system config (${r.status})`);
  }
}
