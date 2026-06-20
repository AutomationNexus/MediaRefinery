import { useEffect, useMemo, useState } from "react";
import { AuditEntry, listAudit } from "../../api/client";
import { t } from "../../lib/i18n";

const PAGE_SIZE = 25;

export default function AuditTable() {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);

  async function refresh() {
    setError(null);
    try {
      const list = await listAudit();
      setEntries(list);
      setPage(0);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  const totalPages = useMemo(
    () => Math.max(1, Math.ceil((entries?.length ?? 0) / PAGE_SIZE)),
    [entries],
  );
  const visible = useMemo(() => {
    if (entries === null) return [];
    const start = page * PAGE_SIZE;
    return entries.slice(start, start + PAGE_SIZE);
  }, [entries, page]);

  return (
    <section aria-labelledby="audit-title" className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 id="audit-title" className="text-lg font-semibold">
          {t("audit.title")}
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

      {entries === null ? (
        <p role="status" aria-live="polite">…</p>
      ) : entries.length === 0 ? (
        <p>{t("audit.empty")}</p>
      ) : (
        <>
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-300 dark:border-slate-600">
              <tr>
                <th scope="col" className="py-2">{t("audit.col.at")}</th>
                <th scope="col">{t("audit.col.action")}</th>
                <th scope="col">{t("audit.col.run")}</th>
                <th scope="col">{t("audit.col.asset")}</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((entry) => (
                <tr
                  key={entry.id}
                  className="border-b border-slate-100 dark:border-slate-800"
                >
                  <td className="py-2 font-mono text-xs">{entry.at}</td>
                  <td>{entry.action}</td>
                  <td>{entry.run_id ?? ""}</td>
                  <td className="font-mono text-xs">{entry.target_asset_id ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="flex items-center justify-between text-sm">
            <span>{t("audit.page", { n: page + 1, total: totalPages })}</span>
            <div className="space-x-2">
              <button
                type="button"
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50 disabled:opacity-50 dark:border-slate-600 dark:hover:bg-slate-700"
              >
                {t("audit.prev")}
              </button>
              <button
                type="button"
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50 disabled:opacity-50 dark:border-slate-600 dark:hover:bg-slate-700"
              >
                {t("audit.next")}
              </button>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
