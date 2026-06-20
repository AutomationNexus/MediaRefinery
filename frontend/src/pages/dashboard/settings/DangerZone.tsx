import { Dialog } from "@headlessui/react";
import { useState } from "react";
import { MeResponse, deleteMe } from "../../../api/client";
import { t } from "../../../lib/i18n";

interface Props {
  me: MeResponse;
  onLoggedOut: () => void;
}

export default function DangerZone({ me, onLoggedOut }: Props) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const matches = typed === me.email;

  function close() {
    if (busy) return;
    setOpen(false);
    setTyped("");
    setError(null);
  }

  async function confirm() {
    if (!matches) return;
    setBusy(true);
    setError(null);
    try {
      await deleteMe();
      onLoggedOut();
    } catch {
      setError(t("settings.danger.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      aria-labelledby="danger-title"
      className="space-y-3 rounded border border-red-300 bg-red-50 p-4 dark:border-red-700 dark:bg-red-900/10"
    >
      <h3 id="danger-title" className="text-base font-semibold text-red-800 dark:text-red-300">
        {t("settings.danger.title")}
      </h3>
      <p className="text-sm">{t("settings.danger.body")}</p>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded bg-red-600 px-3 py-1 text-sm text-white hover:bg-red-700"
      >
        {t("settings.danger.action")}
      </button>

      <Dialog open={open} onClose={close} className="relative z-50">
        <div className="fixed inset-0 bg-black/40" aria-hidden="true" />
        <div className="fixed inset-0 flex items-center justify-center p-4">
          <Dialog.Panel className="w-full max-w-md rounded-lg bg-white p-6 shadow-lg dark:bg-slate-800">
            <Dialog.Title className="text-base font-semibold">
              {t("settings.danger.dialog.title")}
            </Dialog.Title>
            <Dialog.Description className="mt-2 text-sm text-slate-600 dark:text-slate-300">
              {t("settings.danger.dialog.body", { email: me.email })}
            </Dialog.Description>
            <div className="mt-4 space-y-2">
              <label htmlFor="danger-confirm" className="block text-sm font-medium">
                {t("settings.danger.confirm_label", { email: me.email })}
              </label>
              <input
                id="danger-confirm"
                type="text"
                autoComplete="off"
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                disabled={busy}
                className="w-full rounded border border-slate-300 px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-900"
              />
              {error !== null && (
                <p role="alert" className="text-sm text-red-700 dark:text-red-400">
                  {error}
                </p>
              )}
            </div>
            <div className="mt-4 flex justify-end space-x-2">
              <button
                type="button"
                onClick={close}
                disabled={busy}
                className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
              >
                {t("settings.danger.cancel")}
              </button>
              <button
                type="button"
                onClick={confirm}
                disabled={busy || !matches}
                className="rounded bg-red-600 px-3 py-1 text-sm text-white hover:bg-red-700 disabled:opacity-60"
              >
                {busy ? t("settings.danger.deleting") : t("settings.danger.confirm")}
              </button>
            </div>
          </Dialog.Panel>
        </div>
      </Dialog>
    </section>
  );
}
