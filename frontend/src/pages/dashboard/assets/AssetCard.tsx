import { useId, useState } from "react";
import { AssetRow, getAssetDetail } from "../../../api/client";
import { t } from "../../../lib/i18n";

interface Props {
  asset: AssetRow;
  categories: string[];
  onOverride: (assetId: string, categoryId: string | null) => Promise<void>;
}

export default function AssetCard({ asset, categories, onOverride }: Props) {
  const selectId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detail, setDetail] = useState<AssetDetailAnalysis | null>(null);

  async function handleChange(value: string) {
    const next = value === "" ? null : value;
    setBusy(true);
    setError(null);
    try {
      await onOverride(asset.asset_id, next);
      setSavedAt(Date.now());
    } catch {
      setError(t("assets.override.error"));
    } finally {
      setBusy(false);
    }
  }

  async function toggleDetail() {
    const nextOpen = !detailOpen;
    setDetailOpen(nextOpen);
    if (!nextOpen || detail !== null) return;
    setDetailLoading(true);
    setDetailError(null);
    try {
      const response = await getAssetDetail(asset.asset_id);
      setDetail(response.analysis as AssetDetailAnalysis);
    } catch {
      setDetailError(t("assets.detail.error"));
    } finally {
      setDetailLoading(false);
    }
  }

  const current = asset.last_seen_category ?? "";
  const analysis = asset.analysis ?? null;
  const badges = Array.from(
    new Set(
      [
        analysis?.media_kind,
        analysis?.safety_label,
        analysis?.adult_subtype_top_label,
        analysis?.document_type && analysis.document_type !== "none"
          ? analysis.document_type
          : null,
        ...(analysis?.quality_flags ?? []),
        ...(analysis?.review_queues ?? []).slice(0, 3),
      ].filter((value): value is string => Boolean(value)),
    ),
  );
  const ocrText = detail?.ocr?.text?.trim() ?? "";
  const documentReasons = detail?.document?.reasons ?? [];
  const subtypeLabels = detail?.adult_subtypes?.labels ?? [];
  const searchSource = asset.search_source
    ? t(`assets.search.source.${asset.search_source}`)
    : null;
  const searchScore =
    typeof asset.search_score === "number" ? asset.search_score.toFixed(3) : null;
  const eventLabel = asset.event_title ?? analysis?.event_key ?? null;

  return (
    <figure className="rounded border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-800">
      <img
        src={`/api/assets/${encodeURIComponent(asset.asset_id)}/preview`}
        alt={t("assets.preview.alt", { asset_id: asset.asset_id })}
        className="h-40 w-full rounded object-cover"
      />
      <figcaption className="mt-2 space-y-2 text-sm">
        <div className="font-mono text-xs text-slate-600 dark:text-slate-300">
          {asset.asset_id}
        </div>
        {asset.last_run_id !== null && (
          <div className="text-xs text-slate-500 dark:text-slate-400">
            {t("assets.col.run", { run_id: asset.last_run_id })}
            {asset.last_action !== null && ` - ${asset.last_action}`}
          </div>
        )}
        {searchSource !== null && (
          <div className="text-xs text-slate-500 dark:text-slate-400">
            {t("assets.search.result", {
              source: searchSource,
              score: searchScore ?? t("assets.search.score.none"),
            })}
          </div>
        )}
        {analysis !== null && (
          <div className="space-y-2 rounded border border-slate-200 p-2 text-xs dark:border-slate-700">
            <div className="flex flex-wrap gap-1">
              {badges.length === 0 ? (
                <span className="text-slate-500">{t("assets.analysis.none")}</span>
              ) : (
                badges.map((badge) => (
                  <span
                    key={badge}
                    className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-700 dark:bg-slate-700 dark:text-slate-100"
                  >
                    {badge}
                  </span>
                ))
              )}
            </div>
            <dl className="grid grid-cols-2 gap-x-2 gap-y-1">
              <dt className="text-slate-500">{t("assets.analysis.safety")}</dt>
              <dd>
                {analysis.safety_label ?? "unknown"}
                {analysis.safety_confidence !== null &&
                  ` ${Math.round(analysis.safety_confidence * 100)}%`}
              </dd>
              <dt className="text-slate-500">{t("assets.analysis.people")}</dt>
              <dd>{analysis.people_count}</dd>
              <dt className="text-slate-500">{t("assets.analysis.ocr")}</dt>
              <dd>{analysis.ocr_available ? "yes" : "no"}</dd>
              <dt className="text-slate-500">{t("assets.analysis.event")}</dt>
              <dd className="truncate" title={eventLabel ?? ""}>
                {eventLabel ?? "-"}
              </dd>
            </dl>
          </div>
        )}
        <button
          type="button"
          onClick={toggleDetail}
          className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
        >
          {detailOpen ? t("assets.detail.hide") : t("assets.detail.show")}
        </button>
        {detailOpen && (
          <div className="space-y-2 rounded border border-slate-200 p-2 text-xs dark:border-slate-700">
            {detailLoading ? (
              <p role="status" aria-live="polite">
                {t("assets.detail.loading")}
              </p>
            ) : detailError !== null ? (
              <p role="alert" className="text-red-700 dark:text-red-400">
                {detailError}
              </p>
            ) : detail !== null ? (
              <>
                <dl className="grid grid-cols-2 gap-x-2 gap-y-1">
                  <dt className="text-slate-500">{t("assets.detail.document")}</dt>
                  <dd>{detail.document?.type ?? "-"}</dd>
                  <dt className="text-slate-500">{t("assets.detail.reasons")}</dt>
                  <dd>{documentReasons.length ? documentReasons.join(", ") : "-"}</dd>
                  <dt className="text-slate-500">{t("assets.detail.ocr_status")}</dt>
                  <dd>{detail.ocr?.status ?? "-"}</dd>
                  <dt className="text-slate-500">{t("assets.detail.subtypes")}</dt>
                  <dd>
                    {subtypeLabels.length
                      ? subtypeLabels
                          .map(
                            (item) =>
                              `${item.label} ${Math.round(item.confidence * 100)}%`,
                          )
                          .join(", ")
                      : "-"}
                  </dd>
                </dl>
                <div>
                  <div className="mb-1 font-medium text-slate-600 dark:text-slate-300">
                    {t("assets.detail.ocr_text")}
                  </div>
                  {ocrText ? (
                    <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-50 p-2 font-sans dark:bg-slate-900">
                      {ocrText}
                    </pre>
                  ) : (
                    <p className="text-slate-500">{t("assets.detail.ocr_empty")}</p>
                  )}
                </div>
              </>
            ) : null}
          </div>
        )}
        <label htmlFor={selectId} className="block font-medium">
          {t("assets.override.label")}
        </label>
        <select
          id={selectId}
          value={current}
          disabled={busy || !asset.can_override}
          onChange={(e) => handleChange(e.target.value)}
          className="w-full rounded border border-slate-300 bg-white px-2 py-1 text-sm dark:border-slate-600 dark:bg-slate-700"
        >
          <option value="">{t("assets.override.none")}</option>
          {categories.map((cid) => (
            <option key={cid} value={cid}>
              {cid}
            </option>
          ))}
        </select>
        {busy && (
          <p role="status" aria-live="polite" className="text-xs text-slate-500">
            {t("assets.override.saving")}
          </p>
        )}
        {savedAt !== null && !busy && error === null && (
          <p
            role="status"
            aria-live="polite"
            className="text-xs text-emerald-700 dark:text-emerald-400"
          >
            {t("assets.override.saved")}
          </p>
        )}
        {error !== null && (
          <p role="alert" className="text-xs text-red-700 dark:text-red-400">
            {error}
          </p>
        )}
      </figcaption>
    </figure>
  );
}

interface AssetDetailAnalysis {
  ocr?: {
    text?: string;
    status?: string | null;
    confidence?: number | null;
  };
  document?: {
    type?: string;
    reasons?: string[];
    confidence?: number | null;
  };
  adult_subtypes?: {
    status?: string;
    labels?: { label: string; confidence: number }[];
  };
}
