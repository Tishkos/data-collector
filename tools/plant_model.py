"""
Behaviour model of the Erbil hybrid plant: what the controller's rules decide over a day, under
today's settings and under proposed modifications. Pure Python, 1-minute steps, nothing talks to a device.

  python tools/plant_model.py                 prints the comparison table, writes docs/simulation/*.csv + .png
  python tools/plant_model.py --days 30 --seed 3

Where the numbers come from (see docs/SIMULATION.md):
  REAL  - archive / exports of 2026-10-14: Gen P Min 25 %, start/stop reserve 300/400 kW, min run power 210 kW,
          solar coverage ratio 20 %, PV step 100 kW, watchdog 75 kW / 60 s, caps 2,200 kW (one PV breaker)
          and 1,300 kW (one transformer), UPS shedding 70/35 % and 80/35 %, fuel 237.3 g/kWh, 4 gensets loaded
          = 3,851 kW running nominal, PV 3.2 MW; inverter 1 produced 601.6 kWh on 14 Oct (so ~6 MWh for the plant)
  ASSUMED - load profile (anchored on 1,859 kW at 05:28 and 1,482 kW at 17:28), clear-sky PV shape for Erbil in
          October, genset fuel curve shape, UPS critical load share and autonomy, cloud events, PV response delay
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import random
from dataclasses import dataclass, field, replace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ----------------------------------------------------------------------------- plant (REAL unless marked)
GEN_NOM_KW = 3851 / 4            # ASSUMED equal gensets: 3,851 kW running nominal with 4 loaded on 14 Oct
GENSETS = 5
PV_RATED_KW = 3200
FUEL_L_PER_KWH_FULL = 237.3 / 1000 / 0.84      # 237.3 g/kWh (Neo setpoint), diesel 0.84 kg/l -> 0.283 l/kWh
FUEL_IDLE_FRACTION = 0.10        # ASSUMED Willans line: l/h = nom * (0.10*full + (1-0.10)*full*load) -> 0.37 l/kWh at 25 %
STEP_S = 60


@dataclass
class Scenario:
    name: str
    manual_gensets: int | None = None   # None = automatic power management; n = n gensets kept running
    gen_p_min: float = 0.25             # REAL 25 %
    start_reserve_kw: float = 300       # REAL
    stop_reserve_kw: float = 400        # REAL
    min_run_power_kw: float = 210       # REAL
    solar_coverage: float = 0.20        # REAL dynamic spinning reserve: 20 % of PV output kept as reserve
    next_start_delay_s: int = 20        # REAL
    next_stop_delay_s: int = 180        # REAL
    pv_step_kw: float = 100             # REAL ramp step per control cycle (taken as per minute)
    watchdog_kw: float = 75             # REAL PV prod > PV req
    watchdog_s: int = 60                # REAL
    pv_follow_delay_s: int = 30         # ASSUMED SAU/inverter response to a lower setpoint
    pv_breakers_closed: int = 2         # REAL both closed on 14 Oct
    ups_shed_on: tuple = (0.70, 0.80)   # REAL UPS 1 / UPS 2 Load Shedd ON
    ups_shed_off: float = 0.35          # REAL
    ups_bridge: bool = False            # PROPOSED: UPS takes critical load for a few minutes after a PV drop
    ups_bridge_minutes: int = 8
    ups_critical_share: float = 0.05    # ASSUMED each UPS carries 5 % of the plant load
    ups_autonomy_min: float = 30        # ASSUMED battery autonomy at full critical load


@dataclass
class Day:
    name: str
    clearness: float = 1.0              # 1 = clear sky
    cloud_events: int = 0               # number of cloud passages
    grid_hours: tuple = ()              # (start_h, end_h) windows with government grid available
    seed: int = 1


@dataclass
class Result:
    scenario: str
    day: str
    engine_hours: float = 0
    fuel_l: float = 0
    pv_available_kwh: float = 0
    pv_used_kwh: float = 0
    pv_curtailed_kwh: float = 0
    genset_kwh: float = 0
    grid_kwh: float = 0
    load_kwh: float = 0
    avg_gen_load_pct: float = 0
    overload_minutes: int = 0
    watchdog_trips: int = 0
    ups_shed_minutes: int = 0
    starts: int = 0
    timeline: dict = field(default_factory=dict)


def load_profile_kw(minute: int) -> float:
    """ASSUMED bottling-plant day, anchored on the two readings of 14 Oct (1,859 kW 05:28, 1,482 kW 17:28)."""
    anchors = [(0, 1500), (4.5, 1550), (5.5, 1860), (7, 1900), (12, 1880), (14, 1750), (17, 1500), (17.5, 1480), (21, 1480), (24, 1500)]
    h = minute / 60
    for (h0, p0), (h1, p1) in zip(anchors, anchors[1:]):
        if h0 <= h <= h1:
            return p0 + (p1 - p0) * (h - h0) / (h1 - h0)
    return 1500


def pv_available_kw(minute: int, day: Day, rnd: random.Random, clouds: list) -> float:
    """Clear-sky bell for Erbil mid-October (sunrise ~06:05, sunset ~17:45, peak ~78 % of rated), times clouds."""
    h = minute / 60
    rise, set_ = 6.08, 17.75
    if h <= rise or h >= set_:
        return 0.0
    x = (h - rise) / (set_ - rise)
    bell = math.sin(math.pi * x) ** 1.3
    p = PV_RATED_KW * 0.78 * bell * day.clearness
    for (t0, t1, depth) in clouds:
        if t0 <= minute <= t1:
            p *= (1 - depth)
    return p


def make_clouds(day: Day, rnd: random.Random) -> list:
    out = []
    for _ in range(day.cloud_events):
        t0 = rnd.randint(7 * 60, 16 * 60)
        out.append((t0, t0 + rnd.randint(5, 20), rnd.uniform(0.4, 0.7)))
    return out


def grid_on(minute: int, day: Day) -> bool:
    return any(a * 60 <= minute < b * 60 for a, b in day.grid_hours)


def fuel_l_per_min(p_kw: float, nom_kw: float) -> float:
    if nom_kw <= 0:
        return 0.0
    load = max(0.0, min(1.0, p_kw / nom_kw))
    l_per_h = nom_kw * FUEL_L_PER_KWH_FULL * (FUEL_IDLE_FRACTION + (1 - FUEL_IDLE_FRACTION) * load)
    return l_per_h / 60


def simulate(sc: Scenario, day: Day) -> Result:
    rnd = random.Random(day.seed)
    clouds = make_clouds(day, rnd)
    r = Result(sc.name, day.name)
    running = sc.manual_gensets if sc.manual_gensets is not None else 2
    pv_actual = 0.0
    pv_request_prev = 0.0
    start_timer = stop_timer = 0
    pending_drop = []            # (minute_when_pv_follows, kw) for the watchdog
    tripped_until = -1
    ups_battery = [sc.ups_autonomy_min, sc.ups_autonomy_min]
    ups_on = [False, False]
    bridge_until = -1
    pv_prev_available = 0.0
    gen_load_sum = 0.0
    tl = {k: [] for k in ("load", "pv_available", "pv_used", "gensets", "gen_load_pct", "grid")}

    for m in range(24 * 60):
        load = load_profile_kw(m)
        avail = pv_available_kw(m, day, rnd, clouds)
        on_grid = grid_on(m, day)

        # --- UPS: shedding (existing logic) and the proposed bridge
        shed_kw = 0.0
        if sc.ups_bridge and avail < pv_prev_available - 250 and m > 0:   # a PV drop of more than 250 kW within a minute
            bridge_until = m + sc.ups_bridge_minutes
        for i in range(2):
            if ups_on[i]:
                ups_battery[i] -= 1
                if ups_battery[i] <= 0:
                    ups_on[i] = False
            else:
                ups_battery[i] = min(sc.ups_autonomy_min, ups_battery[i] + 0.2)
            if ups_on[i]:
                shed_kw += sc.ups_critical_share * load
                r.ups_shed_minutes += 1
        load_on_bus = load - shed_kw

        if on_grid:
            # gensets off, PV free up to the transformer export limit, grid takes the rest
            running = 0
            pv_req = min(avail, PV_RATED_KW)
            pv_actual = pv_req
            grid_kw = load_on_bus - pv_actual
            gen_kw = 0.0
            run_nom = 0.0
        else:
            run_nom = running * GEN_NOM_KW
            cap = PV_RATED_KW if sc.pv_breakers_closed == 2 else 2200
            # curtailment: gensets must keep Gen P Min of the running nominal
            pv_req = max(0.0, min(avail, cap, load_on_bus - sc.gen_p_min * run_nom))
            # step protection: the request may rise only pv_step_kw per cycle
            pv_req = min(pv_req, pv_request_prev + sc.pv_step_kw)
            # watchdog: a request drop is followed by the plant only after the response delay
            if pv_req < pv_request_prev - sc.watchdog_kw:
                pending_drop.append((m + max(1, sc.pv_follow_delay_s // STEP_S), pv_request_prev - pv_req))
                if sc.pv_follow_delay_s > sc.watchdog_s and m > tripped_until:
                    r.watchdog_trips += 1
                    tripped_until = m + 30           # PV breakers open, operator resets after ~30 min (OFF -> AUTO)
            pending_drop = [(t, kw) for t, kw in pending_drop if t > m]
            pv_actual = 0.0 if m <= tripped_until else pv_req
            pv_request_prev = pv_req if m > tripped_until else 0.0
            gen_kw = max(0.0, load_on_bus - pv_actual)
            grid_kw = 0.0

            # --- power management (automatic) or fixed number of gensets (manual)
            if sc.manual_gensets is None:
                reserve_needed = sc.start_reserve_kw + sc.solar_coverage * pv_actual
                if run_nom - gen_kw < reserve_needed and running < GENSETS:
                    start_timer += STEP_S
                    if start_timer >= sc.next_start_delay_s:
                        running += 1; r.starts += 1; start_timer = 0
                else:
                    start_timer = 0
                if running > 1 and (run_nom - GEN_NOM_KW) - gen_kw >= sc.stop_reserve_kw + sc.solar_coverage * pv_actual \
                        and gen_kw >= sc.min_run_power_kw:
                    stop_timer += STEP_S
                    if stop_timer >= sc.next_stop_delay_s:
                        running -= 1; stop_timer = 0
                else:
                    stop_timer = 0
                running = max(1, running)
            else:
                running = sc.manual_gensets

        gen_load_pct = gen_kw / run_nom if run_nom else 0.0
        if run_nom and gen_kw > run_nom:
            r.overload_minutes += 1
        # UPS shedding decision for the next minute (existing logic on genset load %)
        for i in range(2):
            if not on_grid and gen_load_pct > sc.ups_shed_on[i] and ups_battery[i] > 0:
                ups_on[i] = True
            elif gen_load_pct < sc.ups_shed_off and not (sc.ups_bridge and m < bridge_until):
                ups_on[i] = False
        if sc.ups_bridge and m < bridge_until and ups_battery[0] > 0:
            ups_on[0] = True

        # --- bookkeeping
        dt_h = 1 / 60
        r.load_kwh += load * dt_h
        r.pv_available_kwh += avail * dt_h
        r.pv_used_kwh += pv_actual * dt_h
        r.genset_kwh += gen_kw * dt_h
        r.grid_kwh += grid_kw * dt_h
        r.engine_hours += running * dt_h
        r.fuel_l += fuel_l_per_min(gen_kw / running, GEN_NOM_KW) * running if running else 0.0
        gen_load_sum += gen_load_pct
        pv_prev_available = avail
        for k, v in (("load", load), ("pv_available", avail), ("pv_used", pv_actual), ("gensets", running),
                     ("gen_load_pct", round(100 * gen_load_pct, 1)), ("grid", int(on_grid))):
            tl[k].append(round(v, 1))

    r.pv_curtailed_kwh = max(0.0, r.pv_available_kwh - r.pv_used_kwh)
    r.avg_gen_load_pct = 100 * gen_load_sum / (24 * 60)
    r.timeline = tl
    return r


SCENARIOS = [
    Scenario("S0 today: 4 gensets by hand", manual_gensets=4),
    Scenario("S1 power management AUTO, settings as they are"),
    Scenario("S2 AUTO + Gen P Min 20 %", gen_p_min=0.20),
    Scenario("S3 AUTO + UPS bridge, solar coverage 10 %", ups_bridge=True, solar_coverage=0.10),
]
DAYS = [
    Day("clear, no grid", clearness=1.0, cloud_events=0),
    Day("cloudy, no grid", clearness=0.9, cloud_events=8, seed=7),
    Day("clear, grid 22-06 h", clearness=1.0, grid_hours=((0, 6), (22, 24))),
]


def table(results: list[Result]) -> str:
    head = f"{'scenario':44} {'day':20} {'eng h':>6} {'fuel l':>7} {'PV used':>8} {'PV avail':>9} {'curtail':>8} {'gen %':>6} {'ovl min':>7} {'trips':>5} {'UPS min':>7}"
    lines = [head, "-" * len(head)]
    for r in results:
        lines.append(f"{r.scenario:44} {r.day:20} {r.engine_hours:6.1f} {r.fuel_l:7.0f} {r.pv_used_kwh:8.0f} {r.pv_available_kwh:9.0f} "
                     f"{100 * r.pv_curtailed_kwh / r.pv_available_kwh if r.pv_available_kwh else 0:7.0f}% {r.avg_gen_load_pct:6.0f} "
                     f"{r.overload_minutes:7d} {r.watchdog_trips:5d} {r.ups_shed_minutes:7d}")
    return "\n".join(lines)


def write_outputs(results: list[Result], outdir: str) -> None:
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "scenarios.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scenario", "day", "engine_hours", "fuel_l", "pv_used_kwh", "pv_available_kwh", "pv_curtailed_kwh",
                    "genset_kwh", "grid_kwh", "load_kwh", "avg_gen_load_pct", "overload_minutes", "watchdog_trips", "ups_shed_minutes", "starts"])
        for r in results:
            w.writerow([r.scenario, r.day, round(r.engine_hours, 2), round(r.fuel_l), round(r.pv_used_kwh), round(r.pv_available_kwh),
                        round(r.pv_curtailed_kwh), round(r.genset_kwh), round(r.grid_kwh), round(r.load_kwh), round(r.avg_gen_load_pct, 1),
                        r.overload_minutes, r.watchdog_trips, r.ups_shed_minutes, r.starts])
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return
    day = DAYS[1].name
    picks = [r for r in results if r.day == day and r.scenario.startswith(("S0", "S1", "S3"))]
    if not picks:
        return
    t = [m / 60 for m in range(24 * 60)]
    fig, axes = plt.subplots(3, 1, figsize=(13, 9.5), sharex=True, gridspec_kw={"height_ratios": [3, 2, 1.4]})
    ink, blue, green, orange, purple, grey = "#1f2937", "#2563eb", "#15803d", "#b45309", "#7c3aed", "#9ca3af"
    ax = axes[0]
    ax.plot(t, picks[0].timeline["load"], color=ink, lw=1.6, label="plant load")
    ax.fill_between(t, picks[0].timeline["pv_available"], color=green, alpha=0.12, lw=0)
    ax.plot(t, picks[0].timeline["pv_available"], color=green, lw=1.2, ls="--", label="PV available (sun)")
    for r, c in zip(picks, (orange, blue, purple)):
        ax.plot(t, r.timeline["pv_used"], color=c, lw=1.8, label=f"PV used — {r.scenario}")
    ax.set_ylabel("kW"); ax.set_ylim(0, 3400); ax.grid(alpha=0.25); ax.legend(loc="upper left", fontsize=9, ncol=2, frameon=False)
    ax.set_title(f"One simulated day ({day}) — what the rules decide under each scenario", loc="left", fontsize=12, color=ink)
    ax = axes[1]
    for r, c in zip(picks, (orange, blue, purple)):
        ax.plot(t, r.timeline["gen_load_pct"], color=c, lw=1.6, label=r.scenario)
    ax.axhline(25, color=grey, lw=1, ls=":"); ax.text(23.9, 27, "Gen P Min 25 %", ha="right", fontsize=8, color=grey)
    ax.axhline(70, color=grey, lw=1, ls=":"); ax.text(23.9, 72, "UPS 1 shedding 70 %", ha="right", fontsize=8, color=grey)
    ax.set_ylabel("genset load %"); ax.set_ylim(0, 110); ax.grid(alpha=0.25); ax.legend(loc="upper left", fontsize=9, frameon=False)
    ax = axes[2]
    for r, c in zip(picks, (orange, blue, purple)):
        ax.step(t, r.timeline["gensets"], color=c, lw=1.6, where="post", label=r.scenario)
    ax.set_ylabel("gensets running"); ax.set_ylim(0, 5.5); ax.set_yticks(range(0, 6)); ax.grid(alpha=0.25)
    ax.set_xlabel("hour of day (plant time)"); ax.set_xlim(0, 24); ax.set_xticks(range(0, 25, 2))
    fig.text(0.01, 0.005, "Model of the controller's rules, not the firmware. Load profile, clear-sky shape, fuel curve and UPS shares are assumptions; "
             "setpoints and capacities are the plant's (archive of 2026-10-14).", fontsize=8, color=grey)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(os.path.join(outdir, "one_day.png"), dpi=150)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "simulation"))
    ap.add_argument("--seed", type=int, default=None, help="cloud pattern seed for the cloudy day")
    a = ap.parse_args()
    days = DAYS if a.seed is None else [replace(d, seed=a.seed) if d.cloud_events else d for d in DAYS]
    results = [simulate(sc, d) for d in days for sc in SCENARIOS]
    print(table(results))
    s0 = next(r for r in results if r.scenario.startswith("S0") and r.day == days[0].name)
    s1 = next(r for r in results if r.scenario.startswith("S1") and r.day == days[0].name)
    print(f"\nclear day, S0 -> S1: fuel {s0.fuel_l:.0f} -> {s1.fuel_l:.0f} l ({100 * (s1.fuel_l - s0.fuel_l) / s0.fuel_l:+.0f} %), "
          f"PV used {s0.pv_used_kwh:.0f} -> {s1.pv_used_kwh:.0f} kWh, engine hours {s0.engine_hours:.0f} -> {s1.engine_hours:.0f}")
    write_outputs(results, a.out)
    print(f"written: {a.out}/scenarios.csv, one_day.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
