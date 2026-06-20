import { FormEvent, useEffect, useState } from "react";
import {
  AssetRow,
  EventDetailResponse,
  EventGroup,
  getEventGroup,
  listEventGroups,
  mergeEventGroups,
  removeAssetFromEvent,
  renameEventGroup,
  resetEventGroup,
  splitEventGroup,
} from "../../api/client";
import { t } from "../../lib/i18n";

export default function EventsTab() {
  const [events, setEvents] = useState<EventGroup[]>([]);
  const [detail, setDetail] = useState<EventDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const [mergeSourceId, setMergeSourceId] = useState("");
  const [splitTitle, setSplitTitle] = useState("");
  const [selectedAssetIds, setSelectedAssetIds] = useState<Set<string>>(new Set());

  async function loadEvents(openEventId?: string | null) {
    setError(null);
    const rows = await listEventGroups();
    setEvents(rows);
    if (openEventId) {
      await openEvent(openEventId, rows);
    }
  }

  async function openEvent(eventId: string, knownEvents = events) {
    setError(null);
    setDetailLoading(true);
    try {
      const response = await getEventGroup(eventId);
      setDetail(response);
      setRenameTitle(response.event.title);
      setMergeSourceId(knownEvents.find((event) => event.event_id !== eventId)?.event_id ?? "");
      setSelectedAssetIds(new Set());
    } catch (err) {
      setDetail(null);
      setError((err as Error).message);
    } finally {
      setDetailLoading(false);
    }
  }

  useEffect(() => {
    (async () => {
      try {
        await loadEvents(null);
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleRename(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (detail === null) return;
    setMessage(null);
    setError(null);
    try {
      const updated = await renameEventGroup(detail.event.event_id, renameTitle);
      setDetail({ ...detail, event: updated });
      await loadEvents(updated.event_id);
      setMessage(t("events.rename.saved"));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function handleMerge() {
    if (detail === null || !mergeSourceId) return;
    setMessage(null);
    setError(null);
    try {
      const updated = await mergeEventGroups(detail.event.event_id, [mergeSourceId]);
      await loadEvents(updated.event_id);
      setMessage(t("events.merge.saved"));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function handleSplit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (detail === null || selectedAssetIds.size === 0) return;
    setMessage(null);
    setError(null);
    try {
      const created = await splitEventGroup(
        detail.event.event_id,
        splitTitle,
        Array.from(selectedAssetIds),
      );
      setSplitTitle("");
      await loadEvents(created.event_id);
      setMessage(t("events.split.saved"));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function handleRemove(assetId: string) {
    if (detail === null) return;
    setMessage(null);
    setError(null);
    try {
      await removeAssetFromEvent(detail.event.event_id, assetId);
      await loadEvents(detail.event.event_id);
      setMessage(t("events.remove.saved"));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function handleReset() {
    if (detail === null) return;
    setMessage(null);
    setError(null);
    try {
      await resetEventGroup(detail.event.event_id);
      await loadEvents(null);
      setDetail(null);
      setMessage(t("events.reset.saved"));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  function toggleAsset(assetId: string) {
    setSelectedAssetIds((prev) => {
      const next = new Set(prev);
      if (next.has(assetId)) next.delete(assetId);
      else next.add(assetId);
      return next;
    });
  }

  const selectedEventId = detail?.event.event_id ?? null;
  const mergeOptions = events.filter((event) => event.event_id !== selectedEventId);

  return (
    <section aria-labelledby="events-title" className="space-y-4">
      <div>
        <h2 id="events-title" className="text-lg font-semibold">
          {t("events.title")}
        </h2>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">
          {t("events.body")}
        </p>
      </div>

      {error !== null && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      )}
      {message !== null && (
        <p role="status" className="text-sm text-emerald-700 dark:text-emerald-400">
          {message}
        </p>
      )}

      {loading ? (
        <p role="status">{t("events.loading")}</p>
      ) : events.length === 0 ? (
        <p>{t("events.empty")}</p>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,260px)_1fr]">
          <EventList
            events={events}
            selectedEventId={selectedEventId}
            onOpen={(eventId) => void openEvent(eventId)}
          />
          <div className="min-w-0">
            {detailLoading ? (
              <p role="status">{t("events.detail.loading")}</p>
            ) : detail === null ? (
              <p className="text-sm text-slate-600 dark:text-slate-300">
                {t("events.detail.empty")}
              </p>
            ) : (
              <div className="space-y-5">
                <header className="space-y-1 border-b border-slate-200 pb-3 dark:border-slate-700">
                  <h3 className="text-base font-semibold">{detail.event.title}</h3>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    {t("events.detail.count", { n: detail.event.asset_count })}
                    {sourceSummary(detail.event)}
                  </p>
                </header>

                <form onSubmit={handleRename} className="flex flex-wrap gap-2 text-sm">
                  <label className="min-w-0 flex-1">
                    <span className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">
                      {t("events.rename.label")}
                    </span>
                    <input
                      value={renameTitle}
                      onChange={(event) => setRenameTitle(event.target.value)}
                      className="w-full rounded border border-slate-300 bg-white px-2 py-1 dark:border-slate-600 dark:bg-slate-700"
                    />
                  </label>
                  <button
                    type="submit"
                    className="self-end rounded border border-slate-300 px-3 py-1 text-xs hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
                  >
                    {t("events.rename.action")}
                  </button>
                </form>

                <div className="flex flex-wrap gap-2 text-sm">
                  <label className="min-w-0 flex-1">
                    <span className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">
                      {t("events.merge.label")}
                    </span>
                    <select
                      value={mergeSourceId}
                      onChange={(event) => setMergeSourceId(event.target.value)}
                      className="w-full rounded border border-slate-300 bg-white px-2 py-1 dark:border-slate-600 dark:bg-slate-700"
                    >
                      <option value="">{t("events.merge.none")}</option>
                      {mergeOptions.map((event) => (
                        <option key={event.event_id} value={event.event_id}>
                          {event.title}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    type="button"
                    disabled={!mergeSourceId}
                    onClick={handleMerge}
                    className="self-end rounded border border-slate-300 px-3 py-1 text-xs hover:bg-slate-50 disabled:opacity-50 dark:border-slate-600 dark:hover:bg-slate-700"
                  >
                    {t("events.merge.action")}
                  </button>
                  <button
                    type="button"
                    onClick={handleReset}
                    className="self-end rounded border border-slate-300 px-3 py-1 text-xs hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
                  >
                    {t("events.reset.action")}
                  </button>
                </div>

                <form onSubmit={handleSplit} className="space-y-2 text-sm">
                  <label className="block">
                    <span className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">
                      {t("events.split.label")}
                    </span>
                    <input
                      value={splitTitle}
                      onChange={(event) => setSplitTitle(event.target.value)}
                      className="w-full rounded border border-slate-300 bg-white px-2 py-1 dark:border-slate-600 dark:bg-slate-700"
                    />
                  </label>
                  <button
                    type="submit"
                    disabled={!splitTitle.trim() || selectedAssetIds.size === 0}
                    className="rounded border border-slate-300 px-3 py-1 text-xs hover:bg-slate-50 disabled:opacity-50 dark:border-slate-600 dark:hover:bg-slate-700"
                  >
                    {t("events.split.action", { n: selectedAssetIds.size })}
                  </button>
                </form>

                <EventAssets
                  assets={detail.assets}
                  selectedAssetIds={selectedAssetIds}
                  onToggle={toggleAsset}
                  onRemove={(assetId) => void handleRemove(assetId)}
                />
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function EventList({
  events,
  selectedEventId,
  onOpen,
}: {
  events: EventGroup[];
  selectedEventId: string | null;
  onOpen: (eventId: string) => void;
}) {
  return (
    <div className="space-y-2">
      {events.map((event) => {
        const selected = event.event_id === selectedEventId;
        return (
          <button
            key={event.event_id}
            type="button"
            onClick={() => onOpen(event.event_id)}
            className={
              "block w-full rounded border px-3 py-2 text-left text-sm " +
              (selected
                ? "border-blue-600 bg-blue-50 dark:bg-blue-950/30"
                : "border-slate-200 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-700")
            }
          >
            <span className="block truncate font-medium">{event.title}</span>
            <span className="text-xs text-slate-500 dark:text-slate-400">
              {t("events.list.count", { n: event.asset_count })}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function EventAssets({
  assets,
  selectedAssetIds,
  onToggle,
  onRemove,
}: {
  assets: AssetRow[];
  selectedAssetIds: Set<string>;
  onToggle: (assetId: string) => void;
  onRemove: (assetId: string) => void;
}) {
  if (assets.length === 0) return <p>{t("events.assets.empty")}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200 dark:border-slate-700">
            <th scope="col" className="py-2 pr-3">
              {t("events.assets.select")}
            </th>
            <th scope="col" className="py-2 pr-3">
              {t("events.assets.asset")}
            </th>
            <th scope="col" className="py-2 pr-3">
              {t("events.assets.kind")}
            </th>
            <th scope="col" className="py-2 pr-3">
              {t("events.assets.action")}
            </th>
          </tr>
        </thead>
        <tbody>
          {assets.map((asset) => (
            <tr key={asset.asset_id} className="border-b border-slate-100 dark:border-slate-800">
              <td className="py-2 pr-3">
                <input
                  type="checkbox"
                  checked={selectedAssetIds.has(asset.asset_id)}
                  onChange={() => onToggle(asset.asset_id)}
                  aria-label={t("events.assets.select_asset", {
                    asset_id: asset.asset_id,
                  })}
                />
              </td>
              <td className="py-2 pr-3 font-mono text-xs">{asset.asset_id}</td>
              <td className="py-2 pr-3">{asset.analysis?.media_kind ?? asset.media_type}</td>
              <td className="py-2 pr-3">
                <button
                  type="button"
                  onClick={() => onRemove(asset.asset_id)}
                  className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
                >
                  {t("events.remove.action")}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function sourceSummary(event: EventGroup): string {
  const day = typeof event.source.day === "string" ? event.source.day : null;
  const place = typeof event.source.place === "string" ? event.source.place : null;
  const parts = [day, place].filter(Boolean);
  return parts.length ? ` - ${parts.join(" - ")}` : "";
}
