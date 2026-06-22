import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
import requests

from mediarefinery.settings.defaults import default_nested_config
from mediarefinery.settings.load import ensure_config_db_seeded


def _repo_root() -> str:
    """``tests/e2e_frontend/`` → repo root is two levels up."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Run Playwright's bundled Chromium with container-safe flags in CI."""
    if not os.environ.get("CI"):
        return browser_type_launch_args
    return {
        **browser_type_launch_args,
        "args": [
            *browser_type_launch_args.get("args", []),
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    }


@pytest.fixture(scope="session")
def mediarefinery_server():
    """
    Start MediaRefinery in a subprocess with an isolated runtime data directory.

    Demo mode is enabled via config.db (not operator env vars).
    Readiness probes ``/api/openapi.json``.
    """
    runtime_dir = tempfile.mkdtemp(prefix="mediarefinery-e2e-")
    nested = default_nested_config()
    nested["system"]["demo_mode"] = True
    nested["system"]["immich_base_url"] = "http://demo.invalid"
    nested["system"]["auto_scan_enabled"] = False
    ensure_config_db_seeded(Path(runtime_dir)).bulk_upsert(nested)

    repo_root = _repo_root()
    env = os.environ.copy()
    src_dir = os.path.join(repo_root, "src")
    path_sep = os.pathsep
    prior = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src_dir + (path_sep + prior if prior else "")
    env["MEDIAREFINERY_DATA_DIR"] = runtime_dir
    coverage_data_file = os.path.join(repo_root, ".coverage.e2e-server")
    cmd = [
        sys.executable, "-m", "uvicorn",
        "mediarefinery.service.app:create_app", "--factory",
        "--host", "127.0.0.1",
        "--port", "2470",
    ]
    try:
        import coverage as _cov  # noqa: F401

        cmd = [
            sys.executable, "-m", "coverage", "run",
            f"--data-file={coverage_data_file}",
            "-m", "uvicorn",
            "mediarefinery.service.app:create_app", "--factory",
            "--host", "127.0.0.1",
            "--port", "2470",
        ]
    except ImportError:
        pass
    proc = subprocess.Popen(
        cmd,
        cwd=repo_root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    base_url = "http://127.0.0.1:2470"
    deadline = time.monotonic() + 90.0
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stderr_out = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
            shutil.rmtree(runtime_dir, ignore_errors=True)
            pytest.fail(
                f"MediaRefinery exited early with code {proc.returncode}"
                f"\nstderr: {stderr_out[:500]}"
            )
        try:
            response = requests.get(f"{base_url}/api/openapi.json", timeout=2)
            if response.status_code == 200:
                break
        except (requests.RequestException, OSError) as exc:
            last_error = exc
        time.sleep(0.5)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(runtime_dir, ignore_errors=True)
        detail = f"; last error: {last_error}" if last_error else ""
        pytest.fail(f"MediaRefinery server did not become ready within 90s{detail}")

    yield {"base_url": base_url, "process": proc, "runtime_dir": runtime_dir}

    proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    shutil.rmtree(runtime_dir, ignore_errors=True)
