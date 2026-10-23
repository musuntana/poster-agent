"""Regression tests for the documented local-only server launcher."""
from __future__ import annotations

import argparse

import pytest

from app import run_local


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "127.0.0.2"])
def test_validate_loopback_host_accepts_loopback_addresses(host):
    assert run_local.validate_loopback_host(host) == host


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "192.168.1.10", "10.0.0.5", "localhost", "example.com", ""],
)
def test_validate_loopback_host_rejects_network_or_ambiguous_addresses(host):
    with pytest.raises(ValueError, match="loopback"):
        run_local.validate_loopback_host(host)


@pytest.mark.parametrize("value", ["0", "-1", "65536", "not-a-port"])
def test_parse_port_rejects_invalid_values(value):
    with pytest.raises(argparse.ArgumentTypeError):
        run_local.parse_port(value)


def test_main_starts_uvicorn_on_loopback_and_disables_proxy_headers(monkeypatch):
    calls = []

    def fake_run(app, **kwargs):
        calls.append((app, kwargs))

    monkeypatch.setattr(run_local.uvicorn, "run", fake_run)
    run_local.main(["--host", "::1", "--port", "8123", "--reload"])

    assert calls == [
        (
            "app.main:app",
            {
                "host": "::1",
                "port": 8123,
                "reload": True,
                "proxy_headers": False,
            },
        )
    ]


def test_main_rejects_non_loopback_before_starting_uvicorn(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(run_local.uvicorn, "run", lambda *args, **kwargs: calls.append(args))

    with pytest.raises(SystemExit) as exc:
        run_local.main(["--host", "0.0.0.0"])

    assert exc.value.code == 2
    assert calls == []
    assert "network interface is disabled" in capsys.readouterr().err
