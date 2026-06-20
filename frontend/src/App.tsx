import { useCallback, useEffect, useState } from "react";
import {
  BootstrapStatus,
  InstalledModel,
  MeResponse,
  getBootstrap,
  getApiKeyStatus,
  getInstalledModels,
  getMe,
} from "./api/client";
import LoginPage from "./pages/LoginPage";
import Wizard from "./pages/Wizard";
import DashboardPage from "./pages/dashboard/DashboardPage";

// Routing shell — purely a function of three server-derived facts:
//   - bootstrap.ready: terms recorded AND an admin user exists
//   - me:              authed user, or null
//   - models:          at least one model registry row, with present_on_disk
//
// Order:
//   bootstrap not ready  → Wizard(phase="setup")
//   not authed           → LoginPage
//   authed, no model     → Wizard(phase="install")
//   authed, model exists → DashboardPlaceholder
//
// We never persist this state — every reload re-fetches.

type Snapshot = {
  bootstrap: BootstrapStatus;
  me: MeResponse | null;
  models: InstalledModel[];
  apiKeysReady: boolean;
};

export default function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const bootstrap = await getBootstrap();
      let me: MeResponse | null = null;
      let models: InstalledModel[] = [];
      let apiKeysReady = false;
      if (bootstrap.ready) {
        me = await getMe();
        if (me !== null) {
          models = await getInstalledModels();
          const apiKeyStatus = await getApiKeyStatus();
          apiKeysReady =
            !apiKeyStatus.required_for_scans || apiKeyStatus.api_keys.length > 0;
        }
      }
      setSnapshot({ bootstrap, me, models, apiKeysReady });
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  if (error !== null && snapshot === null) {
    return (
      <main className="flex min-h-screen items-center justify-center px-4">
        <p role="alert" className="text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      </main>
    );
  }

  if (snapshot === null) {
    return (
      <main className="flex min-h-screen items-center justify-center px-4">
        <p role="status" aria-live="polite" className="text-sm">
          …
        </p>
      </main>
    );
  }

  if (!snapshot.bootstrap.ready) {
    return <Wizard phase="setup" onSetupRecorded={refresh} />;
  }
  if (snapshot.me === null) {
    return <LoginPage />;
  }
  const modelReady = snapshot.models.some((m) => m.active && m.present_on_disk);
  if (!modelReady || !snapshot.apiKeysReady) {
    return (
      <Wizard
        phase="install"
        hasModel={modelReady}
        hasApiKey={snapshot.apiKeysReady}
        onInstallDone={refresh}
      />
    );
  }
  return <DashboardPage me={snapshot.me} onLoggedOut={refresh} />;
}
