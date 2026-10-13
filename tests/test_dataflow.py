"""Site collector -> VPS ingest -> tags API, end to end (no network: TestClient)."""
import os
import tempfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from qs_hybrid.collector.push import Spool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def client(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("QS_SITE_CONFIG", os.path.join(ROOT, "config", "site.example.yaml"))
    monkeypatch.setenv("QS_DATA_DIR", tmp)
    monkeypatch.setenv("QS_COLLECTOR_KEY", "ck")
    monkeypatch.setenv("QS_READ_KEY", "rk")
    from qs_hybrid.api import app as mod, tags as tmod
    mod._site = None
    mod._autopilot = None
    tmod.COLLECTOR_KEY, tmod.READ_KEY = "ck", "rk"
    return TestClient(mod.app)


def _batch(ts=None):
    ts = ts or datetime.now(timezone.utc).isoformat()
    return {"ts": ts, "devices": {
        "inv-01": {"ac_power_kw": {"v": 71.4, "q": "good", "ts": ts}, "comm_error": {"v": False, "q": "good", "ts": ts},
                   "string_current_3_a": {"v": 8.9, "q": "good", "ts": ts}},
        "inv-07": {"ac_power_kw": {"v": None, "q": "bad", "ts": ts, "e": "timeout"}},
        "pv-meter": {"power_kw": {"v": 690.0, "q": "good", "ts": ts}},
        "neo-01": {"pvcb_closed": {"v": True, "q": "good", "ts": ts}},
    }}


def test_ingest_requires_collector_key(client):
    assert client.post("/api/v1/ingest", json={"batches": [_batch()]}).status_code == 401
    assert client.post("/api/v1/ingest", json={"batches": [_batch()]}, headers={"X-API-Key": "rk"}).status_code == 401
    r = client.post("/api/v1/ingest", json={"batches": [_batch()]}, headers={"X-API-Key": "ck"})
    assert r.status_code == 200 and r.json()["points"] == 6


def test_tags_in_turkey_naming(client):
    client.post("/api/v1/ingest", json={"batches": [_batch()]}, headers={"X-API-Key": "ck"})
    assert client.get("/api/v1/tags").status_code == 401
    r = client.get("/api/v1/tags", headers={"X-API-Key": "rk"})
    assert r.status_code == 200
    by = {t["name"]: t for t in r.json()}
    assert len(by) == 305
    assert by["INVERTER_1.INV1_AC_POWER"]["value"] == 71.4 and by["INVERTER_1.INV1_AC_POWER"]["quality"] == "good"
    assert by["INVERTER_1.INV1_String_CURRENT_I3"]["value"] == 8.9
    assert by["INVERTER_7.INV7_AC_POWER"]["quality"] == "bad"          # never a fake number
    assert by["Psum3"]["value"] == 690.0 and by["Psum3"]["unit"] == "kW"
    assert by["Thermal_Magnetic_Switch_On_(LV PANEL)"]["value"] is True
    assert by["INVERTER_10.INV10_TOTAL_PRODUCTION"]["value"] is None  # no data yet -> explicit


def test_history_and_csv(client):
    from datetime import timedelta
    t0 = (datetime.now(timezone.utc) - timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    for m in range(0, 60, 10):
        client.post("/api/v1/ingest", json={"batches": [_batch(t0.replace(minute=m).isoformat())]}, headers={"X-API-Key": "ck"})
    r = client.get("/api/v1/tags/Psum3/history?minutes=100000", headers={"X-API-Key": "rk"})
    assert r.status_code == 200 and len(r.json()["rows"]) == 6
    r = client.get("/api/v1/export.csv", headers={"X-API-Key": "rk"})
    assert r.status_code == 200 and r.text.startswith("tag,value,unit,quality") and "INVERTER_10" in r.text


def test_spool_store_and_forward(tmp_path):
    sp = Spool(str(tmp_path / "spool.sqlite"))
    for i in range(3):
        sp.put(_batch(datetime(2026, 10, 13, 8, i, tzinfo=timezone.utc).isoformat()))
    assert sp.size() == 3
    items = sp.oldest(2)
    assert [b["ts"][14:16] for _, b in items] == ["00", "01"]      # oldest first
    sp.ack([i for i, _ in items])
    assert sp.size() == 1
