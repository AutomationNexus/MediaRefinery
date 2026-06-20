import { useEffect, useState } from "react";
import { getCategories, putCategories } from "../../api/client";
import { t } from "../../lib/i18n";

export default function CategoriesEditor() {
  const [text, setText] = useState<string>("");
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newId, setNewId] = useState("");
  const [newMatch, setNewMatch] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const r = await getCategories();
        setText(JSON.stringify(r.categories, null, 2));
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
      await putCategories(parsed);
      setMessage(t("settings.saved"));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function addCustomCategory() {
    setError(null);
    const id = newId.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "_");
    const terms = newMatch
      .split(",")
      .map((term) => term.trim())
      .filter(Boolean);
    if (!id) {
      setError(t("settings.categories.id_required"));
      return;
    }
    let parsed: Record<string, unknown>;
    try {
      parsed = text.trim() ? JSON.parse(text) : {};
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        throw new Error("not an object");
      }
    } catch {
      setError(t("settings.invalid_json"));
      return;
    }
    parsed[id] = {
      enabled: true,
      threshold: 0.5,
      rules: terms.length > 0 ? [{ match_any: terms }] : [],
    };
    setText(JSON.stringify(parsed, null, 2));
    setNewId("");
    setNewMatch("");
  }

  return (
    <div className="space-y-2">
      <div className="grid gap-2 rounded border border-slate-200 p-3 text-sm dark:border-slate-700 sm:grid-cols-[1fr_2fr_auto]">
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">
            {t("settings.categories.new_id")}
          </span>
          <input
            value={newId}
            onChange={(e) => setNewId(e.target.value)}
            className="w-full rounded border border-slate-300 bg-white px-2 py-1 dark:border-slate-600 dark:bg-slate-700"
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">
            {t("settings.categories.match_terms")}
          </span>
          <input
            value={newMatch}
            onChange={(e) => setNewMatch(e.target.value)}
            className="w-full rounded border border-slate-300 bg-white px-2 py-1 dark:border-slate-600 dark:bg-slate-700"
            placeholder={t("settings.categories.match_placeholder")}
          />
        </label>
        <button
          type="button"
          onClick={addCustomCategory}
          disabled={!loaded}
          className="self-end rounded border border-slate-300 px-3 py-1 hover:bg-slate-50 disabled:opacity-60 dark:border-slate-600 dark:hover:bg-slate-700"
        >
          {t("settings.categories.add")}
        </button>
      </div>
      <label htmlFor="categories-json" className="block text-sm font-medium">
        {t("settings.categories.label")}
      </label>
      <textarea
        id="categories-json"
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
        {busy ? t("settings.saving") : t("settings.save_categories")}
      </button>
    </div>
  );
}
