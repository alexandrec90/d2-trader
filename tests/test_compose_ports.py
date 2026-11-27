"""The dashboard's published port is the gateway's backend, never the public one.

devkit's dashboard gateway binds 127.0.0.1:8010 (APP_HOST_PORT) and forwards to the
port `app` publishes, waking the container on the first request. If `app` published
8010 again, the gateway could not bind it, and the dashboard would never sleep.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_app_publishes_on_the_backend_port_on_loopback():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    published = re.findall(r'-\s*"([^"]*):8000"', compose)
    assert published == ["127.0.0.1:${APP_BACKEND_PORT:-18010}"]


def test_the_example_env_keeps_the_two_ports_apart():
    env = dict(
        line.split("=", 1)
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    assert env["APP_HOST_PORT"] == "8010"
    assert env["APP_BACKEND_PORT"] == "18010"
