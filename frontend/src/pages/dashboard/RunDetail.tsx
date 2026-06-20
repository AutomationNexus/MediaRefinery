import { useEffect, useState } from "react";
import { ScanDetail, getScan } from "../../api/client";
import { t } from "../../lib/i18n";

interface Props {
  runId: number;
  onBack: () => void;
}

export default function RunDetail({ runId, onBack }: Props) {
  const [run, setRun] = useState<ScanDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const detail = await getScan(runId);
        if (!cancelled) setRun(detail);
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [runId]);

  return (
    <section aria-labelledby="run-detail-title" className="space-y-4">
      <button
        type="button"
        onClick={onBack}
        className="text-sm text-blue-700 hover:underline dark:text-blue-300"
      >
        {t("runs.detail.back")}
      </button>
      <h2 id="run-detail-title" className="text-lg font-semibold">
        {t("runs.detail.title", { run_id: runId })}
      </h2>

      {error !== null && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      )}

      {run === null && error === null ? (
        <p role="status" aria-live="polite">…</p>
      ) : run === null ? null : run.actions.length === 0 ? (
        <p>{t("runs.detail.empty")}</p>
      ) : (
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-300 dark:border-slate-600">
            <tr>
              <th scope="col" className="py-2">{t("runs.detail.col.action")}</th>
              <th scope="col">{t("runs.detail.col.asset")}</th>
              <th scope="col">{t("runs.detail.col.outcome")}</th>
              <th scope="col">{t("runs.detail.col.error")}</th>
            </tr>
          </thead>
          <tbody>
            {run.actions.map((action, idx) => (
              <tr
                key={`${action.asset_id}-${idx}`}
                className="border-b border-slate-100 dark:border-slate-800"
              >
                <td className="py-2 font-mono text-xs">{action.action_name}</td>
                <td className="font-mono text-xs">{action.asset_id}</td>
                <td>
                  {action.success === null
                    ? t("runs.detail.outcome.dry")
                    : action.success
                      ? t("runs.detail.outcome.ok")
                      : t("runs.detail.outcome.fail")}
                </td>
                <td className="font-mono text-xs text-red-700 dark:text-red-400">
                  {action.error_code ?? ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
