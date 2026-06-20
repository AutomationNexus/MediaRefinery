import { useEffect, useState } from "react";
import { getPolicies, putPolicies } from "../../api/client";
import { t } from "../../lib/i18n";

export default function PoliciesEditor() {
  const [text, setText] = useState<string>("");
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const policies = await getPolicies();
        setText(JSON.stringify(policies, null, 2));
        setLoaded(true);
      } catch (err) {
        setError((err as Error).message);
      }
    })();
  }, []);

  async function save() {
    setError(null);
    setMessage(null);
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(text);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        throw new Error("not an object");
      }
    } catch {
      setError(t("settings.invalid_json"));
      return;
    }
    setBusy(true);
    try {
      await putPolicies(parsed);
      setMessage(t("settings.saved"));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <label htmlFor="policies-json" className="block text-sm font-medium">
        {t("settings.policies.label")}
      </label>
      <textarea
        id="policies-json"
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={!loaded}
        rows={10}
        className="w-full rounded border border-slate-300 bg-white p-2 font-mono text-xs dark:border-slate-600 dark:bg-slate-900"
      />
      {error !== null && (
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      )}
      {message !== null && (
        <p role="status" aria-live="polite" className="text-sm text-emerald-700 dark:text-emerald-400">
          {message}
        </p>
      )}
      <button
        type="button"
        onClick={save}
        disabled={!loaded || busy}
        className="rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700 disabled:opacity-60"
      >
        {busy ? t("settings.saving") : t("settings.save_policies")}
      </button>
    </div>
  );
}
