import { useEffect, useState } from "react";
import { AutoScanSettings, getAutoScan, putAutoScan } from "../../../api/client";
import { t } from "../../../lib/i18n";

const INTERVAL_OPTIONS = [5, 15, 30, 60, 120, 360, 720, 1440];

export default function AutoScanPanel() {
  const [settings, setSettings] = useState<AutoScanSettings | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [interval, setInterval] = useState(30);
  const [saving, setSaving] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const s = await getAutoScan();
        setSettings(s);
        setEnabled(s.enabled);
        setInterval(s.interval_minutes);
      } catch {
        setLoadError(t("dashboard.error.generic"));
      }
    })();
  }, []);

  async function onSave() {
    setSaving(true);
    setError(null);
    setSavedFlash(false);
    try {
      const s = await putAutoScan(enabled, interval);
      setSettings(s);
      setEnabled(s.enabled);
      setInterval(s.interval_minutes);
      setSavedFlash(true);
    } catch {
      setError(t("settings.auto_scan.error"));
    } finally {
      setSaving(false);
    }
  }

  if (loadError !== null) {
    return (
      <section className="space-y-2">
        <h3 className="font-semibold">{t("settings.auto_scan.title")}</h3>
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {loadError}
        </p>
      </section>
    );
  }
  if (settings === null) {
    return null;
  }

  const serverDisabled =
    !settings.enabled &&
    settings.last_error_code === "upstream_session_expired";

  return (
    <section className="space-y-3">
      <h3 className="font-semibold">{t("settings.auto_scan.title")}</h3>
      <p className="text-sm text-slate-600 dark:text-slate-300">
        {t("settings.auto_scan.body")}
      </p>

      {serverDisabled && (
        <p
          role="status"
          aria-live="polite"
          className="rounded border border-amber-300 bg-amber-50 p-2 text-sm dark:border-amber-700 dark:bg-amber-900/20"
        >
          {t("settings.auto_scan.disabled_by_server")}
        </p>
      )}

      <label className="flex items-center space-x-2 text-sm">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
        />
        <span>{t("settings.auto_scan.toggle")}</span>
      </label>

      <label className="flex items-center space-x-2 text-sm">
        <span>{t("settings.auto_scan.interval")}</span>
        <select
          aria-label={t("settings.auto_scan.interval")}
          value={interval}
          onChange={(e) => setInterval(Number(e.target.value))}
          className="rounded border border-slate-300 bg-white px-2 py-1 dark:border-slate-600 dark:bg-slate-700"
        >
          {INTERVAL_OPTIONS.map((m) => (
            <option key={m} value={m}>
              {t("settings.auto_scan.interval.minutes", { n: m })}
            </option>
          ))}
        </select>
      </label>

      <dl className="text-sm text-slate-600 dark:text-slate-300">
        <div className="flex space-x-2">
          <dt>{t("settings.auto_scan.last_run")}:</dt>
          <dd>
            {settings.last_run_at ?? t("settings.auto_scan.last_run.never")}
            {settings.last_status === "ok" &&
              ` — ${t("settings.auto_scan.status.ok")}`}
            {settings.last_status === "error" &&
              ` — ${t("settings.auto_scan.status.error")}${
                settings.last_error_code
                  ? ` (${describeError(settings.last_error_code)})`
                  : ""
              }`}
          </dd>
        </div>
      </dl>

      {error !== null && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      )}
      {savedFlash && (
        <p
          role="status"
          aria-live="polite"
          className="text-sm text-emerald-700 dark:text-emerald-400"
        >
          {t("settings.auto_scan.saved")}
        </p>
      )}

      <button
        type="button"
        onClick={onSave}
        disabled={saving}
        className="rounded bg-blue-600 px-3 py-1 text-white hover:bg-blue-700 disabled:opacity-60"
      >
        {saving
          ? t("settings.auto_scan.saving")
          : t("settings.auto_scan.save")}
      </button>
    </section>
  );
}

function describeError(code: string): string {
  return t(`settings.auto_scan.error_code.${code}`);
}
