"""End-to-end checks on the demo plant: store, views, exports, API. No network."""

import io
import os
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from plantcollector.api import create_app
from plantcollector.core import Collector
from plantcollector import export, views

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config", "site.yaml")


@pytest.fixture(scope="module")
def collector(tmp_path_factory):
    return Collector(CFG, str(tmp_path_factory.mktemp("data")), seed_hours=30, fresh=True)


@pytest.fixture(scope="module")
def client(collector):
    return TestClient(create_app(collector))


def test_registry_is_read_only(collector):
    for d in collector.registry.devices.values():
        assert not hasattr(d, "commands")
    import plantcollector.adapters.modbus_tcp as m
    src = open(m.__file__).read()
    assert "write_register" not in src and "write_coil" not in src


def test_seeded_history_and_totals(collector):
    assert collector.store.count() > 1000
    t = collector.daily_totals()["totals"]
    assert t["load_kwh"] > 0 and t["pv_kwh"] > 0
    assert abs(t["load_kwh"] - (t["pv_kwh"] + t["genset_kwh"] + t["grid_kwh"])) / t["load_kwh"] < 0.08


def test_summary_and_series(collector):
    s = views.summary(collector)
    assert s["mode"] == "read-only" and s["simulated"] is True
    assert s["kpi"]["gensets_total"] == 3
    assert all(d["comms"] == "ok" for d in s["devices"])
    ser = views.series(collector, collector.now() - timedelta(hours=6), collector.now())
    assert ser["step"] >= 30 and len(ser["t"]) == len(ser["series"]["load_kw"])
    assert any(v is not None for v in ser["series"]["mains_kw"])
    assert "genset-01/power_kw" in ser["series"]


def test_events_logged(collector):
    codes = {e["code"] for e in collector.store.events(collector.now() - timedelta(hours=30))}
    assert {"COMMS_LOST", "COMMS_OK", "ALARM", "ALARM_OK", "GRID_OFF", "GRID_OK"} <= codes


def test_wide_export_csv_and_xlsx(collector):
    since, until = collector.now() - timedelta(hours=3), collector.now()
    headers, rows = export.wide_table(collector, since, until, 300)
    assert headers[0] == "time_utc" and "meter-01.load_kw [kW]" in headers
    assert 30 <= len(rows) <= 38
    csv = export.to_csv(headers, rows).decode("utf-8-sig").splitlines()
    assert csv[0].startswith("time_utc,") and len(csv) == len(rows) + 1
    wb = load_workbook(io.BytesIO(export.to_xlsx(collector, headers, rows, since, until, 300)))
    assert wb.sheetnames == ["Data", "Summary", "Points"]
    assert wb["Data"].max_row == len(rows) + 1


def test_daily_report(collector):
    wb = load_workbook(io.BytesIO(export.daily_report_xlsx(collector)))
    assert wb.sheetnames == ["Summary", "Gensets", "Hourly", "Events"]
    assert wb["Hourly"].max_row == 25


def test_turkey_tags(collector):
    cat = views.load_tag_catalog()
    assert len(cat) == 305
    vals = views.catalog_values(collector, cat)
    assert vals[0]["name"].startswith("INVERTER_1.")
    wb = load_workbook(io.BytesIO(export.tags_xlsx(collector, cat)))
    assert wb["Tags"].max_row == 306


def test_api(client):
    assert client.get("/").status_code == 200
    assert client.get("/api/v1/health").json()["ok"] is True
    s = client.get("/api/v1/local/summary").json()
    assert s["kpi"]["load_kw"] is not None
    r = client.get("/api/v1/local/series?hours=6&keys=load_kw,pv_kw").json()
    assert set(r["series"]) == {"load_kw", "pv_kw"}
    assert client.get("/api/v1/local/series?hours=99999").status_code == 422
    a = client.get("/api/v1/local/alarms?hours=48").json()
    assert "events" in a and "active" in a
    x = client.get("/api/v1/local/export.xlsx?hours=2&step=300")
    assert x.status_code == 200 and x.headers["content-disposition"].endswith('.xlsx"')
    c = client.get("/api/v1/local/export.csv?hours=2&step=300")
    assert c.status_code == 200 and c.text.startswith("﻿time_utc,")
    assert client.get("/api/v1/local/report.xlsx").status_code == 200
    t = client.get("/api/v1/tags").json()
    assert t["count"] == 305
    assert client.get("/api/v1/tags/INVERTER_1.INV1_AC_POWER").json()["device"] == "inv-01"
    assert client.get("/api/v1/tags/NOPE").status_code == 404
    assert client.get("/api/v1/tags.csv").status_code == 200
    assert len(client.get("/api/v1/local/tags").json()) == sum(len(d.points) for d in client.app.state.collector.registry.devices.values())


def test_no_write_endpoints(client):
    for path in ("/api/v1/local/summary", "/api/v1/tags"):
        assert client.post(path).status_code == 405
