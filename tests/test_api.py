import os
import tempfile

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("QS_SITE_CONFIG", os.path.join(ROOT, "config", "site.example.yaml"))
    monkeypatch.setenv("QS_DATA_DIR", tmp)
    from qs_hybrid.api import app as mod
    mod._site = None
    mod._autopilot = None
    return TestClient(mod.app)


def test_read_endpoints(client):
    assert client.get("/api/v1/site").status_code == 200
    devs = client.get("/api/v1/devices").json()
    assert any(d["id"] == "genset-01" for d in devs)
    caps = client.get("/api/v1/devices/genset-01/capabilities").json()
    assert "start" in {c["id"] for c in caps["commands"]}
    assert client.get("/api/v1/devices/pv-01/points").json()["power_kw"]["quality"] == "good"


def test_command_lifecycle_over_http(client):
    op = {"X-User": "tishko", "X-Role": "operator"}
    eng = {"X-User": "yousif", "X-Role": "engineer"}
    r = client.post("/api/v1/commands", json={"device_id": "genset-01", "command_id": "start", "reason": "t"}, headers=op)
    assert r.status_code == 202
    cid = r.json()["command_id"]
    assert r.json()["status"] == "awaiting_approval"       # 202 != executed
    r = client.post(f"/api/v1/commands/{cid}/approve", headers=op)
    assert r.status_code == 409                               # can't approve your own
    r = client.post(f"/api/v1/commands/{cid}/approve", headers=eng)
    assert r.json()["status"] == "verified"


def test_viewer_blocked(client):
    r = client.post("/api/v1/commands", json={"device_id": "pv-01", "command_id": "set_power_limit", "args": {"limit_pct": 50}},
                    headers={"X-User": "x", "X-Role": "viewer"})
    assert r.json()["status"] == "rejected"
    assert client.get("/api/v1/audit-events", headers={"X-Role": "viewer"}).status_code == 403


def test_plan_endpoint(client):
    r = client.post("/api/v1/plan", json={"objective": "share_target", "pv_share_pct": 60, "bess_reserve_pct": 30})
    assert r.status_code == 200 and "steps" in r.json()


def test_only_engineer_changes_mode(client):
    r = client.post("/api/v1/site/mode", json={"mode": "autonomous"}, headers={"X-Role": "operator"})
    assert r.status_code == 403
    r = client.post("/api/v1/site/mode", json={"mode": "autonomous"}, headers={"X-Role": "engineer"})
    assert r.json()["mode"] == "autonomous"
