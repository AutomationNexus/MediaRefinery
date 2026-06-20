import { useEffect, useState } from "react";
import {
  AssetRow,
  EventGroup,
  getCategories,
  listEventGroups,
  listAssets,
  setAssetCategory,
} from "../../../api/client";
import { t } from "../../../lib/i18n";
import AssetCard from "./AssetCard";

export default function AssetsList() {
  const [assets, setAssets] = useState<AssetRow[] | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [history, setHistory] = useState<(string | null)[]>([null]);
  const [error, setError] = useState<string | null>(null);
  const [categories, setCategories] = useState<string[]>([]);
  const [events, setEvents] = useState<EventGroup[]>([]);
  const [queue, setQueue] = useState("");
  const [mediaKind, setMediaKind] = useState("");
  const [eventId, setEventId] = useState("");
  const [query, setQuery] = useState("");
  const [searchMode, setSearchMode] = useState<"metadata" | "semantic">("metadata");

  async function load(cursor: string | null) {
    setError(null);
    setAssets(null);
    try {
      const r = await listAssets(cursor ?? undefined, {
        queue: queue || undefined,
        media_kind: mediaKind || undefined,
        event_id: eventId || undefined,
        q: query || undefined,
        search_mode: searchMode,
      });
      setAssets(r.assets);
      setNextCursor(r.next_cursor);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  useEffect(() => {
    (async () => {
      try {
        const c = await getCategories();
        setCategories(Object.keys(c.categories ?? {}));
      } catch {
        // categories list is optional UX sugar
      }
      try {
        const e = await listEventGroups();
        setEvents(e);
      } catch {
        // event list is optional UX sugar
      }
    })();
    load(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleOverride(assetId: string, categoryId: string | null) {
    await setAssetCategory(assetId, categoryId);
    setAssets((prev) =>
      prev === null
        ? prev
        : prev.map((a) =>
            a.asset_id === assetId ? { ...a, last_seen_category: categoryId } : a,
          ),
    );
  }

  function goNext() {
    if (nextCursor === null) return;
    setHistory((h) => [...h, nextCursor]);
    load(nextCursor);
  }

  function goPrev() {
    if (history.length <= 1) return;
    const trimmed = history.slice(0, -1);
    setHistory(trimmed);
    load(trimmed[trimmed.length - 1]);
  }

  const atFirstPage = history.length <= 1;
  const atLastPage = nextCursor === null;
  const queues = [
    "",
    "nsfw",
    "adult_subtypes",
    "sfw",
    "documents",
    "duplicates",
    "quality",
    "people",
    "review_needed",
    "custom",
  ];

  return (
    <section aria-labelledby="assets-title" className="space-y-4">
      <h2 id="assets-title" className="text-lg font-semibold">
        {t("assets.title")}
      </h2>
      <div className="grid gap-3 rounded border border-slate-200 p-3 text-sm dark:border-slate-700 sm:grid-cols-[1fr_auto_auto_auto]">
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">
            {t("assets.search.label")}
          </span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                setHistory([null]);
                load(null);
              }
            }}
            className="w-full rounded border border-slate-300 bg-white px-2 py-1 dark:border-slate-600 dark:bg-slate-700"
            placeholder={t("assets.search.placeholder")}
          />
        </label>
        <div className="block">
          <span className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">
            {t("assets.search.mode.label")}
          </span>
          <div
            className="inline-flex rounded border border-slate-300 p-0.5 dark:border-slate-600"
            role="group"
            aria-label={t("assets.search.mode.label")}
          >
            {(["metadata", "semantic"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                aria-pressed={searchMode === mode}
                onClick={() => setSearchMode(mode)}
                className={`px-2 py-1 text-xs ${
                  searchMode === mode
                    ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                    : "text-slate-700 hover:bg-slate-50 dark:text-slate-100 dark:hover:bg-slate-700"
                }`}
              >
                {t(`assets.search.mode.${mode}`)}
              </button>
            ))}
          </div>
        </div>
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">
            {t("assets.queue.label")}
          </span>
          <select
            value={queue}
            onChange={(e) => setQueue(e.target.value)}
            className="w-full rounded border border-slate-300 bg-white px-2 py-1 dark:border-slate-600 dark:bg-slate-700"
          >
            {queues.map((name) => (
              <option key={name || "all"} value={name}>
                {name || t("assets.queue.all")}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">
            {t("assets.media_kind.label")}
          </span>
          <select
            value={mediaKind}
            onChange={(e) => setMediaKind(e.target.value)}
            className="w-full rounded border border-slate-300 bg-white px-2 py-1 dark:border-slate-600 dark:bg-slate-700"
          >
            <option value="">{t("assets.queue.all")}</option>
            <option value="image">image</option>
            <option value="video">video</option>
            <option value="gif">gif</option>
          </select>
        </label>
        <label className="block sm:col-span-2">
          <span className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">
            {t("assets.event.label")}
          </span>
          <select
            value={eventId}
            onChange={(e) => setEventId(e.target.value)}
            className="w-full rounded border border-slate-300 bg-white px-2 py-1 dark:border-slate-600 dark:bg-slate-700"
          >
            <option value="">{t("assets.queue.all")}</option>
            {events.map((event) => (
              <option key={event.event_id} value={event.event_id}>
                {event.title}
              </option>
            ))}
          </select>
        </label>
        <div className="sm:col-span-4">
          <button
            type="button"
            onClick={() => {
              setHistory([null]);
              load(null);
            }}
            className="rounded border border-slate-300 px-3 py-1 text-xs hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
          >
            {t("assets.filter.apply")}
          </button>
        </div>
      </div>
      {error !== null && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      )}
      {assets === null ? (
        <p role="status" aria-live="polite">
          {t("assets.loading")}
        </p>
      ) : assets.length === 0 ? (
        <p>{t("assets.empty")}</p>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {assets.map((asset) => (
            <AssetCard
              key={asset.asset_id}
              asset={asset}
              categories={categories}
              onOverride={handleOverride}
            />
          ))}
        </div>
      )}
      <div className="flex items-center justify-end space-x-2 text-sm">
        <button
          type="button"
          onClick={goPrev}
          disabled={atFirstPage}
          className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50 disabled:opacity-50 dark:border-slate-600 dark:hover:bg-slate-700"
        >
          {t("assets.prev")}
        </button>
        <button
          type="button"
          onClick={goNext}
          disabled={atLastPage}
          className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50 disabled:opacity-50 dark:border-slate-600 dark:hover:bg-slate-700"
        >
          {t("assets.next")}
        </button>
      </div>
    </section>
  );
}
