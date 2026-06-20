import { useEffect, useState } from "react";
import {
  MeResponse,
  getCategories,
  logout,
  startScan,
} from "../../api/client";
import { t } from "../../lib/i18n";
import AuditTable from "./AuditTable";
import CategoriesEditor from "./CategoriesEditor";
import PoliciesEditor from "./PoliciesEditor";
import RunDetail from "./RunDetail";
import RunsList from "./RunsList";
import AssetsList from "./assets/AssetsList";
import EventsTab from "./EventsTab";
import ModelsTab from "./ModelsTab";
import AutoScanPanel from "./settings/AutoScanPanel";
import DangerZone from "./settings/DangerZone";
import SystemSettingsPanel from "./settings/SystemSettingsPanel";
import UnlockPanel from "./settings/UnlockPanel";

type Tab = "runs" | "audit" | "assets" | "events" | "models" | "settings";

interface Props {
  me: MeResponse;
  onLoggedOut: () => void;
}

export default function DashboardPage({ me, onLoggedOut }: Props) {
  const [tab, setTab] = useState<Tab>("runs");
  const [openRunId, setOpenRunId] = useState<number | null>(null);
  const [needsReclassify, setNeedsReclassify] = useState(false);
  const [rescanBusy, setRescanBusy] = useState(false);
  const [rescanMessage, setRescanMessage] = useState<string | null>(null);
  const [rescanError, setRescanError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const r = await getCategories();
        setNeedsReclassify(r.needs_reclassify);
      } catch {
        // surfaced lazily by the settings panel; don't gate the dashboard
      }
    })();
  }, []);

  async function triggerRescan() {
    setRescanError(null);
    setRescanMessage(null);
    setRescanBusy(true);
    try {
      const resp = await startScan();
      setRescanMessage(t("dashboard.reclassify.started", { run_id: resp.run_id }));
      setNeedsReclassify(false);
    } catch (err) {
      setRescanError((err as Error).message);
    } finally {
      setRescanBusy(false);
    }
  }

  async function handleLogout() {
    try {
      await logout();
    } catch {
      // even on error we tear down — server-side row is gone or stale
    }
    onLoggedOut();
  }

  return (
    <main className="min-h-screen bg-slate-50 dark:bg-slate-900">
      <header className="border-b border-slate-200 bg-white px-4 py-3 dark:border-slate-700 dark:bg-slate-800">
        <div className="mx-auto flex max-w-5xl items-center justify-between">
          <h1 className="text-lg font-semibold">{t("dashboard.title")}</h1>
          <div className="flex items-center space-x-3 text-sm">
            <span>
              {t("dashboard.signed_in_as", {
                who: me.name ?? me.email,
              })}
            </span>
            <button
              type="button"
              onClick={handleLogout}
              className="rounded border border-slate-300 px-3 py-1 hover:bg-slate-50 dark:border-slate-600 dark:hover:bg-slate-700"
            >
              {t("dashboard.logout")}
            </button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-5xl px-4 py-6 space-y-6">
        {needsReclassify && (
          <section
            role="status"
            aria-live="polite"
            className="rounded border border-amber-300 bg-amber-50 p-4 text-sm dark:border-amber-700 dark:bg-amber-900/20"
          >
            <h2 className="font-semibold">{t("dashboard.reclassify.title")}</h2>
            <p className="mt-1">{t("dashboard.reclassify.body")}</p>
            {rescanError !== null && (
              <p role="alert" className="mt-2 text-red-700 dark:text-red-400">
                {rescanError}
              </p>
            )}
            <button
              type="button"
              onClick={triggerRescan}
              disabled={rescanBusy}
              className="mt-3 rounded bg-amber-600 px-3 py-1 text-white hover:bg-amber-700 disabled:opacity-60"
            >
              {rescanBusy
                ? t("dashboard.reclassify.starting")
                : t("dashboard.reclassify.action")}
            </button>
          </section>
        )}
        {rescanMessage !== null && !needsReclassify && (
          <p role="status" aria-live="polite" className="text-sm text-emerald-700 dark:text-emerald-400">
            {rescanMessage}
          </p>
        )}

        <nav role="tablist" aria-label={t("dashboard.title")} className="flex space-x-2 border-b border-slate-200 dark:border-slate-700">
          {(
            (
              ["runs", "audit", "assets", "events", "models", "settings"] as Tab[]
            ).filter((id) => (id === "models" ? me.is_admin : true))
          ).map((id) => {
            const labelKey = `dashboard.tab.${id}` as const;
            const selected = tab === id;
            return (
              <button
                key={id}
                role="tab"
                aria-selected={selected}
                aria-controls={`panel-${id}`}
                id={`tab-${id}`}
                tabIndex={selected ? 0 : -1}
                onClick={() => {
                  setTab(id);
                  setOpenRunId(null);
                }}
                className={
                  "px-3 py-2 text-sm " +
                  (selected
                    ? "border-b-2 border-blue-600 font-semibold"
                    : "text-slate-600 dark:text-slate-300")
                }
              >
                {t(labelKey)}
              </button>
            );
          })}
        </nav>

        <div
          id={`panel-${tab}`}
          role="tabpanel"
          aria-labelledby={`tab-${tab}`}
          className="rounded border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-800"
        >
          {tab === "runs" && openRunId === null && (
            <RunsList onOpenRun={setOpenRunId} />
          )}
          {tab === "runs" && openRunId !== null && (
            <RunDetail runId={openRunId} onBack={() => setOpenRunId(null)} />
          )}
          {tab === "audit" && <AuditTable />}
          {tab === "assets" && <AssetsList />}
          {tab === "events" && <EventsTab />}
          {tab === "models" && me.is_admin && <ModelsTab />}
          {tab === "settings" && (
            <div className="space-y-6">
              <h2 id="settings-title" className="text-lg font-semibold">
                {t("settings.title")}
              </h2>
              {me.is_admin && <SystemSettingsPanel />}
              <CategoriesEditor />
              <PoliciesEditor />
              <AutoScanPanel />
              <UnlockPanel />
              <DangerZone me={me} onLoggedOut={onLoggedOut} />
            </div>
          )}
        </div>
      </div>
    </main>
  );
}
