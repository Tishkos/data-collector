"""
Plant simulator — a shared, stepping model of the whole site.

One SimPlant instance holds all devices; each SimulatorAdapter is a view onto one
device in it. The plant conserves power: load = pv + bess_discharge + sum(gensets).
If dispatch can't cover load, the shortfall is recorded as `unserved_kw` (a fault).

It is deliberately simple but honest about the things that matter for control:
genset min load / min run / min stop / start delay / fuel curve, PV availability
from a sun curve + optional curtailment, BESS SOC window + BMS power limits +
round-trip losses. Good enough to test the gateway, EMS and AI end to end.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from ..registry.models import CommandDefinition, Device, PointDefinition, Quality
from .base import Adapter, Reading, make_reading


# --- device state -----------------------------------------------------------

@dataclass
class GensetState:
    running: bool = False
    power_kw: float = 0.0
    power_target_kw: float = 0.0
    run_hours: float = 1200.0
    fuel_level_pct: float = 80.0
    fuel_rate_lph: float = 0.0
    coolant_temp_c: float = 40.0
    oil_pressure_bar: float = 0.0
    battery_v: float = 25.6
    mode: str = "AUT"
    alarm_active: bool = False
    alarm_code: int = 0
    last_start: datetime | None = None
    last_stop: datetime | None = None
    tank_l: float = 2000.0  # 80% of 2500 L


@dataclass
class PVState:
    power_kw: float = 0.0
    available_kw: float = 0.0
    energy_today_kwh: float = 0.0
    energy_total_kwh: float = 950_000.0
    irradiance_wm2: float = 0.0
    inverters_online: int = 8
    status: int = 1
    power_limit_pct: float = 100.0
    soiling_factor: float = 1.0      # 1.0 clean; lower = dirty panels (fault scenario)


@dataclass
class BESSState:
    soc_pct: float = 55.0
    power_kw: float = 0.0            # + discharge / - charge
    power_target_kw: float = 0.0
    soh_pct: float = 97.0
    max_charge_kw: float = 300.0
    max_discharge_kw: float = 300.0
    cell_temp_max_c: float = 28.0
    status: int = 1
    alarm_active: bool = False
    efficiency: float = 0.92         # round-trip


@dataclass
class SimPlant:
    devices: dict[str, Device]
    load_profile_kw: list[float]
    now: datetime = field(default_factory=lambda: datetime(2026, 10, 12, 6, 0, tzinfo=timezone.utc))
    gensets: dict[str, GensetState] = field(default_factory=dict)
    pv: dict[str, PVState] = field(default_factory=dict)
    bess: dict[str, BESSState] = field(default_factory=dict)
    load_kw: float = 0.0
    unserved_kw: float = 0.0
    pv_auto_curtailed_kw: float = 0.0
    comms_down: set[str] = field(default_factory=set)   # device ids with simulated comms loss
    log: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for d in self.devices.values():
            if d.kind == "genset":
                self.gensets[d.id] = GensetState()
            elif d.kind == "pv":
                self.pv[d.id] = PVState()
            elif d.kind == "bess":
                self.bess[d.id] = BESSState()
        self.step(0)

    # --- environment ----------------------------------------------------------

    def _sun(self, t: datetime) -> float:
        """0..1 clear-sky curve, sunrise ~06:00 sunset ~18:00 local-ish."""
        h = t.hour + t.minute / 60.0
        if h < 6 or h > 18:
            return 0.0
        return max(0.0, math.sin(math.pi * (h - 6) / 12.0))

    def _load(self, t: datetime) -> float:
        h = t.hour
        a = self.load_profile_kw[h]
        b = self.load_profile_kw[(h + 1) % 24]
        return a + (b - a) * (t.minute / 60.0)

    # --- the step -------------------------------------------------------------

    def step(self, seconds: float) -> None:
        dt_h = seconds / 3600.0
        self.now += timedelta(seconds=seconds)
        self.load_kw = self._load(self.now)
        sun = self._sun(self.now)

        # PV
        pv_total = 0.0
        for did, s in self.pv.items():
            rated = self.devices[did].limits.rated_kw or 0.0
            s.irradiance_wm2 = round(1000.0 * sun, 1)
            s.available_kw = round(rated * sun * s.soiling_factor * (s.inverters_online / 8.0), 1)
            s.power_kw = round(min(s.available_kw, rated * s.power_limit_pct / 100.0), 1)
            s.energy_today_kwh += s.power_kw * dt_h
            s.energy_total_kwh += s.power_kw * dt_h
            if self.now.hour == 0 and self.now.minute == 0:
                s.energy_today_kwh = 0.0
            pv_total += s.power_kw

        # BESS first pass: follows its target within SOC + BMS limits (computed below in the
        # BESS block, but we need its value for the genset remainder) — so do a dry calc here.
        bess_planned = 0.0
        for did, b in self.bess.items():
            lim = self.devices[did].limits
            tgt = b.power_target_kw
            if tgt > 0:
                tgt = min(tgt, b.max_discharge_kw)
                if b.soc_pct <= (lim.soc_min_pct or 0):
                    tgt = 0.0
            elif tgt < 0:
                tgt = max(tgt, -b.max_charge_kw)
                if b.soc_pct >= (lim.soc_max_pct or 100):
                    tgt = 0.0
            bess_planned += tgt

        # Gensets are the swing source in island mode (InteliNeo isochronous / load sharing):
        # running sets pick up whatever PV + battery don't cover, sharing it in proportion to
        # their power targets, each clamped to [min_load, max_load] and ramp-limited.
        remainder = max(0.0, self.load_kw - pv_total - bess_planned)
        running = [(did, g) for did, g in self.gensets.items() if g.running]
        weights = {did: max(g.power_target_kw, self.devices[did].limits.min_load_kw or 1.0) for did, g in running}
        wsum = sum(weights.values()) or 1.0
        gen_total = 0.0
        for did, g in self.gensets.items():
            lim = self.devices[did].limits
            if g.running:
                ramp = (lim.ramp_kw_per_min or 1e9) * (seconds / 60.0)
                share = remainder * weights[did] / wsum
                target = min(max(share, lim.min_load_kw or 0.0), lim.max_load_kw or 1e9)
                g.power_kw = round(min(target, g.power_kw + ramp) if target > g.power_kw else max(target, g.power_kw - ramp), 1)
                frac = g.power_kw / (lim.rated_kw or 1.0)
                g.fuel_rate_lph = round(g.power_kw * self._l_per_kwh(lim, frac), 2)
                g.tank_l -= g.fuel_rate_lph * dt_h
                g.fuel_level_pct = round(100.0 * g.tank_l / 2500.0, 1)
                g.run_hours += dt_h
                g.coolant_temp_c = min(92.0, g.coolant_temp_c + 0.8 * seconds / 60.0)
                g.oil_pressure_bar = 4.2
                if frac < 0.3 and (self.now - (g.last_start or self.now)).total_seconds() > 3600:
                    pass  # low-load running — the fault detector will flag it from telemetry
            else:
                g.power_kw = 0.0
                g.fuel_rate_lph = 0.0
                g.oil_pressure_bar = 0.0
                g.coolant_temp_c = max(30.0, g.coolant_temp_c - 0.3 * seconds / 60.0)
            gen_total += g.power_kw

        # BESS: follows target within SOC + BMS limits
        bess_total = 0.0
        for did, b in self.bess.items():
            lim = self.devices[did].limits
            cap = lim.capacity_kwh or 1.0
            tgt = b.power_target_kw
            if tgt > 0:  # discharge
                tgt = min(tgt, b.max_discharge_kw)
                if b.soc_pct <= (lim.soc_min_pct or 0):
                    tgt = 0.0
            elif tgt < 0:  # charge
                tgt = max(tgt, -b.max_charge_kw)
                if b.soc_pct >= (lim.soc_max_pct or 100):
                    tgt = 0.0
            b.power_kw = round(tgt, 1)
            if tgt > 0:
                b.soc_pct -= 100.0 * (tgt * dt_h) / (cap * math.sqrt(b.efficiency))
            elif tgt < 0:
                b.soc_pct += 100.0 * (-tgt * dt_h * math.sqrt(b.efficiency)) / cap
            b.soc_pct = round(min(100.0, max(0.0, b.soc_pct)), 2)
            bess_total += b.power_kw

        # --- islanded power balance ------------------------------------------------
        # The EMS targets are open-loop between ticks. Physically, the grid-forming
        # BESS (droop) absorbs the imbalance within its limits, and PV inverters
        # auto-curtail on over-frequency if there's still surplus. Model both.
        imbalance = self.load_kw - (pv_total + gen_total + bess_total)   # + deficit / - surplus
        for did, b in self.bess.items():
            lim = self.devices[did].limits
            cap = lim.capacity_kwh or 1.0
            if imbalance > 0 and b.soc_pct > (lim.soc_min_pct or 0):
                extra = min(imbalance, b.max_discharge_kw - b.power_kw)
                if extra > 0:
                    b.power_kw = round(b.power_kw + extra, 1)
                    b.soc_pct = round(max(0.0, b.soc_pct - 100.0 * (extra * dt_h) / (cap * math.sqrt(b.efficiency))), 2)
                    imbalance -= extra
            elif imbalance < 0 and b.soc_pct < (lim.soc_max_pct or 100):
                extra = min(-imbalance, b.max_charge_kw + b.power_kw)   # room left to charge more
                if extra > 0:
                    b.power_kw = round(b.power_kw - extra, 1)
                    b.soc_pct = round(min(100.0, b.soc_pct + 100.0 * (extra * dt_h * math.sqrt(b.efficiency)) / cap), 2)
                    imbalance += extra
        self.pv_auto_curtailed_kw = 0.0
        if imbalance < 0:  # still surplus: inverters curtail
            for s in self.pv.values():
                cut = min(s.power_kw, -imbalance)
                s.power_kw = round(s.power_kw - cut, 1)
                s.energy_today_kwh -= cut * dt_h
                s.energy_total_kwh -= cut * dt_h
                self.pv_auto_curtailed_kw += cut
                imbalance += cut
                if imbalance >= 0:
                    break
        self.unserved_kw = round(max(0.0, imbalance), 1)

    @staticmethod
    def _l_per_kwh(lim, frac: float) -> float:
        curve = lim.fuel_l_per_kwh_curve or [(0.25, 0.42), (1.0, 0.26)]
        pts = sorted(curve)
        if frac <= pts[0][0]:
            return pts[0][1]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= frac <= x1:
                return y0 + (y1 - y0) * (frac - x0) / (x1 - x0)
        return pts[-1][1]

    # --- scenario injection (for tests / demos) ------------------------------

    def inject(self, scenario: str, device_id: str | None = None) -> None:
        if scenario == "pv_soiling":
            for s in self.pv.values():
                s.soiling_factor = 0.72
        elif scenario == "inverter_offline":
            for s in self.pv.values():
                s.inverters_online = 6
        elif scenario == "genset_alarm" and device_id:
            g = self.gensets[device_id]
            g.alarm_active, g.alarm_code = True, 1041   # e.g. "low fuel pressure"
        elif scenario == "comms_loss" and device_id:
            self.comms_down.add(device_id)
        elif scenario == "comms_restore" and device_id:
            self.comms_down.discard(device_id)
        elif scenario == "bess_hot":
            for b in self.bess.values():
                b.cell_temp_max_c = 47.0
                b.max_charge_kw = 120.0
        self.log.append(f"{self.now.isoformat()} inject {scenario} {device_id or ''}")


# --- adapter view -----------------------------------------------------------

@dataclass
class _Meter:
    load_kw: float
    frequency_hz: float
    voltage_v: float


class SimulatorAdapter(Adapter):
    name = "simulator"

    def __init__(self, device: Device, plant: SimPlant):
        super().__init__(device)
        self.plant = plant

    def _state(self):
        d = self.device
        if d.kind == "meter":
            p = self.plant
            sag = min(1.5, p.unserved_kw / 100.0)          # 1 Hz sag per 100 kW unserved (illustrative)
            return _Meter(load_kw=p.load_kw, frequency_hz=round(50.0 - sag, 2), voltage_v=round(400.0 - 8 * sag, 1))
        return {"genset": self.plant.gensets, "pv": self.plant.pv, "bess": self.plant.bess}[d.kind][d.id]

    def read(self, points: list[PointDefinition] | None = None) -> dict[str, Reading]:
        pts = points or self.device.points
        if self.device.id in self.plant.comms_down:
            return {p.id: make_reading(p, None, Quality.BAD, "comms timeout") for p in pts}
        st = self._state()
        out: dict[str, Reading] = {}
        for p in pts:
            val = getattr(st, p.id, None)
            r = make_reading(p, val)
            r.source_ts = self.plant.now
            out[p.id] = r
        return out

    def write(self, command: CommandDefinition, args: dict[str, Any]) -> None:
        st = self._state()
        d = self.device
        cid = command.id
        if d.kind == "genset":
            g: GensetState = st
            if cid == "set_power_target":
                g.power_target_kw = float(args["target_kw"])
            elif cid == "start":
                g.running, g.last_start = True, self.plant.now
                g.power_kw = d.limits.min_load_kw or 0.0
                g.power_target_kw = max(g.power_target_kw, d.limits.min_load_kw or 0.0)
            elif cid == "stop":
                g.running, g.last_stop, g.power_kw = False, self.plant.now, 0.0
            elif cid == "fault_reset":
                g.alarm_active, g.alarm_code = False, 0
            elif cid == "set_mode":
                g.mode = str(args["mode"])
        elif d.kind == "pv" and cid == "set_power_limit":
            st.power_limit_pct = float(args["limit_pct"])
        elif d.kind == "bess" and cid == "set_active_power_target":
            st.power_target_kw = float(args["target_kw"])
        else:
            raise ValueError(f"simulator: unsupported command {d.id}.{cid}")
        self.plant.log.append(f"{self.plant.now.isoformat()} write {d.id}.{cid} {args}")
