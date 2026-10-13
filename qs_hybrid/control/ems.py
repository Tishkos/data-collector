"""
Deterministic energy-management (dispatch) engine.

Given the live state and a Goal, produce a Plan: an ordered list of registered
commands with explanation and the cost of alternatives. The AI calls this; it
does not invent setpoints itself.

Merit order with real constraints:
  1. PV: use everything available (curtail only if nothing can absorb it)
  2. BESS: absorb surplus / cover deficit inside its SOC window and reserve
  3. Gensets: fewest sets, each >= min stable load, respecting min run/stop,
     start cost, and a firm reserve margin for load steps and clouds.

Goals: "min_cost" (default) or a share target like pv_share_pct=60 with
bess_reserve_pct=30 — a share target is interpreted as "PV should cover ~60% of
load *if the sun allows*; never curtail to hit it; keep >=30% SOC for later".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..registry.loader import Registry
from ..registry.models import Device, Quality
from ..telemetry.store import TelemetryStore


@dataclass
class Goal:
    objective: str = "min_cost"             # min_cost | share_target
    pv_share_pct: float | None = None       # e.g. 60
    bess_reserve_pct: float | None = None   # e.g. 30 -> don't discharge below this
    reserve_margin_kw: float = 60.0         # firm headroom for load steps / clouds
    horizon_min: int = 15


@dataclass
class Step:
    device_id: str
    command_id: str
    args: dict[str, Any]
    why: str


@dataclass
class Plan:
    goal: Goal
    load_kw: float
    pv_available_kw: float
    pv_dispatch_kw: float
    bess_dispatch_kw: float             # + discharge / - charge
    genset_dispatch: dict[str, float]   # device -> kW (0 = off)
    steps: list[Step] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)
    est_cost_per_h: float = 0.0
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    feasible: bool = True
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "goal": self.goal.__dict__, "load_kw": self.load_kw, "pv_available_kw": self.pv_available_kw,
            "pv_dispatch_kw": self.pv_dispatch_kw, "bess_dispatch_kw": self.bess_dispatch_kw,
            "genset_dispatch": self.genset_dispatch, "steps": [s.__dict__ for s in self.steps],
            "explanation": self.explanation, "est_cost_per_h": round(self.est_cost_per_h, 2),
            "alternatives": self.alternatives, "feasible": self.feasible, "warnings": self.warnings,
        }


class EMS:
    def __init__(self, registry: Registry, store: TelemetryStore, clock=None):
        self.reg = registry
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    # --- state helpers ----------------------------------------------------------

    def _v(self, d: Device, pid: str, default=None):
        v, q = self.store.value(d.id, pid, now=self.clock())
        return v if q == Quality.GOOD and v is not None else default

    def _load(self) -> float:
        for m in self.reg.by_kind("meter"):
            v = self._v(m, "load_kw")
            if v is not None:
                return float(v)
        tot = 0.0
        for d in self.reg.devices.values():
            if d.kind in ("genset", "pv", "bess"):
                tot += float(self._v(d, "power_kw", 0.0) or 0.0)
        return tot

    def _l_per_kwh(self, d: Device, kw: float) -> float:
        curve = sorted(d.limits.fuel_l_per_kwh_curve or [(0.25, 0.42), (1.0, 0.26)])
        f = kw / (d.limits.rated_kw or 1.0)
        if f <= curve[0][0]:
            return curve[0][1]
        for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
            if x0 <= f <= x1:
                return y0 + (y1 - y0) * (f - x0) / (x1 - x0)
        return curve[-1][1]

    def _pv_forecast(self, pvs: list[Device], pv_avail_now: float, horizon_min: int) -> float:
        """Linear trend of PV availability over the last 30 min, projected `horizon_min` ahead.
        Only ever lowers the estimate (a falling sun is the risk; a rising one is a bonus)."""
        from datetime import timedelta

        now = self.clock()
        slope_total = 0.0
        for p in pvs:
            rows = [(t, v) for t, v in self.store.history(p.id, "available_kw", now - timedelta(minutes=30), now) if v is not None]
            if len(rows) >= 2:
                t0, v0 = rows[0]
                t1, v1 = rows[-1]
                dt_min = (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() / 60.0
                if dt_min > 1:
                    slope_total += (v1 - v0) / dt_min
        if slope_total >= 0:
            return pv_avail_now
        return max(0.0, pv_avail_now + slope_total * horizon_min)

    def genset_cost_per_h(self, d: Device, kw: float) -> float:
        if kw <= 0:
            return 0.0
        return kw * self._l_per_kwh(d, kw) * self.reg.policy.fuel_price_per_l

    # --- the planner ------------------------------------------------------------

    def plan(self, goal: Goal | None = None, load_kw: float | None = None) -> Plan:
        goal = goal or Goal()
        pol = self.reg.policy
        load = float(load_kw if load_kw is not None else self._load())

        pvs = self.reg.by_kind("pv")
        bess = self.reg.by_kind("bess")
        gens = self.reg.by_kind("genset")

        pv_avail = sum(float(self._v(p, "available_kw", self._v(p, "power_kw", 0.0)) or 0.0) for p in pvs)
        plan = Plan(goal=goal, load_kw=load, pv_available_kw=pv_avail, pv_dispatch_kw=0.0,
                    bess_dispatch_kw=0.0, genset_dispatch={g.id: 0.0 for g in gens})

        # ---- 1. PV first: take everything -------------------------------------
        pv_use = min(pv_avail, load)
        surplus = max(0.0, pv_avail - load)
        residual = max(0.0, load - pv_avail)
        plan.explanation.append(f"Load {load:.0f} kW. PV available {pv_avail:.0f} kW → using {pv_use:.0f} kW.")
        if goal.pv_share_pct is not None:
            want = load * goal.pv_share_pct / 100.0
            if pv_avail < want:
                plan.warnings.append(f"PV share target {goal.pv_share_pct:.0f}% needs {want:.0f} kW but only {pv_avail:.0f} kW of sun is available — using all of it; the rest comes from battery/gensets.")
            else:
                plan.explanation.append(f"PV covers ≥{goal.pv_share_pct:.0f}% of load as requested.")

        # ---- 2. BESS second ----------------------------------------------------
        bess_kw = 0.0
        for b in bess:
            soc = self._v(b, "soc_pct")
            if soc is None:
                plan.warnings.append(f"{b.id}: SOC unknown — battery not dispatched.")
                continue
            lim = b.limits
            reserve = goal.bess_reserve_pct if goal.bess_reserve_pct is not None else (lim.soc_reserve_pct or lim.soc_min_pct or 10)
            dis_max = min(lim.discharge_kw_max or 0.0, float(self._v(b, "max_discharge_kw", lim.discharge_kw_max or 0.0) or 0.0))
            chg_max = min(lim.charge_kw_max or 0.0, float(self._v(b, "max_charge_kw", lim.charge_kw_max or 0.0) or 0.0))
            if surplus > 0 and soc < (lim.soc_max_pct or 95):
                take = min(surplus, chg_max)
                bess_kw -= take
                surplus -= take
                plan.explanation.append(f"{b.id}: charging {take:.0f} kW from PV surplus (SOC {soc:.0f}%).")
            elif residual > 0 and soc > reserve:
                give = min(residual, dis_max)
                bess_kw += give
                residual -= give
                plan.explanation.append(f"{b.id}: discharging {give:.0f} kW (SOC {soc:.0f}% > reserve {reserve:.0f}%).")
            elif residual > 0:
                plan.explanation.append(f"{b.id}: held at reserve ({soc:.0f}% ≤ {reserve:.0f}%) — not discharging.")
        plan.bess_dispatch_kw = round(bess_kw, 1)

        # ---- 3. Gensets last: unit commitment by enumeration ----------------
        # Spinning reserve: a genset is committed not only when there is a deficit now,
        # but when PV (trend-adjusted) + available battery can't cover load + margin.
        pv_forecast = self._pv_forecast(pvs, pv_avail, goal.horizon_min)
        firm_bess = 0.0
        for b in bess:
            soc = self._v(b, "soc_pct")
            lim = b.limits
            reserve = goal.bess_reserve_pct if goal.bess_reserve_pct is not None else (lim.soc_reserve_pct or lim.soc_min_pct or 10)
            if soc is not None and soc > reserve:
                firm_bess += min(lim.discharge_kw_max or 0.0, float(self._v(b, "max_discharge_kw", lim.discharge_kw_max or 0.0) or 0.0))
        gens_running = any(bool(self._v(g, "running", False)) for g in gens)
        short_of_reserve = (pv_forecast + firm_bess) < (load + goal.reserve_margin_kw)
        if residual <= 0 and short_of_reserve and not gens_running:
            plan.explanation.append(
                f"Reserve check: PV trend {pv_forecast:.0f} kW + battery {firm_bess:.0f} kW < load {load:.0f} + margin "
                f"{goal.reserve_margin_kw:.0f} kW → committing a genset ahead of the shortfall."
            )
            residual = max(residual, 1.0)   # force commitment; it will run at min load, battery rebalances
        if residual > 0:
            best = self._commit(gens, residual, goal.reserve_margin_kw)
            if best is None:
                plan.feasible = False
                plan.warnings.append(f"No genset combination can cover {residual:.0f} kW + reserve. Load shedding or manual action required.")
            else:
                combo, alloc, cost, alts = best
                plan.genset_dispatch = alloc
                plan.alternatives = alts
                plan.est_cost_per_h += cost
                for gid, kw in alloc.items():
                    if kw > 0:
                        plan.explanation.append(f"{gid}: {kw:.0f} kW ({100*kw/(self.reg.get(gid).limits.rated_kw or 1):.0f}% load, {self._l_per_kwh(self.reg.get(gid), kw):.2f} L/kWh).")
        else:
            plan.explanation.append("No genset needed: PV + battery cover the load.")

        # ---- rebalance: once gensets are committed (each >= min load), the battery
        # only fills the remaining gap. Keeps SOC for the evening instead of
        # over-discharging alongside a genset that must run at min load anyway.
        gen_total = sum(plan.genset_dispatch.values())
        if gen_total > 0 and bess:
            gap = load - pv_use - gen_total           # + means battery must discharge, - means it can charge
            new_bess = 0.0
            for b in bess:
                soc = self._v(b, "soc_pct")
                if soc is None:
                    continue
                lim = b.limits
                reserve = goal.bess_reserve_pct if goal.bess_reserve_pct is not None else (lim.soc_reserve_pct or lim.soc_min_pct or 10)
                dis_max = min(lim.discharge_kw_max or 0.0, float(self._v(b, "max_discharge_kw", lim.discharge_kw_max or 0.0) or 0.0))
                chg_max = min(lim.charge_kw_max or 0.0, float(self._v(b, "max_charge_kw", lim.charge_kw_max or 0.0) or 0.0))
                if gap > 0 and soc > reserve:
                    new_bess = min(gap, dis_max)
                elif gap < 0 and soc < (lim.soc_max_pct or 95):
                    new_bess = -min(-gap, chg_max)
            if abs(new_bess - bess_kw) > 1.0:
                plan.explanation.append(
                    f"Rebalanced battery to {new_bess:+.0f} kW so gensets run at efficient load and SOC is preserved."
                )
                bess_kw = new_bess
                plan.bess_dispatch_kw = round(bess_kw, 1)

        # curtail only if surplus still has nowhere to go
        pv_dispatch = pv_use + (pv_avail - load - surplus if pv_avail > load else 0.0)
        if surplus > 0:
            plan.warnings.append(f"{surplus:.0f} kW of PV must be curtailed (battery full/limited). Consider adding load or storage.")
            pv_dispatch = pv_avail - surplus
        plan.pv_dispatch_kw = round(pv_dispatch, 1)
        plan.est_cost_per_h += abs(bess_kw) * pol.bess_cycle_cost_per_kwh

        # ---- turn dispatch into registered commands --------------------------
        for p in pvs:
            rated = p.limits.rated_kw or 1.0
            limit = 100.0 if surplus <= 0 else max(p.limits.curtail_min_pct or 0.0, 100.0 * plan.pv_dispatch_kw / rated)
            cur = float(self._v(p, "power_limit_pct", 100.0) or 100.0)
            if abs(cur - limit) > 0.5:
                plan.steps.append(Step(p.id, "set_power_limit", {"limit_pct": round(limit, 1)},
                                       "lift curtailment" if limit > cur else "curtail PV surplus"))
        for b in bess:
            cur = float(self._v(b, "power_target_kw", 0.0) or 0.0)
            if abs(cur - plan.bess_dispatch_kw) > 2.0:
                plan.steps.append(Step(b.id, "set_active_power_target", {"target_kw": plan.bess_dispatch_kw},
                                       "charge from surplus" if plan.bess_dispatch_kw < 0 else "discharge to cover deficit" if plan.bess_dispatch_kw > 0 else "idle"))
        for g in gens:
            running = bool(self._v(g, "running", False))
            kw = plan.genset_dispatch.get(g.id, 0.0)
            tgt = float(self._v(g, "power_target_kw", 0.0) or 0.0)
            if kw > 0 and not running:
                plan.steps.append(Step(g.id, "start", {}, f"needed for {kw:.0f} kW"))
                plan.steps.append(Step(g.id, "set_power_target", {"target_kw": round(kw, 1)}, "after start"))
            elif kw > 0 and running and abs(tgt - kw) > 5:
                plan.steps.append(Step(g.id, "set_power_target", {"target_kw": round(kw, 1)}, "re-balance"))
            elif kw == 0 and running:
                plan.steps.append(Step(g.id, "stop", {}, "no longer needed (PV/battery cover load)"))
        return plan

    def _commit(self, gens: list[Device], need_kw: float, margin_kw: float):
        """Enumerate genset subsets; pick cheapest feasible allocation (fewest sets on ties)."""
        from itertools import combinations

        best = None
        alts: list[dict[str, Any]] = []
        for n in range(1, len(gens) + 1):
            for combo in combinations(gens, n):
                cap_max = sum(g.limits.max_load_kw or 0.0 for g in combo)
                cap_min = sum(g.limits.min_load_kw or 0.0 for g in combo)
                if cap_max < need_kw + margin_kw or cap_min > need_kw + 1e-6 and n > 1 and need_kw < cap_min:
                    continue
                if need_kw < cap_min:
                    # must run at min load -> surplus will charge battery/curtail; still feasible but note it
                    pass
                target = max(need_kw, cap_min)
                # proportional-to-rating allocation, clipped to [min,max]
                tot_rated = sum(g.limits.rated_kw or 0.0 for g in combo)
                alloc = {g.id: max(g.limits.min_load_kw or 0.0, min(g.limits.max_load_kw or 0.0, target * (g.limits.rated_kw or 0.0) / tot_rated)) for g in combo}
                cost = sum(self.genset_cost_per_h(g, alloc[g.id]) for g in combo)
                cost += sum(self.reg.policy.genset_start_cost for g in combo if not bool(self._v(g, "running", False)))
                for g in gens:
                    alloc.setdefault(g.id, 0.0)
                alts.append({"gensets": [g.id for g in combo], "cost_per_h": round(cost, 2), "alloc": {k: round(v, 1) for k, v in alloc.items() if v > 0}})
                if best is None or cost < best[2] - 1e-9 or (abs(cost - best[2]) < 1e-9 and n < len(best[0])):
                    best = (combo, {k: round(v, 1) for k, v in alloc.items()}, cost, None)
        if best is None:
            return None
        alts.sort(key=lambda a: a["cost_per_h"])
        return best[0], best[1], best[2], alts[:4]
