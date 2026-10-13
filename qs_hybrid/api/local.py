"""
Local dashboard API — runs on the plant PC, no cloud, no login (bind to the LAN).

  GET  /                                  dashboard page
  GET  /api/v1/local/summary              KPI tiles + per-device headline values
  GET  /api/v1/local/series?hours=24      downsampled series for the charts
  GET  /api/v1/local/alarms               active faults + recent events
  GET  /api/v1/local/tags                 every point of every device, latest value + quality
  GET  /api/v1/local/export.csv?from&to&step   history, wide table (timestamp x tag)
  GET  /api/v1/local/export.xlsx?from&to&step  same, Excel, plus a Summary sheet
  GET  /api/v1/local/report.xlsx?date     daily report numbers as Excel
"""

from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse

from ..analytics.report import build_daily_report
from ..registry.models import Quality
from ..site import Site
from .app import site

router = APIRouter()
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

# chart series: (device kind, point) -> display name
HEADLINE = {
    "genset": ["power_kw", "running", "fuel_level_pct", "fuel_rate_lph", "run_hours", "alarm_active"],
    "pv": ["power_kw", "available_kw", "energy_today_kwh", "irradiance_wm2", "inverters_online", "power_limit_pct"],
    "bess": ["soc_pct", "power_kw", "cell_temp_max_c", "soh_pct"],
    "meter": ["load_kw", "frequency_hz", "voltage_v"],
}


def _val(s: Site, did: str, pid: str):
    v, q = s.store.value(did, pid, now=s.clock())
    return (v if q == Quality.GOOD else None), q.value


@router.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


@router.get("/api/v1/local/summary")
def summary(s: Site = Depends(site)):
    reg = s.registry
    gens = reg.by_kind("genset")
    pvs = reg.by_kind("pv")
    bess = reg.by_kind("bess")
    meters = reg.by_kind("meter")

    def tot(devs, pid):
        vals = [_val(s, d.id, pid)[0] for d in devs]
        vals = [float(v) for v in vals if v is not None]
        return round(sum(vals), 1) if vals else None

    load = tot(meters, "load_kw")
    pv = tot(pvs, "power_kw")
    gen = tot(gens, "power_kw")
    bess_kw = tot(bess, "power_kw")
    soc = _val(s, bess[0].id, "soc_pct")[0] if bess else None
    running = sum(1 for g in gens if _val(s, g.id, "running")[0])
    rep = build_daily_report(reg, s.store, s.clock())
    t = rep["totals"]
    devices = []
    for d in reg.devices.values():
        latest = s.store.latest(d.id)
        points = {}
        for pid in HEADLINE.get(d.kind, []):
            r = latest.get(pid)
            if r is not None:
                points[pid] = {"v": r.value, "q": r.quality.value, "unit": r.unit}
        comms = "ok" if latest and all(r.quality == Quality.GOOD for r in latest.values()) else ("lost" if latest and all(r.quality != Quality.GOOD for r in latest.values()) else "partial")
        devices.append({"id": d.id, "kind": d.kind, "model": f"{d.vendor} {d.model}".strip(), "comms": comms, "points": points})
    return {
        "site": reg.policy.name, "time": s.clock().isoformat(), "mode": reg.policy.mode.value, "simulated": s.simulated,
        "kpi": {
            "load_kw": load, "pv_kw": pv, "genset_kw": gen, "bess_kw": bess_kw, "soc_pct": soc,
            "gensets_running": running, "gensets_total": len(gens),
            "pv_share_pct_today": t.get("pv_share_pct"), "pv_kwh_today": t.get("pv_kwh"),
            "fuel_l_today": t.get("fuel_l"), "fuel_saved_l_today": t.get("fuel_saved_vs_diesel_only_l"),
            "genset_kwh_today": t.get("genset_kwh"), "load_kwh_today": t.get("load_kwh"),
        },
        "active_faults": s.faults.active(),
        "devices": devices,
    }


@router.get("/api/v1/local/series")
def series(hours: float = Query(24, gt=0, le=24 * 31), max_points: int = 400, s: Site = Depends(site)):
    reg = s.registry
    now = s.clock()
    since = now - timedelta(hours=hours)
    out: dict[str, list] = {}

    def add(name: str, devs, pid: str):
        acc: dict[str, float] = {}
        for d in devs:
            for ts, v in s.store.history(d.id, pid, since, now):
                if v is None:
                    continue
                acc[ts] = acc.get(ts, 0.0) + float(v)
        rows = sorted(acc.items())
        step = max(1, len(rows) // max_points)
        out[name] = [[ts, round(v, 1)] for ts, v in rows[::step]]

    add("load_kw", reg.by_kind("meter"), "load_kw")
    add("pv_kw", reg.by_kind("pv"), "power_kw")
    add("genset_kw", reg.by_kind("genset"), "power_kw")
    add("bess_kw", reg.by_kind("bess"), "power_kw")
    add("soc_pct", reg.by_kind("bess"), "soc_pct")
    for g in reg.by_kind("genset"):
        add(f"{g.id}_kw", [g], "power_kw")
    return {"from": since.isoformat(), "to": now.isoformat(), "series": out}


@router.get("/api/v1/local/alarms")
def alarms(hours: float = 24, s: Site = Depends(site)):
    since = s.clock() - timedelta(hours=hours)
    ev = [e for e in s.store.events(since, limit=300) if e["kind"] != "sim_tick"]
    return {"active": s.faults.active(), "events": ev}


@router.get("/api/v1/local/tags")
def tags(s: Site = Depends(site)):
    rows = []
    for d in s.registry.devices.values():
        for pid, r in s.store.latest(d.id).items():
            rows.append({"device": d.id, "point": pid, "value": r.value, "unit": r.unit,
                         "quality": r.quality.value, "ts": r.source_ts.isoformat(), "error": r.error})
    return rows


# --- exports ----------------------------------------------------------------------

def _parse_range(frm: str | None, to: str | None, s: Site) -> tuple[datetime, datetime]:
    now = s.clock()
    t1 = datetime.fromisoformat(to).replace(tzinfo=timezone.utc) if to else now
    t0 = datetime.fromisoformat(frm).replace(tzinfo=timezone.utc) if frm else t1 - timedelta(days=1)
    if t1 <= t0:
        raise HTTPException(400, "'to' must be after 'from'")
    if t1 - t0 > timedelta(days=62):
        raise HTTPException(400, "range too large (max 62 days)")
    return t0, t1


def _wide_table(s: Site, t0: datetime, t1: datetime, step_s: int) -> tuple[list[str], list[list]]:
    """Resample every point to a common grid (last value in each bucket)."""
    cols: list[str] = []
    grid: dict[str, dict[str, object]] = {}
    for d in s.registry.devices.values():
        for p in d.points:
            col = f"{d.id}/{p.id}"
            rows = s.store.history(d.id, p.id, t0, t1)
            if not rows:
                continue
            cols.append(col + (f" [{p.unit}]" if p.unit else ""))
            for ts, v in rows:
                t = datetime.fromisoformat(ts)
                bucket = t.replace(second=0, microsecond=0)
                if step_s >= 60:
                    bucket = bucket.replace(minute=(bucket.minute // max(1, step_s // 60)) * max(1, step_s // 60))
                key = bucket.isoformat()
                grid.setdefault(key, {})[cols[-1]] = v
    header = ["timestamp"] + cols
    body = [[k] + [grid[k].get(c) for c in cols] for k in sorted(grid)]
    return header, body


@router.get("/api/v1/local/export.csv")
def export_csv(frm: str | None = Query(None, alias="from"), to: str | None = None, step: int = 60, s: Site = Depends(site)):
    t0, t1 = _parse_range(frm, to, s)
    header, body = _wide_table(s, t0, t1, step)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(body)
    name = f"plant_data_{t0:%Y%m%d_%H%M}_{t1:%Y%m%d_%H%M}.csv"
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/api/v1/local/export.xlsx")
def export_xlsx(frm: str | None = Query(None, alias="from"), to: str | None = None, step: int = 60, s: Site = Depends(site)):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    t0, t1 = _parse_range(frm, to, s)
    header, body = _wide_table(s, t0, t1, step)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(header)
    for c in ws[1]:
        c.font = Font(name="Arial", bold=True)
        c.fill = PatternFill("solid", fgColor="DDEBF7")
    for row in body:
        ws.append(row)
    ws.freeze_panes = "B2"
    ws.column_dimensions["A"].width = 26
    for i in range(2, len(header) + 1):
        ws.column_dimensions[get_column_letter(i)].width = max(12, min(30, len(header[i - 1]) + 2))
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name="Arial")

    # Summary sheet: the day's computed numbers (from the telemetry store, as values)
    rep = build_daily_report(s.registry, s.store, t1)
    sm = wb.create_sheet("Summary")
    sm.append(["Site", rep["site"]])
    sm.append(["Range", f"{t0:%Y-%m-%d %H:%M} to {t1:%Y-%m-%d %H:%M} UTC"])
    sm.append(["Step", f"{step} s"])
    sm.append([])
    sm.append(["Daily totals (day of 'to')", ""])
    for k, v in rep["totals"].items():
        sm.append([k, v])
    sm.append([])
    sm.append(["Genset", "energy_kwh", "fuel_l", "run_hours", "avg_load_kw", "l_per_kwh"])
    for gid, d in rep["devices"].items():
        sm.append([gid, d["energy_kwh"], d["fuel_l"], d["run_hours"], d["avg_load_kw"], d["l_per_kwh"]])
    for row in sm.iter_rows():
        for c in row:
            c.font = Font(name="Arial", bold=(c.column == 1 and c.row in (1, 2, 3, 5)))
    sm.column_dimensions["A"].width = 32
    sm.column_dimensions["B"].width = 36

    out = io.BytesIO()
    wb.save(out)
    name = f"plant_data_{t0:%Y%m%d_%H%M}_{t1:%Y%m%d_%H%M}.xlsx"
    return Response(out.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/api/v1/local/report.xlsx")
def report_xlsx(date: str | None = None, s: Site = Depends(site)):
    import openpyxl
    from openpyxl.styles import Font

    day = datetime.fromisoformat(date).replace(tzinfo=timezone.utc) if date else s.clock()
    rep = build_daily_report(s.registry, s.store, day)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Daily report"
    ws.append(["Site", rep["site"]])
    ws.append(["Date", rep["date"]])
    ws.append([])
    ws.append(["Totals", ""])
    for k, v in rep["totals"].items():
        ws.append([k, v])
    ws.append([])
    ws.append(["Genset", "energy_kwh", "fuel_l", "run_hours", "avg_load_kw", "l_per_kwh"])
    for gid, d in rep["devices"].items():
        ws.append([gid, d["energy_kwh"], d["fuel_l"], d["run_hours"], d["avg_load_kw"], d["l_per_kwh"]])
    ws.append([])
    ws.append(["Faults", "device", "severity", "message", "time"])
    for e in rep["faults"]:
        ws.append([e["code"], e["device_id"], e["severity"], e["message"], e["ts"]])
    for row in ws.iter_rows():
        for c in row:
            c.font = Font(name="Arial", bold=(c.column == 1 and c.value in ("Site", "Date", "Totals", "Genset", "Faults")))
    ws.column_dimensions["A"].width = 32
    for col in "BCDEF":
        ws.column_dimensions[col].width = 18
    out = io.BytesIO()
    wb.save(out)
    return Response(out.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="daily_report_{rep["date"]}.xlsx"'})
