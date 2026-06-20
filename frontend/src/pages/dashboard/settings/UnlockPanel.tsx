import { Dialog } from "@headlessui/react";
import { useEffect, useState } from "react";
import {
  ApiError,
  ScanSummary,
  UnlockResponse,
  getScan,
  listScans,
  unlockLockedFolder,
} from "../../../api/client";
import { t } from "../../../lib/i18n";

interface RunWithLocked {
  run: ScanSummary;
}

export default function UnlockPanel() {
  const [runs, setRuns] = useState<RunWithLocked[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [target, setTarget] = useState<number | null>(null);
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [result, setResult] = useState<UnlockResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const scans = await listScans();
        const completed = scans.filter((s) => s.status === "completed");
        const details = await Promise.all(
          completed.map((s) =>
            getScan(s.run_id)
              .then((d) => ({ run: s, actions: d.actions }))
              .catch(() => null),
          ),
        );
        const filtered: RunWithLocked[] = [];
        for (const d of details) {
          if (!d) continue;
          if (d.actions.some((a) => a.action_name === "move_to_locked_folder")) {
            filtered.push({ run: d.run });
          }
        }
        if (!cancelled) setRuns(filtered);
      } catch (err) {
        if (!cancelled) setLoadError((err as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  function closeDialog() {
    setTarget(null);
    setPin("");
    setSubmitError(null);
  }

  async function submit() {
    if (target === null) return;
    setBusy(true);
    setSubmitError(null);
    const pinSnapshot = pin;
    try {
      const resp = await unlockLockedFolder(target, pinSnapshot);
      setResult(resp);
      setTarget(null);
    } catch (err) {
      const code =
        err instanceof ApiError
          ? err.message
          : "generic";
      const key =
        code === "pin" || code === "empty" || code === "upstream" || code === "notfound"
          ? `settings.unlock.error.${code}`
          : "settings.unlock.error.generic";
      setSubmitError(t(key));
    } finally {
      setPin("");
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="unlock-title" className="space-y-3">
      <h3 id="unlock-title" className="text-base font-semibold">
        {t("settings.unlock.title")}
      </h3>
      <p className="text-sm text-slate-600 dark:text-slate-300">
        {t("settings.unlock.body")}
      </p>
      {loadError !== null && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {loadError}
        </p>
      )}
      {result !== null && (
        <div role="status" aria-live="polite" className="text-sm text-emerald-700 dark:text-emerald-400">
          <p>{t("settings.unlock.success", { n: result.reverted })}</p>
          {result.failed_asset_ids.length > 0 && (
            <div className="mt-1 text-amber-700 dark:text-amber-400">
              <p>{t("settings.unlock.failed_label")}</p>
              <ul className="ml-4 list-disc font-mono text-xs">
                {result.failed_asset_ids.map((id) => (
                  <li key={id}>{id}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
      {runs === null ? (
        <p role="status" aria-live="polite">…</p>
      ) : runs.length === 0 ? (
        <p className="text-sm">{t("settings.unlock.empty")}</p>
      ) : (
        <ul className="divide-y divide-slate-200 dark:divide-slate-700">
          {runs.map(({ run }) => (
            <li key={run.run_id} className="flex items-center justify-between py-2 text-sm">
              <span>
                {t("settings.unlock.run_label", { run_id: run.run_id })}
                {run.ended_at !== null && (
                  <span className="ml-2 text-slate-500">{run.ended_at}</span>
                )}
              </span>
              <button
                type="button"
                onClick={() => setTarget(run.run_id)}
                className="rounded border border-amber-400 px-2 py-1 text-xs text-amber-800 hover:bg-amber-50 dark:border-amber-500 dark:text-amber-200 dark:hover:bg-amber-900/30"
              >
                {t("settings.unlock.action")}
              </button>
            </li>
          ))}
        </ul>
      )}

      <Dialog
        open={target !== null}
        onClose={() => (busy ? null : closeDialog())}
        className="relative z-50"
      >
        <div className="fixed inset-0 bg-black/40" aria-hidden="true" />
        <div className="fixed inset-0 flex items-center justify-center p-4">
          <Dialog.Panel className="w-full max-w-md rounded-lg bg-white p-6 shadow-lg dark:bg-slate-800">
            <Dialog.Title className="text-base font-semibold">
              {t("settings.unlock.dialog.title")}
            </Dialog.Title>
            <Dialog.Description className="mt-2 text-sm text-slate-600 dark:text-slate-300">
              {t("settings.unlock.dialog.body")}
            </Dialog.Description>
            <div className="mt-4 space-y-2">
              <label htmlFor="unlock-pin" className="block text-sm font-medium">
                {t("settings.unlock.pin_label")}
              </label>
              <input
                id="unlock-pin"
                type="password"
                autoComplete="off"
                value={pin}
                onChange={(e) => setPin(e.target.value)}
                disabled={busy}
                className="w-full rounded border border-slate-300 px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-900"
              />
              {submitError !== null && (
                <p role="alert" className="text-sm text-red-700 dark:text-red-400">
                  {submitError}
                </p>
              )}
            </div>
            <div className="mt-4 flex justify-end space-x-2">
              <button
                type="button"
                onClick={closeDialog}
                disabled={busy}
                className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
              >
                {t("settings.unlock.cancel")}
              </button>
              <button
                type="button"
                onClick={submit}
                disabled={busy || pin.length === 0}
                className="rounded bg-amber-600 px-3 py-1 text-sm text-white hover:bg-amber-700 disabled:opacity-60"
              >
                {busy ? t("settings.unlock.submitting") : t("settings.unlock.submit")}
              </button>
            </div>
          </Dialog.Panel>
        </div>
      </Dialog>
    </section>
  );
}
