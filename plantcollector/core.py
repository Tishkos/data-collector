"""
Collector core: registry + adapters + store + a few health checks + daily totals.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone

from .adapters import build_adapter
from .adapters.simulator import SimPlant
from .registry import Quality, load_registry
from .store import TelemetryStore


class Collector:
    def __init__(self, config_path: str, data_dir: str = "./data", *, seed_hours: float = 0.0, fresh: bool = False,
                 initial_poll: bool = True):
        self.config_path = config_path
        self.registry = load_registry(config_path)
        self.simulated = all(d.adapter == "simulator" for d in self.registry.devices.values())
        self.plant: SimPlant | None = None
        if self.simulated:
            prof = self.registry.extra.get("site_load", {}).get("profile_kw") or [300] * 24
            self.plant = SimPlant(self.registry.devices, prof)
            if seed_hours > 0:                       # start the demo clock in the past, then catch up
                self.plant.now -= timedelta(hours=seed_hours)
        os.makedirs(data_dir, exist_ok=True)
        db = os.path.join(data_dir, "demo.sqlite" if self.simulated else "telemetry.sqlite")
        if fresh and self.simulated and os.path.exists(db):      # demo data is regenerated; real data is never deleted
            for ext in ("", "-wal", "-shm"):
                try:
                    os.remove(db + ext)
                except FileNotFoundError:
                    pass
        self.db_path = db
        self.store = TelemetryStore(db)
        self.adapters = {d.id: build_adapter(d, plant=self.plant) for d in self.registry.devices.values()}
        # adapters connect on their first read (a connect to an unreachable device blocks for its timeout)
        self._comms_lost: set[str] = set()
        self._alarm_on: set[str] = set()
        self._grid_off: set[str] = set()
        self._poll_lock = threading.RLock()
        self.started_at = datetime.now(timezone.utc)
        self.polls = 0
        self.last_poll_at: datetime | None = None
        if self.simulated and seed_hours > 0:
            self.poll()
            self._seed_demo(seed_hours)
        elif initial_poll:
            self.poll()                      # `run` skips this and polls from its own thread, so the
                                             # dashboard is up at once even when devices time out

    def _seed_demo(self, hours: float) -> None:
        """Generate demo history with a couple of scripted events so every panel has content."""
        assert self.plant is not None
        gens = [d.id for d in self.registry.by_kind("genset")]
        script = []                                   # (hours before now, scenario, device)
        if len(gens) > 1 and hours >= 12:
            script += [(min(hours - 1, 30), "comms_loss", gens[1]), (min(hours - 1, 30) - 0.4, "comms_restore", gens[1])]
        if gens and hours >= 6:
            script += [(min(hours - 0.5, 9), "genset_alarm", gens[0]), (min(hours - 0.5, 9) - 1.5, "genset_alarm_clear", gens[0])]
        script.sort(key=lambda s: -s[0])
        left = hours
        for at, scenario, dev in script:
            if left > at:
                self.advance((left - at) * 3600, step=300)
                left = at
            self.plant.inject(scenario, dev)
        if left > 0:
            self.advance(left * 3600, step=300)

    def now(self) -> datetime:
        return self.plant.now if self.plant is not None else datetime.now(timezone.utc)

    def close(self) -> None:
        for a in self.adapters.values():
            try:
                a.close()
            except Exception:
                pass

    # --- polling ----------------------------------------------------------------------

    def poll(self) -> None:
        """Read every device once. Safe to call from the API thread while the loop runs."""
        with self._poll_lock:
            self._poll()

    def _poll(self) -> None:
        for did, ad in self.adapters.items():
            try:
                rows = ad.read()
            except Exception as exc:
                d = self.registry.devices[did]
                from .adapters import reading
                rows = {p.id: reading(p, None, Quality.BAD, f"read failed: {exc}") for p in d.points}
            self.store.ingest(did, rows)
            self._health(did, rows)
        self.polls += 1
        self.last_poll_at = datetime.now(timezone.utc)

    def advance(self, seconds: float, step: float = 60.0) -> None:
        """Simulator only: move simulated time forward, polling every `step` seconds."""
        assert self.plant is not None
        left = seconds
        with self.store.bulk():
            while left > 0:
                s = min(step, left)
                self.plant.step(s)
                left -= s
                self.poll()

    def _health(self, did: str, rows) -> None:
        """Minimal event log: comms lost/restored and controller alarm bits."""
        ts = self.now()
        dev = self.registry.devices[did]
        measured = [r for pid, r in rows.items() if not (dev.point(pid) and dev.point(pid).raw.get("derived"))]
        all_bad = bool(measured) and all(r.quality == Quality.BAD for r in measured)
        if all_bad and did not in self._comms_lost:
            self._comms_lost.add(did)
            err = next((r.error for r in rows.values() if r.error), "")
            self.store.event("comms_lost", f"{did}: no valid data ({err})", device_id=did, severity="error", code="COMMS_LOST", ts=ts)
        elif not all_bad and did in self._comms_lost:
            self._comms_lost.discard(did)
            self.store.event("comms_restored", f"{did}: data restored", device_id=did, severity="info", code="COMMS_OK", ts=ts)
        av = rows.get("available") if dev.kind == "grid" else rows.get("mains_available")
        if av is not None and av.quality == Quality.GOOD:
            key = f"{did}:grid"
            if not av.value and key not in self._grid_off:
                self._grid_off.add(key)
                self.store.event("grid_lost", f"{did}: grid not available", device_id=did, severity="warning", code="GRID_OFF", ts=ts)
            elif av.value and key in self._grid_off:
                self._grid_off.discard(key)
                self.store.event("grid_restored", f"{did}: grid back", device_id=did, severity="info", code="GRID_OK", ts=ts)
        a = rows.get("alarm_active")
        if a is not None and a.quality == Quality.GOOD:
            if a.value and did not in self._alarm_on:
                self._alarm_on.add(did)
                code = rows.get("alarm_code")
                self.store.event("alarm", f"{did}: alarm active" + (f" (code {code.value})" if code and code.value else ""),
                                 device_id=did, severity="error", code="ALARM", ts=ts)
            elif not a.value and did in self._alarm_on:
                self._alarm_on.discard(did)
                self.store.event("alarm_cleared", f"{did}: alarm cleared", device_id=did, severity="info", code="ALARM_OK", ts=ts)

    def active_issues(self) -> list[dict]:
        out = [{"code": "COMMS_LOST", "device": d, "message": "no valid data"} for d in sorted(self._comms_lost)]
        out += [{"code": "ALARM", "device": d, "message": "controller alarm active"} for d in sorted(self._alarm_on)]
        out += [{"code": "GRID_OFF", "device": k.split(":")[0], "message": "grid not available"} for k in sorted(self._grid_off)]
        return out

    # --- daily totals ----------------------------------------------------------------------

    def daily_totals(self, day: datetime | None = None) -> dict:
        day = (day or self.now()).astimezone(timezone.utc)
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        reg, st = self.registry, self.store
        load = sum(st.integrate_kwh(m.id, "load_kw", start, end) for m in reg.by_kind("meter"))
        pv = sum(st.integrate_kwh(p.id, "power_kw", start, end) for p in reg.by_kind("pv"))
        pv_avail = sum(st.integrate_kwh(p.id, "available_kw", start, end) for p in reg.by_kind("pv"))
        gens = {}
        gen_kwh = fuel = 0.0
        for g in reg.by_kind("genset"):
            kwh = st.integrate_kwh(g.id, "power_kw", start, end)
            rows = st.history(g.id, "fuel_rate_lph", start, end)
            litres = sum((v0 + v1) / 2 * (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() / 3600
                         for (t0, v0), (t1, v1) in zip(rows, rows[1:]) if v0 is not None and v1 is not None)
            run = st.history(g.id, "running", start, end)
            hours = sum((datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() / 3600
                        for (t0, v0), (t1, _v1) in zip(run, run[1:]) if v0)
            gens[g.id] = {"energy_kwh": round(kwh, 1), "fuel_l": round(litres, 1), "run_hours": round(hours, 2),
                          "avg_load_kw": round(kwh / hours, 1) if hours > 0.05 else 0.0,
                          "l_per_kwh": round(litres / kwh, 3) if kwh > 1 else None}
            gen_kwh += kwh
            fuel += litres
        grid_kwh = sum(st.integrate_kwh(g.id, "power_kw", start, end) for g in reg.by_kind("grid"))
        if not reg.by_kind("grid"):
            grid_kwh = sum(st.integrate_kwh(d.id, "mains_power_kw", start, end) for d in reg.devices.values() if d.point("mains_power_kw"))
        grid_hours = 0.0
        for d in reg.devices.values():
            pid = "available" if d.kind == "grid" else ("mains_available" if d.point("mains_available") else None)
            if pid:
                rows = st.history(d.id, pid, start, end)
                grid_hours += sum((datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() / 3600
                                  for (t0, v0), (t1, _v1) in zip(rows, rows[1:]) if v0)
        bess_dis = bess_chg = 0.0
        for b in reg.by_kind("bess"):
            rows = st.history(b.id, "power_kw", start, end)
            for (t0, v0), (t1, v1) in zip(rows, rows[1:]):
                if v0 is None or v1 is None:
                    continue
                e = (v0 + v1) / 2 * (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() / 3600
                bess_dis += max(e, 0); bess_chg += max(-e, 0)
        # what the gensets would have burned without solar: everything the grid did not supply
        counter_l = max(0.0, load - grid_kwh) * 0.30            # ~0.30 L/kWh at typical load
        return {
            "date": start.date().isoformat(), "site": reg.site.name,
            "totals": {
                "load_kwh": round(load, 1), "pv_kwh": round(pv, 1), "pv_available_kwh": round(pv_avail, 1),
                "pv_curtailed_kwh": round(max(0.0, pv_avail - pv), 1),
                "pv_share_pct": round(100 * pv / load, 1) if load else None,
                "bess_discharged_kwh": round(bess_dis, 1), "bess_charged_kwh": round(bess_chg, 1),
                "genset_kwh": round(gen_kwh, 1), "fuel_l": round(fuel, 1),
                "fuel_cost": round(fuel * reg.site.fuel_price_per_l, 2),
                "fuel_saved_vs_no_solar_l": round(max(0.0, counter_l - fuel), 1),
                "grid_kwh": round(grid_kwh, 1), "grid_hours": round(grid_hours, 1),
                "grid_share_pct": round(100 * grid_kwh / load, 1) if load else None,
            },
            "gensets": gens,
            "events": [e for e in st.events(start, limit=300) if e["ts"] < end.isoformat()],
        }


def run_loop(c: Collector, interval: float, stop=None) -> None:
    while not (stop and stop.is_set()):
        t0 = time.time()
        try:
            if c.simulated:
                c.advance(interval, step=interval)
            else:
                c.poll()
        except Exception as exc:
            print(f"collector error: {exc}")
        time.sleep(max(0.0, interval - (time.time() - t0)))
