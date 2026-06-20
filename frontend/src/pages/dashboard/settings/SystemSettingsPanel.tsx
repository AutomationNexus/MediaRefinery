import { useEffect, useState } from "react";
import {
  SystemConfig,
  getSystemConfig,
  patchSystemConfig,
} from "../../../api/client";
import { t } from "../../../lib/i18n";

type MediaSampling = NonNullable<SystemConfig["media_sampling"]>;
type Ocr = NonNullable<SystemConfig["ocr"]>;

const defaultSampling = (): MediaSampling => ({
  enabled: false,
  max_original_bytes: 262144000,
  max_duration_seconds: 300,
  max_frames: 3,
  extraction_timeout_seconds: 60,
  ffmpeg_path: "ffmpeg",
});

const defaultOcr = (): Ocr => ({
  enabled: true,
  max_inputs: 4,
  max_text_chars: 20000,
});

export default function SystemSettingsPanel() {
  const [form, setForm] = useState<SystemConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const cfg = await getSystemConfig();
        setForm({
          ...cfg,
          media_sampling: { ...defaultSampling(), ...cfg.media_sampling },
          ocr: { ...defaultOcr(), ...cfg.ocr },
        });
      } catch {
        setLoadError(t("settings.system.error.load"));
      }
    })();
  }, []);

  function update<K extends keyof SystemConfig>(key: K, value: SystemConfig[K]) {
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  function updateSampling<K extends keyof MediaSampling>(
    key: K,
    value: MediaSampling[K],
  ) {
    setForm((prev) =>
      prev
        ? {
            ...prev,
            media_sampling: {
              ...defaultSampling(),
              ...prev.media_sampling,
              [key]: value,
            },
          }
        : prev,
    );
  }

  function updateOcr<K extends keyof Ocr>(key: K, value: Ocr[K]) {
    setForm((prev) =>
      prev
        ? {
            ...prev,
            ocr: { ...defaultOcr(), ...prev.ocr, [key]: value },
          }
        : prev,
    );
  }

  async function onSave() {
    if (!form) return;
    setSaving(true);
    setError(null);
    setSavedFlash(false);
    try {
      const patches: Array<[string, unknown]> = [
        ["immich_base_url", form.immich_base_url],
        ["base_url", form.base_url],
        ["trusted_proxies", form.trusted_proxies ?? ""],
        ["demo_mode", form.demo_mode ?? false],
        ["auto_scan_enabled", form.auto_scan_enabled ?? true],
        ["session_ttl_seconds", form.session_ttl_seconds ?? 43200],
        ["revalidate_interval_seconds", form.revalidate_interval_seconds ?? 300],
        ["login_rate_per_min", form.login_rate_per_min ?? 5],
        ["media_sampling.enabled", form.media_sampling?.enabled ?? false],
        [
          "media_sampling.max_frames",
          form.media_sampling?.max_frames ?? 3,
        ],
        ["ocr.enabled", form.ocr?.enabled ?? true],
        ["ocr.max_inputs", form.ocr?.max_inputs ?? 4],
      ];
      for (const [key, value] of patches) {
        await patchSystemConfig(key, value);
      }
      setSavedFlash(true);
    } catch {
      setError(t("settings.system.error.save"));
    } finally {
      setSaving(false);
    }
  }

  if (loadError !== null) {
    return (
      <section className="space-y-2">
        <h3 className="font-semibold">{t("settings.system.title")}</h3>
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {loadError}
        </p>
      </section>
    );
  }
  if (form === null) {
    return null;
  }

  return (
    <section className="space-y-4">
      <h3 className="font-semibold">{t("settings.system.title")}</h3>
      <p className="text-sm text-slate-600 dark:text-slate-300">
        {t("settings.system.body")}
      </p>

      <label className="block space-y-1 text-sm">
        <span>{t("settings.system.immich_url")}</span>
        <input
          type="url"
          className="w-full rounded border border-slate-300 px-2 py-1 dark:border-slate-600 dark:bg-slate-900"
          value={form.immich_base_url ?? ""}
          onChange={(e) => update("immich_base_url", e.target.value)}
        />
      </label>

      <label className="block space-y-1 text-sm">
        <span>{t("settings.system.base_url")}</span>
        <input
          type="url"
          className="w-full rounded border border-slate-300 px-2 py-1 dark:border-slate-600 dark:bg-slate-900"
          value={form.base_url ?? ""}
          onChange={(e) => update("base_url", e.target.value)}
        />
      </label>

      <label className="block space-y-1 text-sm">
        <span>{t("settings.system.trusted_proxies")}</span>
        <input
          type="text"
          className="w-full rounded border border-slate-300 px-2 py-1 dark:border-slate-600 dark:bg-slate-900"
          value={form.trusted_proxies ?? ""}
          onChange={(e) => update("trusted_proxies", e.target.value)}
        />
      </label>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={form.demo_mode ?? false}
          onChange={(e) => update("demo_mode", e.target.checked)}
        />
        <span>{t("settings.system.demo_mode")}</span>
      </label>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={form.auto_scan_enabled ?? true}
          onChange={(e) => update("auto_scan_enabled", e.target.checked)}
        />
        <span>{t("settings.system.auto_scan")}</span>
      </label>

      <div className="grid gap-3 sm:grid-cols-3">
        <label className="block space-y-1 text-sm">
          <span>{t("settings.system.session_ttl")}</span>
          <input
            type="number"
            min={60}
            className="w-full rounded border border-slate-300 px-2 py-1 dark:border-slate-600 dark:bg-slate-900"
            value={form.session_ttl_seconds ?? 43200}
            onChange={(e) =>
              update("session_ttl_seconds", Number(e.target.value))
            }
          />
        </label>
        <label className="block space-y-1 text-sm">
          <span>{t("settings.system.revalidate")}</span>
          <input
            type="number"
            min={30}
            className="w-full rounded border border-slate-300 px-2 py-1 dark:border-slate-600 dark:bg-slate-900"
            value={form.revalidate_interval_seconds ?? 300}
            onChange={(e) =>
              update("revalidate_interval_seconds", Number(e.target.value))
            }
          />
        </label>
        <label className="block space-y-1 text-sm">
          <span>{t("settings.system.login_rate")}</span>
          <input
            type="number"
            min={1}
            className="w-full rounded border border-slate-300 px-2 py-1 dark:border-slate-600 dark:bg-slate-900"
            value={form.login_rate_per_min ?? 5}
            onChange={(e) =>
              update("login_rate_per_min", Number(e.target.value))
            }
          />
        </label>
      </div>

      <fieldset className="space-y-2 rounded border border-slate-200 p-3 dark:border-slate-600">
        <legend className="px-1 text-sm font-medium">
          {t("settings.system.sampling.title")}
        </legend>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.media_sampling?.enabled ?? false}
            onChange={(e) => updateSampling("enabled", e.target.checked)}
          />
          <span>{t("settings.system.sampling.enabled")}</span>
        </label>
        <label className="block space-y-1 text-sm">
          <span>{t("settings.system.sampling.max_frames")}</span>
          <input
            type="number"
            min={1}
            max={10}
            className="w-full rounded border border-slate-300 px-2 py-1 dark:border-slate-600 dark:bg-slate-900"
            value={form.media_sampling?.max_frames ?? 3}
            onChange={(e) =>
              updateSampling("max_frames", Number(e.target.value))
            }
          />
        </label>
      </fieldset>

      <fieldset className="space-y-2 rounded border border-slate-200 p-3 dark:border-slate-600">
        <legend className="px-1 text-sm font-medium">
          {t("settings.system.ocr.title")}
        </legend>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.ocr?.enabled ?? true}
            onChange={(e) => updateOcr("enabled", e.target.checked)}
          />
          <span>{t("settings.system.ocr.enabled")}</span>
        </label>
        <label className="block space-y-1 text-sm">
          <span>{t("settings.system.ocr.max_inputs")}</span>
          <input
            type="number"
            min={1}
            className="w-full rounded border border-slate-300 px-2 py-1 dark:border-slate-600 dark:bg-slate-900"
            value={form.ocr?.max_inputs ?? 4}
            onChange={(e) => updateOcr("max_inputs", Number(e.target.value))}
          />
        </label>
      </fieldset>

      {error !== null && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      )}
      {savedFlash && (
        <p role="status" className="text-sm text-emerald-700 dark:text-emerald-400">
          {t("settings.saved")}
        </p>
      )}

      <button
        type="button"
        onClick={onSave}
        disabled={saving}
        className="rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700 disabled:opacity-60"
      >
        {saving ? t("settings.saving") : t("settings.system.save")}
      </button>
    </section>
  );
}
