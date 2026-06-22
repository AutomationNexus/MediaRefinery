"""TLS verification selection, container-aware bind warning, and bind config."""

from __future__ import annotations

import builtins
from io import StringIO
from pathlib import Path

from mediarefinery.doctor import _ImmichDoctorHttpClient
from mediarefinery.immich import HttpImmichClient
from mediarefinery.service import config as svc_config
from mediarefinery.settings.load import service_config_from_nested


def test_immich_client_verify_defaults_to_true() -> None:
    """Default is full TLS verification."""
    client = HttpImmichClient(base_url="https://immich.local", api_key="k")
    assert client._verify is True


def test_immich_client_verify_false_only_when_opted_in() -> None:
    """verify_tls=False disables verification (explicit opt-in)."""
    client = HttpImmichClient(base_url="https://immich.local", api_key="k", verify_tls=False)
    assert client._verify is False


def test_immich_client_ca_bundle_takes_precedence() -> None:
    """A CA bundle path pins a custom CA, overriding verify_tls=False."""
    client = HttpImmichClient(
        base_url="https://immich.local", api_key="k", verify_tls=False, ca_bundle="/ca.pem"
    )
    assert client._verify == "/ca.pem"


def test_doctor_client_verify_selection() -> None:
    """The doctor probe client resolves verify the same way."""
    base = {"base_url": "https://immich.local", "api_key": "k", "timeout_seconds": 1.0}
    assert _ImmichDoctorHttpClient(**base, verify_tls=True)._verify is True
    assert _ImmichDoctorHttpClient(**base, verify_tls=False)._verify is False
    pinned = _ImmichDoctorHttpClient(**base, verify_tls=True, ca_bundle="/ca.pem")
    assert pinned._verify == "/ca.pem"


def test_warn_if_exposed_loopback_is_silent(capsys, monkeypatch) -> None:
    """Loopback binds never warn."""
    monkeypatch.setattr(svc_config, "_in_container", lambda: False)
    for host in ("127.0.0.1", "::1", "localhost"):
        svc_config.warn_if_exposed(host)
    assert capsys.readouterr().err == ""


def test_warn_if_exposed_in_container_is_silent(capsys, monkeypatch) -> None:
    """A non-loopback bind inside a container is the normal case — silent."""
    monkeypatch.setattr(svc_config, "_in_container", lambda: True)
    svc_config.warn_if_exposed("0.0.0.0")
    assert capsys.readouterr().err == ""


def test_warn_if_exposed_outside_container_warns(capsys, monkeypatch) -> None:
    """A non-loopback bind outside a container warns loudly."""
    monkeypatch.setattr(svc_config, "_in_container", lambda: False)
    svc_config.warn_if_exposed("0.0.0.0")
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "0.0.0.0" in err


def test_in_container_detects_dockerenv(monkeypatch) -> None:
    """``/.dockerenv`` is treated as a container."""
    monkeypatch.setattr(svc_config.os.path, "exists", lambda p: p == "/.dockerenv")
    assert svc_config._in_container() is True


def test_in_container_reads_cgroup(monkeypatch) -> None:
    """A docker cgroup is treated as a container."""
    monkeypatch.setattr(svc_config.os.path, "exists", lambda p: False)
    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if path == "/proc/1/cgroup":
            return StringIO("12:devices:/docker/abc123\n")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    assert svc_config._in_container() is True


def test_in_container_false_when_no_signals(monkeypatch) -> None:
    """No container signals and no readable cgroup means not a container."""
    monkeypatch.setattr(svc_config.os.path, "exists", lambda p: False)

    def raise_oserror(*args, **kwargs):
        raise OSError("no /proc")

    monkeypatch.setattr("builtins.open", raise_oserror)
    assert svc_config._in_container() is False


def _nested(**system):
    base = {"immich_base_url": "https://immich.local", "base_url": "https://app.local"}
    base.update(system)
    return {"system": base}


def test_bind_defaults_to_all_interfaces_port_8080() -> None:
    """Bind defaults match the container service model."""
    cfg = service_config_from_nested(_nested(), data_dir=Path("/data"))
    assert cfg.bind_host == "0.0.0.0"
    assert cfg.bind_port == 8080


def test_bind_host_and_port_read_from_config() -> None:
    """Operators can override the bind host/port via config.db."""
    cfg = service_config_from_nested(
        _nested(bind_host="127.0.0.1", bind_port=9000), data_dir=Path("/data")
    )
    assert cfg.bind_host == "127.0.0.1"
    assert cfg.bind_port == 9000
