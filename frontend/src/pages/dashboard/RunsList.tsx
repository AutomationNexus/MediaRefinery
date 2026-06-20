import { Dialog } from "@headlessui/react";
import { useEffect, useState } from "react";
import { ScanSummary, listScans, undoScan } from "../../api/client";
import { t } from "../../lib/i18n";

interface Props {
  onOpenRun: (runId: number) => void;
}

export default function RunsList({ onOpenRun }: Props) {
  const [scans, setScans] = useState<ScanSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [undoTarget, setUndoTarget] = useState<number | null>(null);
  const [undoBusy, setUndoBusy] = useState(false);
  const [undoMessage, setUndoMessage] = useState<string | null>(null);

  async function refresh() {
    setError(null);
    try {
      setScans(await listScans());
    } catch (err) {
      setError((err as Error).message);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function confirmUndo() {
    if (undoTarget === null) return;
    setUndoBusy(true);
    try {
      const reverted = await undoScan(undoTarget);
      setUndoMessage(t("runs.undo.done", { n: reverted }));
      setUndoTarget(null);
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setUndoBusy(false);
    }
  }

  return (
    <section aria-labelledby="runs-title" className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 id="runs-title" className="text-lg font-semibold">
          {t("runs.title")}
        </h2>
        <button
          type="button"
          onClick={refresh}
          className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
        >
          {t("dashboard.refresh")}
        </button>
      </div>

      {error !== null && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      )}
      {undoMessage !== null && (
        <p role="status" aria-live="polite" className="text-sm text-emerald-700 dark:text-emerald-400">
          {undoMessage}
        </p>
      )}

      {scans === null ? (
        <p role="status" aria-live="polite">…</p>
      ) : scans.length === 0 ? (
        <p>{t("runs.empty")}</p>
      ) : (
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-300 dark:border-slate-600">
            <tr>
              <th scope="col" className="py-2">{t("runs.col.run")}</th>
              <th scope="col">{t("runs.col.status")}</th>
              <th scope="col">{t("runs.col.started")}</th>
              <th scope="col">{t("runs.col.ended")}</th>
              <th scope="col" className="text-right">{t("runs.col.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {scans.map((scan) => (
              <tr
                key={scan.run_id}
                className="border-b border-slate-100 dark:border-slate-800"
              >
                <td className="py-2">#{scan.run_id}</td>
                <td>{scan.status}</td>
                <td>{scan.started_at ?? ""}</td>
                <td>{scan.ended_at ?? ""}</td>
                <td className="space-x-2 text-right">
                  <button
                    type="button"
                    onClick={() => onOpenRun(scan.run_id)}
                    className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
                  >
                    {t("runs.open")}
                  </button>
                  {scan.status === "completed" && (
                    <button
                      type="button"
                      onClick={() => setUndoTarget(scan.run_id)}
                      className="rounded border border-amber-400 px-2 py-1 text-xs text-amber-800 hover:bg-amber-50 dark:border-amber-500 dark:text-amber-200 dark:hover:bg-amber-900/30"
                    >
                      {t("runs.undo")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <Dialog
        open={undoTarget !== null}
        onClose={() => (undoBusy ? null : setUndoTarget(null))}
        className="relative z-50"
      >
        <div className="fixed inset-0 bg-black/40" aria-hidden="true" />
        <div className="fixed inset-0 flex items-center justify-center p-4">
          <Dialog.Panel className="w-full max-w-md rounded-lg bg-white p-6 shadow-lg dark:bg-slate-800">
            <Dialog.Title className="text-base font-semibold">
              {undoTarget !== null
                ? t("runs.undo.confirm.title", { run_id: undoTarget })
                : ""}
            </Dialog.Title>
            <Dialog.Description className="mt-2 text-sm text-slate-600 dark:text-slate-300">
              {t("runs.undo.confirm.body")}
            </Dialog.Description>
            <div className="mt-4 flex justify-end space-x-2">
              <button
                type="button"
                onClick={() => setUndoTarget(null)}
                disabled={undoBusy}
                className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
              >
                {t("runs.undo.cancel")}
              </button>
              <button
                type="button"
                onClick={confirmUndo}
                disabled={undoBusy}
                className="rounded bg-amber-600 px-3 py-1 text-sm text-white hover:bg-amber-700 disabled:opacity-60"
              >
                {t("runs.undo.confirm")}
              </button>
            </div>
          </Dialog.Panel>
        </div>
      </Dialog>
    </section>
  );
}
