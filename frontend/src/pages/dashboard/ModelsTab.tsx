import { Dialog } from "@headlessui/react";
import { useEffect, useState } from "react";
import {
  CatalogModel,
  InstalledModel,
  getCatalog,
  getInstalledModels,
  installModel,
  registerAdultSubtypeProfile,
  uninstallModel,
} from "../../api/client";
import { t } from "../../lib/i18n";

export default function ModelsTab() {
  const [installed, setInstalled] = useState<InstalledModel[] | null>(null);
  const [catalog, setCatalog] = useState<CatalogModel[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | number | null>(null);

  const [installTarget, setInstallTarget] = useState<CatalogModel | null>(null);
  const [licenseAccepted, setLicenseAccepted] = useState(false);
  const [subtypeModelId, setSubtypeModelId] = useState("adult-subtype-local");
  const [subtypeName, setSubtypeName] = useState("");
  const [subtypePath, setSubtypePath] = useState("");
  const [subtypeLabels, setSubtypeLabels] = useState("");
  const [subtypeThresholds, setSubtypeThresholds] = useState("{}");
  const [subtypeAcknowledged, setSubtypeAcknowledged] = useState(false);
  const [subtypeMessage, setSubtypeMessage] = useState<string | null>(null);

  const [uninstallTarget, setUninstallTarget] = useState<InstalledModel | null>(
    null,
  );

  async function refresh() {
    setError(null);
    try {
      const [inst, cat] = await Promise.all([
        getInstalledModels(),
        getCatalog(),
      ]);
      setInstalled(inst);
      setCatalog(cat);
    } catch {
      setError(t("models.error.load"));
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  function openInstall(model: CatalogModel) {
    setLicenseAccepted(false);
    setInstallTarget(model);
  }
  function closeInstall() {
    if (busyId !== null) return;
    setInstallTarget(null);
    setLicenseAccepted(false);
  }

  async function confirmInstall() {
    if (installTarget === null) return;
    setBusyId(installTarget.id);
    setError(null);
    try {
      await installModel(installTarget.id);
      setInstallTarget(null);
      setLicenseAccepted(false);
      await refresh();
    } catch {
      setError(t("models.error.install"));
    } finally {
      setBusyId(null);
    }
  }

  async function confirmUninstall() {
    if (uninstallTarget === null) return;
    setBusyId(uninstallTarget.id);
    setError(null);
    try {
      await uninstallModel(uninstallTarget.id);
      setUninstallTarget(null);
      await refresh();
    } catch {
      setError(t("models.error.uninstall"));
    } finally {
      setBusyId(null);
    }
  }

  async function registerSubtypeProfile() {
    const labels = subtypeLabels
      .split(",")
      .map((label) => label.trim())
      .filter(Boolean);
    let thresholds: Record<string, number> = {};
    try {
      const parsed = JSON.parse(subtypeThresholds || "{}") as unknown;
      if (
        parsed === null ||
        typeof parsed !== "object" ||
        Array.isArray(parsed)
      ) {
        throw new Error("thresholds");
      }
      thresholds = Object.fromEntries(
        Object.entries(parsed).map(([key, value]) => [key, Number(value)]),
      );
    } catch {
      setError(t("models.subtype.error.thresholds"));
      return;
    }
    setBusyId("adult-subtype");
    setError(null);
    setSubtypeMessage(null);
    try {
      await registerAdultSubtypeProfile({
        model_id: subtypeModelId,
        name: subtypeName || null,
        model_path: subtypePath,
        output_labels: labels,
        thresholds,
        admin_acknowledgement: subtypeAcknowledged,
      });
      setSubtypeMessage(t("models.subtype.saved"));
      await refresh();
    } catch {
      setError(t("models.subtype.error.save"));
    } finally {
      setBusyId(null);
    }
  }

  function formatSize(bytes: number): string {
    if (bytes >= 1024 * 1024 * 1024) {
      return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
    }
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  return (
    <section aria-labelledby="models-title" className="space-y-6">
      <div>
        <h2 id="models-title" className="text-lg font-semibold">
          {t("models.title")}
        </h2>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">
          {t("models.body")}
        </p>
      </div>

      {error !== null && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      )}

      <section aria-labelledby="installed-title" className="space-y-2">
        <h3 id="installed-title" className="text-base font-semibold">
          {t("models.installed.title")}
        </h3>
        {installed === null ? (
          <p role="status" aria-live="polite">…</p>
        ) : installed.length === 0 ? (
          <p className="text-sm">{t("models.installed.empty")}</p>
        ) : (
          <ul className="divide-y divide-slate-200 dark:divide-slate-700">
            {installed.map((m) => (
              <li
                key={m.id}
                className="flex items-center justify-between py-2 text-sm"
              >
                <div className="space-y-0.5">
                  <div className="font-medium">
                    {m.name}{" "}
                    <span className="text-xs text-slate-500">{m.version}</span>
                    {m.active && (
                      <span className="ml-2 rounded bg-emerald-100 px-1.5 py-0.5 text-xs font-semibold text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200">
                        {t("models.installed.active")}
                      </span>
                    )}
                    {!m.present_on_disk && (
                      <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-amber-800 dark:bg-amber-900/40 dark:text-amber-200">
                        {t("models.installed.missing")}
                      </span>
                    )}
                  </div>
                  <div className="font-mono text-xs text-slate-500">
                    {m.sha256.slice(0, 16)}…
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setUninstallTarget(m)}
                  disabled={busyId === m.id}
                  className="rounded border border-red-400 px-2 py-1 text-xs text-red-700 hover:bg-red-50 disabled:opacity-60 dark:border-red-500 dark:text-red-300 dark:hover:bg-red-900/30"
                >
                  {busyId === m.id
                    ? t("models.installed.uninstalling")
                    : t("models.installed.uninstall")}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="catalog-title" className="space-y-2">
        <h3 id="catalog-title" className="text-base font-semibold">
          {t("models.catalog.title")}
        </h3>
        {catalog === null ? (
          <p role="status" aria-live="polite">…</p>
        ) : catalog.length === 0 ? (
          <p className="text-sm">{t("models.catalog.empty")}</p>
        ) : (
          <ul className="divide-y divide-slate-200 dark:divide-slate-700">
            {catalog.map((m) => (
              <li key={m.id} className="space-y-1 py-2 text-sm">
                <div className="flex items-center justify-between">
                  <div className="font-medium">
                    {m.name}{" "}
                    <span className="text-xs text-slate-500">({m.kind})</span>
                  </div>
                  {m.installed ? (
                    <span className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-700 dark:bg-slate-700 dark:text-slate-200">
                      {t("models.catalog.installed_marker")}
                    </span>
                  ) : m.installable ? (
                    <button
                      type="button"
                      onClick={() => openInstall(m)}
                      disabled={busyId === m.id}
                      className="rounded bg-blue-600 px-2 py-1 text-xs text-white hover:bg-blue-700 disabled:opacity-60"
                    >
                      {busyId === m.id
                        ? t("models.catalog.installing")
                        : t("models.catalog.install")}
                    </button>
                  ) : (
                    <span className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-700 dark:bg-slate-700 dark:text-slate-200">
                      {m.status}
                    </span>
                  )}
                </div>
                <div className="text-xs text-slate-500">
                  {t("models.catalog.size")}: {formatSize(m.size_bytes)} ·{" "}
                  {t("models.catalog.license")}: {m.license}
                  {m.license_url !== null && (
                    <>
                      {" · "}
                      <a
                        href={m.license_url}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="text-blue-700 hover:underline dark:text-blue-300"
                      >
                        {t("models.catalog.license_url")}
                      </a>
                    </>
                  )}
                </div>
                <div className="font-mono text-xs text-slate-500">
                  {t("models.catalog.sha256")}: {m.sha256.slice(0, 24)}…
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="subtype-title" className="space-y-3">
        <div>
          <h3 id="subtype-title" className="text-base font-semibold">
            {t("models.subtype.title")}
          </h3>
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">
            {t("models.subtype.body")}
          </p>
        </div>
        {subtypeMessage !== null && (
          <p role="status" className="text-sm text-emerald-700 dark:text-emerald-400">
            {subtypeMessage}
          </p>
        )}
        <div className="grid gap-3 text-sm sm:grid-cols-2">
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">
              {t("models.subtype.model_id")}
            </span>
            <input
              value={subtypeModelId}
              onChange={(e) => setSubtypeModelId(e.target.value)}
              className="w-full rounded border border-slate-300 bg-white px-2 py-1 dark:border-slate-600 dark:bg-slate-700"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">
              {t("models.subtype.name")}
            </span>
            <input
              value={subtypeName}
              onChange={(e) => setSubtypeName(e.target.value)}
              className="w-full rounded border border-slate-300 bg-white px-2 py-1 dark:border-slate-600 dark:bg-slate-700"
            />
          </label>
          <label className="block sm:col-span-2">
            <span className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">
              {t("models.subtype.path")}
            </span>
            <input
              value={subtypePath}
              onChange={(e) => setSubtypePath(e.target.value)}
              className="w-full rounded border border-slate-300 bg-white px-2 py-1 font-mono text-xs dark:border-slate-600 dark:bg-slate-700"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">
              {t("models.subtype.labels")}
            </span>
            <input
              value={subtypeLabels}
              onChange={(e) => setSubtypeLabels(e.target.value)}
              className="w-full rounded border border-slate-300 bg-white px-2 py-1 dark:border-slate-600 dark:bg-slate-700"
              placeholder="label_one, label_two"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">
              {t("models.subtype.thresholds")}
            </span>
            <input
              value={subtypeThresholds}
              onChange={(e) => setSubtypeThresholds(e.target.value)}
              className="w-full rounded border border-slate-300 bg-white px-2 py-1 font-mono text-xs dark:border-slate-600 dark:bg-slate-700"
            />
          </label>
        </div>
        <label className="flex items-start space-x-2 text-sm">
          <input
            type="checkbox"
            checked={subtypeAcknowledged}
            onChange={(e) => setSubtypeAcknowledged(e.target.checked)}
            disabled={busyId !== null}
            className="mt-1"
          />
          <span>{t("models.subtype.acknowledge")}</span>
        </label>
        <button
          type="button"
          onClick={registerSubtypeProfile}
          disabled={
            busyId !== null ||
            !subtypeAcknowledged ||
            !subtypePath.trim() ||
            !subtypeLabels.trim()
          }
          className="rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700 disabled:opacity-60"
        >
          {busyId === "adult-subtype"
            ? t("models.subtype.saving")
            : t("models.subtype.save")}
        </button>
      </section>

      <Dialog
        open={installTarget !== null}
        onClose={closeInstall}
        className="relative z-50"
      >
        <div className="fixed inset-0 bg-black/40" aria-hidden="true" />
        <div className="fixed inset-0 flex items-center justify-center p-4">
          <Dialog.Panel className="w-full max-w-md rounded-lg bg-white p-6 shadow-lg dark:bg-slate-800">
            <Dialog.Title className="text-base font-semibold">
              {installTarget?.name}
            </Dialog.Title>
            <Dialog.Description className="mt-2 text-sm text-slate-600 dark:text-slate-300">
              {t("models.catalog.license")}: {installTarget?.license}
              {installTarget?.license_url && (
                <>
                  {" · "}
                  <a
                    href={installTarget.license_url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="text-blue-700 hover:underline dark:text-blue-300"
                  >
                    {t("models.catalog.license_url")}
                  </a>
                </>
              )}
            </Dialog.Description>
            <label className="mt-4 flex items-start space-x-2 text-sm">
              <input
                type="checkbox"
                checked={licenseAccepted}
                onChange={(e) => setLicenseAccepted(e.target.checked)}
                disabled={busyId !== null}
                className="mt-1"
              />
              <span>{t("models.catalog.accept_license")}</span>
            </label>
            <div className="mt-4 flex justify-end space-x-2">
              <button
                type="button"
                onClick={closeInstall}
                disabled={busyId !== null}
                className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
              >
                {t("models.uninstall.cancel")}
              </button>
              <button
                type="button"
                onClick={confirmInstall}
                disabled={busyId !== null || !licenseAccepted}
                className="rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700 disabled:opacity-60"
              >
                {busyId !== null
                  ? t("models.catalog.installing")
                  : t("models.catalog.install")}
              </button>
            </div>
          </Dialog.Panel>
        </div>
      </Dialog>

      <Dialog
        open={uninstallTarget !== null}
        onClose={() => (busyId !== null ? null : setUninstallTarget(null))}
        className="relative z-50"
      >
        <div className="fixed inset-0 bg-black/40" aria-hidden="true" />
        <div className="fixed inset-0 flex items-center justify-center p-4">
          <Dialog.Panel className="w-full max-w-md rounded-lg bg-white p-6 shadow-lg dark:bg-slate-800">
            <Dialog.Title className="text-base font-semibold">
              {uninstallTarget !== null
                ? t("models.uninstall.confirm.title", {
                    name: uninstallTarget.name,
                  })
                : ""}
            </Dialog.Title>
            <Dialog.Description className="mt-2 text-sm text-slate-600 dark:text-slate-300">
              {t("models.uninstall.confirm.body")}
            </Dialog.Description>
            <div className="mt-4 flex justify-end space-x-2">
              <button
                type="button"
                onClick={() => setUninstallTarget(null)}
                disabled={busyId !== null}
                className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
              >
                {t("models.uninstall.cancel")}
              </button>
              <button
                type="button"
                onClick={confirmUninstall}
                disabled={busyId !== null}
                className="rounded bg-red-600 px-3 py-1 text-sm text-white hover:bg-red-700 disabled:opacity-60"
              >
                {busyId !== null
                  ? t("models.installed.uninstalling")
                  : t("models.uninstall.confirm")}
              </button>
            </div>
          </Dialog.Panel>
        </div>
      </Dialog>
    </section>
  );
}
