"""
Daily report data. All numbers are computed here from the telemetry store;
the AI (ai/agent.py) turns this dict into a narrative. Never the other way round.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..registry.loader import Registry
from ..telemetry.store import TelemetryStore


def _l_per_kwh(curve, frac):
    pts = sorted(curve or [(0.25, 0.42), (1.0, 0.26)])
    if frac <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= frac <= x1:
            return y0 + (y1 - y0) * (frac - x0) / (x1 - x0)
    return pts[-1][1]


def build_daily_report(reg: Registry, store: TelemetryStore, day: datetime) -> dict:
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    pol = reg.policy
    out: dict = {"site": pol.name, "date": start.date().isoformat(), "devices": {}, "totals": {}}

    load_kwh = 0.0
    for m in reg.by_kind("meter"):
        load_kwh += store.daily_energy(m.id, "load_kw", start)

    pv_kwh = sum(store.daily_energy(p.id, "power_kw", start) for p in reg.by_kind("pv"))
    pv_avail_kwh = sum(store.daily_energy(p.id, "available_kw", start) for p in reg.by_kind("pv"))
    bess_rows = [store.history(b.id, "power_kw", start, end) for b in reg.by_kind("bess")]
    bess_dis = bess_chg = 0.0
    for rows in bess_rows:
        for (t0, v0), (t1, v1) in zip(rows, rows[1:]):
            if v0 is None or v1 is None:
                continue
            dt_h = (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() / 3600
            e = (v0 + v1) / 2 * dt_h
            if e > 0:
                bess_dis += e
            else:
                bess_chg += -e

    gen_kwh_total = fuel_total = 0.0
    for g in reg.by_kind("genset"):
        kwh = store.daily_energy(g.id, "power_kw", start)
        rows = store.history(g.id, "fuel_rate_lph", start, end)
        litres = 0.0
        for (t0, v0), (t1, v1) in zip(rows, rows[1:]):
            if v0 is None or v1 is None:
                continue
            litres += (v0 + v1) / 2 * (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() / 3600
        run_rows = store.history(g.id, "running", start, end)
        run_h = 0.0
        for (t0, v0), (t1, v1) in zip(run_rows, run_rows[1:]):
            if v0:
                run_h += (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() / 3600
        out["devices"][g.id] = {
            "energy_kwh": round(kwh, 1), "fuel_l": round(litres, 1), "run_hours": round(run_h, 2),
            "avg_load_kw": round(kwh / run_h, 1) if run_h > 0.05 else 0.0,
            "l_per_kwh": round(litres / kwh, 3) if kwh > 1 else None,
        }
        gen_kwh_total += kwh
        fuel_total += litres

    # counterfactual: what would the day have cost with gensets only (no PV, no BESS)?
    gens = reg.by_kind("genset")
    if gens and load_kwh > 0:
        g = gens[0]
        rated = g.limits.rated_kw or 500
        # assume gensets would run at ~60% average load if they carried everything
        counter_l = load_kwh * _l_per_kwh(g.limits.fuel_l_per_kwh_curve, 0.6)
        fuel_saved_l = max(0.0, counter_l - fuel_total)
    else:
        fuel_saved_l = 0.0

    out["totals"] = {
        "load_kwh": round(load_kwh, 1),
        "pv_kwh": round(pv_kwh, 1),
        "pv_available_kwh": round(pv_avail_kwh, 1),
        "pv_curtailed_kwh": round(max(0.0, pv_avail_kwh - pv_kwh), 1),
        "pv_share_pct": round(100 * pv_kwh / load_kwh, 1) if load_kwh else None,
        "bess_discharged_kwh": round(bess_dis, 1),
        "bess_charged_kwh": round(bess_chg, 1),
        "genset_kwh": round(gen_kwh_total, 1),
        "fuel_l": round(fuel_total, 1),
        "fuel_cost": round(fuel_total * pol.fuel_price_per_l, 2),
        "fuel_saved_vs_diesel_only_l": round(fuel_saved_l, 1),
        "savings_vs_diesel_only": round(fuel_saved_l * pol.fuel_price_per_l, 2),
    }
    out["faults"] = [e for e in store.events(start, kind="fault", limit=200) if e["ts"] < end.isoformat()]
    out["commands"] = [e for e in store.events(start, kind="command_verified", limit=200) if e["ts"] < end.isoformat()]
    out["rejected_commands"] = [e for e in store.events(start, kind="command_rejected", limit=100) if e["ts"] < end.isoformat()]
    return out
