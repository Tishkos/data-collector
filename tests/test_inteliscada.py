"""InteliSCADA adapter against a fake server implementing the proven API flow."""
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from qs_hybrid.adapters import inteliscada as mod
from qs_hybrid.registry.models import Device, PointDefinition, Quality

SITE = "ca141f1f-00db-4fb0-9aa1-8775c232765a"


class Fake(BaseHTTPRequestHandler):
    state = {"unlocks": 0, "queries": 0, "expire_once": False}

    def log_message(self, *a):  # silence
        pass

    def _send(self, code, obj=None):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if obj is not None:
            self.wfile.write(json.dumps(obj).encode())

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/auth/login":
            return self._send(200, {"accessToken": "anon", "refreshToken": "r"})
        if self.path == f"/api/auth/sites/{SITE}/unlock":
            if self.headers.get("Authorization") != "Bearer anon":
                return self._send(401)
            if body.get("password") != "secret":
                return self._send(401)
            Fake.state["unlocks"] += 1
            return self._send(200, {"accessToken": f"site{Fake.state['unlocks']}", "refreshToken": "r"})
        if self.path == "/api/trends/query":
            tok = self.headers.get("Authorization", "")
            if not tok.startswith("Bearer site") or (Fake.state["expire_once"] and tok == "Bearer site1"):
                Fake.state["expire_once"] = False
                return self._send(401)
            Fake.state["queries"] += 1
            now = datetime.now(timezone.utc)
            out = []
            for tid in body["requestedTrends"]:
                if tid == "t-genset1-kw":
                    out.append({"id": tid, "name": "Genset 1 P", "dimension": "kW", "decimalPlaces": 1,
                                "samples": [{"timestamp": (now - timedelta(seconds=90)).isoformat(), "value": 310.0},
                                            {"timestamp": (now - timedelta(seconds=30)).isoformat(), "value": 322.5}]})
                elif tid == "t-old":
                    out.append({"id": tid, "name": "Old", "samples": [{"timestamp": (now - timedelta(hours=2)).isoformat(), "value": 1}]})
                else:
                    out.append({"id": tid, "name": "Empty", "samples": []})
            return self._send(200, out)
        self._send(404)

    def do_GET(self):
        tok = self.headers.get("Authorization", "")
        if self.path == "/api/sites":
            return self._send(200, [{"id": SITE, "name": "Coca Cola-Erbil last", "unlocked": False}]) if tok else self._send(401)
        if not tok.startswith("Bearer site"):
            return self._send(401)
        if self.path == f"/api/sites/{SITE}/devices":
            return self._send(200, [{"id": "d1", "name": "InteliNeo 5500"}, {"id": "d2", "name": "Genset 1"}])
        if self.path == f"/api/trends/{SITE}":
            return self._send(200, [{"id": "t-genset1-kw", "name": "Genset 1 P"}, {"id": "t-old", "name": "Old"}, {"id": "t-none", "name": "Nothing"}])
        self._send(404)


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), Fake)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _device(url, pw="secret"):
    return Device(
        id="neo-01", kind="genset", adapter="inteliscada",
        connection={"url": url, "password": pw, "lookback_min": 10, "stale_after_s": 300},
        points=[
            PointDefinition(id="genset1_kw", unit="kW", raw={"trend": "Genset 1 P"}),   # by name
            PointDefinition(id="old_value", raw={"trend": "t-old"}),                     # by id, stale
            PointDefinition(id="nothing", raw={"trend": "t-none"}),                      # no samples
            PointDefinition(id="unmapped", raw={}),
        ],
    )


def test_full_flow_and_quality(server):
    mod._CLIENTS.clear()
    Fake.state.update({"unlocks": 0, "queries": 0, "expire_once": False})
    ad = mod.InteliScadaAdapter(_device(server))
    ad.client.min_query_interval = 0
    assert ad.connect() is True
    assert ad.client.site_id == SITE                       # auto-detected from /api/sites
    assert [d["name"] for d in ad.client.devices()] == ["InteliNeo 5500", "Genset 1"]
    r = ad.read()
    assert r["genset1_kw"].value == 322.5 and r["genset1_kw"].quality == Quality.GOOD   # newest sample wins
    assert r["old_value"].quality == Quality.STALE
    assert r["nothing"].quality == Quality.BAD
    assert r["unmapped"].quality == Quality.BAD and "mapping" in r["unmapped"].error
    assert Fake.state["unlocks"] == 1


def test_reunlock_after_idle_logout(server):
    mod._CLIENTS.clear()
    Fake.state.update({"unlocks": 0, "queries": 0, "expire_once": True})
    ad = mod.InteliScadaAdapter(_device(server))
    ad.client.min_query_interval = 0
    r = ad.read()                                           # first query gets 401 -> re-unlock -> retry
    assert r["genset1_kw"].quality == Quality.GOOD
    assert Fake.state["unlocks"] == 2


def test_wrong_password_is_bad_quality_not_crash(server):
    mod._CLIENTS.clear()
    ad = mod.InteliScadaAdapter(_device(server, pw="nope"))
    ad.client.min_query_interval = 0
    assert ad.connect() is False
    r = ad.read()
    assert all(x.quality == Quality.BAD for x in r.values())


def test_write_is_refused(server):
    mod._CLIENTS.clear()
    ad = mod.InteliScadaAdapter(_device(server))
    from qs_hybrid.registry.models import CommandDefinition, RiskClass
    with pytest.raises(PermissionError):
        ad.write(CommandDefinition(id="start", risk_class=RiskClass.C3_TRANSITION), {})
