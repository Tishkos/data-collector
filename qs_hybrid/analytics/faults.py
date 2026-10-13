"""
Rule-based fault detector. Deterministic rules with durations so a one-sample
blip doesn't alarm; the AI explains what these mean and what to do.

Each rule looks at live values (+ short history where needed) and yields a Fault.
Peer comparison catches the silent failures: one genset burning more than its
twin at the same load, PV under-performing its own irradiance, etc.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..registry.loader import Registry
from ..registry.models import Device, Quality
from ..telemetry.store import TelemetryStore


@dataclass
class Fault:
    code: str
    device_id: str | None
    severity: str          # info | warning | error | critical
    message: str


class FaultDetector:
    def __init__(self, registry: Registry, store: TelemetryStore, clock=None):
        self.reg = registry
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._since: dict[str, datetime] = {}       # condition key -> first seen
        self._active: set[str] = set()

    def _v(self, d: Device, pid: str):
        v, q = self.store.value(d.id, pid, now=self.clock())
        return (v if q == Quality.GOOD else None), q

    def _persist(self, key: str, cond: bool, seconds: float) -> bool:
        """True only once `cond` has held for `seconds`."""
        now = self.clock()
        if not cond:
            self._since.pop(key, None)
            return False
        self._since.setdefault(key, now)
        return (now - self._since[key]).total_seconds() >= seconds

    def scan(self) -> list[Fault]:
        faults: list[Fault] = []
        now = self.clock()

        # --- comms / data quality ----------------------------------------------
        for d in self.reg.devices.values():
            latest = self.store.latest(d.id)
            bad = [r for r in latest.values() if r.quality != Quality.GOOD]
            if latest and self._persist(f"{d.id}:comms", len(bad) == len(latest), 120):
                faults.append(Fault("COMMS_LOST", d.id, "error", f"{d.id}: no valid telemetry for 2+ min ({bad[0].error})"))

        # --- gensets ---------------------------------------------------------------
        gens = self.reg.by_kind("genset")
        for g in gens:
            running, _ = self._v(g, "running")
            kw, _ = self._v(g, "power_kw")
            alarm, _ = self._v(g, "alarm_active")
            fuel, _ = self._v(g, "fuel_level_pct")
            temp, _ = self._v(g, "coolant_temp_c")
            oil, _ = self._v(g, "oil_pressure_bar")
            lim = g.limits
            if alarm:
                code, _ = self._v(g, "alarm_code")
                faults.append(Fault("GENSET_ALARM", g.id, "error", f"{g.id}: controller alarm active (code {code})"))
            if running and kw is not None and lim.min_load_kw and self._persist(f"{g.id}:lowload", kw < lim.min_load_kw, 900):
                faults.append(Fault("GENSET_LOW_LOAD", g.id, "warning", f"{g.id}: running at {kw:.0f} kW < min stable {lim.min_load_kw:.0f} kW for 15+ min (wet-stacking risk, poor L/kWh)"))
            if fuel is not None and fuel < 20:
                faults.append(Fault("FUEL_LOW", g.id, "warning" if fuel >= 10 else "critical", f"{g.id}: fuel {fuel:.0f}%"))
            if temp is not None and temp > 98:
                faults.append(Fault("COOLANT_HIGH", g.id, "error", f"{g.id}: coolant {temp:.0f}°C"))
            if running and oil is not None and oil < 1.5 and self._persist(f"{g.id}:oil", True, 30):
                faults.append(Fault("OIL_PRESSURE_LOW", g.id, "critical", f"{g.id}: oil pressure {oil:.1f} bar while running"))

        # genset running while PV surplus is being curtailed = money burning
        pvs = self.reg.by_kind("pv")
        pv_avail = sum(float(self._v(p, "available_kw")[0] or 0) for p in pvs)
        pv_now = sum(float(self._v(p, "power_kw")[0] or 0) for p in pvs)
        gen_now = sum(float(self._v(g, "power_kw")[0] or 0) for g in gens)
        if gen_now > 0 and pv_avail - pv_now > 50 and self._persist("site:gen_while_curtail", True, 600):
            faults.append(Fault("GEN_WHILE_PV_CURTAILED", None, "warning", f"Gensets supplying {gen_now:.0f} kW while {pv_avail-pv_now:.0f} kW of PV is curtailed for 10+ min — dispatch is wasting fuel"))

        # peer comparison: fuel rate per kW between running gensets
        running = [(g, float(self._v(g, "power_kw")[0] or 0), float(self._v(g, "fuel_rate_lph")[0] or 0)) for g in gens if self._v(g, "running")[0]]
        rates = [(g, f / kw) for g, kw, f in running if kw > 50]
        if len(rates) >= 2:
            med = sorted(r for _, r in rates)[len(rates) // 2]
            for g, r in rates:
                if med > 0 and r > 1.15 * med and self._persist(f"{g.id}:peer_fuel", True, 1800):
                    faults.append(Fault("GENSET_FUEL_OUTLIER", g.id, "warning", f"{g.id}: {r:.3f} L/kWh is {100*(r/med-1):.0f}% above its peers — check injectors/filters/load share"))

        # --- PV -----------------------------------------------------------------------
        for p in pvs:
            irr, _ = self._v(p, "irradiance_wm2")
            avail, _ = self._v(p, "available_kw")
            kw, _ = self._v(p, "power_kw")
            inv, _ = self._v(p, "inverters_online")
            rated = p.limits.rated_kw or 0.0
            if irr is not None and avail is not None and irr > 300 and rated:
                expected = rated * irr / 1000.0
                ratio = avail / expected if expected else 1.0
                if self._persist(f"{p.id}:underperf", ratio < 0.85, 1800):
                    faults.append(Fault("PV_UNDERPERFORMANCE", p.id, "warning", f"{p.id}: producing {100*ratio:.0f}% of expected for {irr:.0f} W/m² for 30+ min — soiling, shading or inverter derate"))
            if inv is not None and inv < 8:
                faults.append(Fault("INVERTER_OFFLINE", p.id, "error", f"{p.id}: {8-int(inv)} inverter(s) offline"))
            if kw is not None and avail is not None and avail - kw > 50 and self._persist(f"{p.id}:curtailed", True, 1800):
                faults.append(Fault("PV_CURTAILED", p.id, "info", f"{p.id}: {avail-kw:.0f} kW curtailed for 30+ min"))

        # --- BESS -----------------------------------------------------------------------
        for b in self.reg.by_kind("bess"):
            soc, _ = self._v(b, "soc_pct")
            t, _ = self._v(b, "cell_temp_max_c")
            soh, _ = self._v(b, "soh_pct")
            alarm, _ = self._v(b, "alarm_active")
            if alarm:
                faults.append(Fault("BESS_ALARM", b.id, "error", f"{b.id}: BMS alarm active"))
            if soc is not None and b.limits.soc_min_pct is not None and soc <= b.limits.soc_min_pct + 2:
                faults.append(Fault("BESS_SOC_LOW", b.id, "warning", f"{b.id}: SOC {soc:.0f}% at floor"))
            if t is not None and t > 45:
                faults.append(Fault("BESS_TEMP_HIGH", b.id, "error" if t < 50 else "critical", f"{b.id}: cell temp {t:.0f}°C"))
            if soh is not None and soh < 80:
                faults.append(Fault("BESS_SOH_LOW", b.id, "warning", f"{b.id}: state of health {soh:.0f}%"))

        # --- site --------------------------------------------------------------------
        ev = self.store.events(now - timedelta(minutes=3), kind="sim_tick", limit=1)
        if ev and "unserved_kw" in (ev[0]["data"] or ""):
            try:
                uns = float(ev[0]["data"].split("unserved_kw': ")[1].rstrip("}"))
                if uns > 5 and self._persist("site:unserved", True, 120):
                    faults.append(Fault("LOAD_UNSERVED", None, "critical", f"{uns:.0f} kW of load not served for 2+ min"))
            except (IndexError, ValueError):
                pass

        # de-duplicate against active set so the event log isn't spammed every poll
        out = []
        new_active = set()
        for f in faults:
            key = f"{f.code}:{f.device_id}"
            new_active.add(key)
            if key not in self._active:
                out.append(f)
        for key in self._active - new_active:
            code, did = key.split(":", 1)
            self.store.event("fault_cleared", f"{code} cleared", device_id=None if did == "None" else did, code=code, ts=now)
        self._active = new_active
        return out

    def active(self) -> list[str]:
        return sorted(self._active)
