"""
PLC lab: the plant and the master's PLC program running live in the browser, with every block of every
sheet shown with its live pin values, like the controller's own PLC monitor, but on a model you can poke.

    python tools/plc_lab.py                                  -> http://localhost:8090
    python tools/plc_lab.py --speed 10 --hour 8.8
    python tools/plc_lab.py --replay data/trace/neo-01_2026-10-16.csv [--replay-sau data/trace/pv-array_2026-10-16.csv]

Nothing here touches the plant.

What is exact: the PLC program (the exported sheets, executed block by block at the controller's 100 ms cycle),
its setpoints, the ECU refresh and timeout figures from the archive, the ramp constants, the load anchors and
the grid hours from the history, the sun curve for mid-October in Erbil.
What is modelled (marked ASSUMED in plant_model.py and adjustable on the page): how fast the inverters follow
a setpoint, the controller's curtailment regulator, what the SAU does with a write while it is not answering,
load noise, cloud edges. Replay mode removes most of that: the PLC then runs on the values the trace recorded
on the plant, and the page shows the recorded setpoint next to the one the program computes.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import random
import sys
import threading
import time
from collections import deque
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from neo_plc import Plc, load_program, plant_inputs  # noqa: E402
import plant_model as pm  # noqa: E402

try:
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse
except ImportError:                                     # the model part works without the web part
    FastAPI = Request = HTMLResponse = JSONResponse = None

CHANGE_DIR = os.path.join(ROOT, "docs", "changes", "change-01_comm_fail_tolerance")
PROGRAMS = {
    "today": (pm.program, {}),
    "change-01 v2 (hold until the link is back)": (lambda: load_program(os.path.join(CHANGE_DIR, "program_v2")), {65514: 300.0}),
    "change-01 v3 (hold 60 s, then 0)": (lambda: load_program(os.path.join(CHANGE_DIR, "program_v3")), {}),
}
HISTORY_S = 900
PLC_CYCLE_S = 0.1            # REAL: the controller's PLC cycle
ECU_REFRESH_S = 1.0          # REAL: the PV logger group 1 session refreshes its values every 1,000 ms (archive)
SESSION2_WRITE_S = 10.0      # REAL: group 2 writes its setpoints every 10,000 ms or on change (archive)

# knobs: the figures the trace will measure; until then these are the archive values and the model's assumptions
DEFAULT_PARAMS = dict(
    timeout_s=3.0,           # REAL: Modbus timeout of the PV logger sessions (archive)
    retries=2,               # ASSUMED: lost replies before the module declares the comm fail (the integrator: 6-9 s)
    inverter_tau_s=10.0,     # ASSUMED: first-order response of the inverters to a setpoint
    inverter_ramp_kw_s=0.0,  # ASSUMED: 0 = no extra rate limit in the SAU (set e.g. 100 for 100 kW/s)
    curtail_rate=0.03,       # CALIBRATED: the controller's curtailment regulator, %/s per % of error
    gen_p_min_pct=25.0,      # REAL: Gen P Min
    load_noise=1.0,          # ASSUMED: slow random load variation, % of load
    cloud_edge_s=20.0,       # ASSUMED: a cloud edge takes this long to pass
    writes_through_silence=0,  # HYPOTHESIS: 1 = a write still lands in the SAU while it gives no replies
)


# Scenarios: a program, how the gensets are run, and the model knobs. The first is the plant as it runs today.
SCENARIOS = [
    dict(name="Default: today's program, 3 gensets by hand", program="today", gensets=3, auto_pm=False,
         note="The plant as recorded on 14 October: gensets started by hand, Gen P Min 25 %, any comm fail forces the PV request to 0."),
    dict(name="Change 01 v3: hold 60 s, then 0", program="change-01 v3 (hold 60 s, then 0)", gensets=3, auto_pm=False,
         note="Sheet 10 keeps the last request for 60 s after a comm fail is declared, then 0 exactly as today. Nothing else changes."),
    dict(name="Change 01 v2: hold until the link is back, PV breakers open after 5 min", program="change-01 v2 (hold until the link is back)", gensets=3, auto_pm=False,
         note="Sheet 10 holds as long as the fail lasts; after 5 minutes without the logger sheet 3 opens the PV breakers (stage 2)."),
    dict(name="Power management AUTO, settings as they are", program="today", gensets=None, auto_pm=True,
         note="The master starts and stops gensets itself with the reserves and delays from the archive; today's program."),
    dict(name="AUTO + Gen P Min 20 %", program="today", gensets=None, auto_pm=True, params=dict(gen_p_min_pct=20.0),
         note="As above with the gensets allowed down to 20 % instead of 25 %: more solar, closer to the engine's floor."),
    dict(name="AUTO + change 01 v3 + UPS fixes", program="change-01 v3 (hold 60 s, then 0)", gensets=None, auto_pm=True,
         ups_math_fix=True, ups_shed_delay_s=10.0,
         note="Power management on, the comm-fail hold, the UPS sheet using the real genset nominal and a 10 s shedding delay (the model's S3)."),
    dict(name="Two gensets in the sun", program="change-01 v3 (hold 60 s, then 0)", gensets=2, auto_pm=False,
         note="Two sets by hand through the day with the comm-fail hold: the first real fuel step. Watch gen load % on clouds and fails."),
    dict(name="AUTO, two gensets minimum", program="change-01 v3 (hold 60 s, then 0)", gensets=None, auto_pm=True, min_gensets=2,
         note="Power management never below two sets (the model's S6): the safe version of the step above."),
    dict(name="Grid day with export", program="today", gensets=0, auto_pm=False, grid=True,
         note="On the grid: no gensets, the PV request is the SAU maximum capped by the export limit of 1,300 kW per feeder."),
]
SCENARIO_BY_NAME = {s["name"]: s for s in SCENARIOS}


class LivePlant:
    """plant_model.simulate(), unrolled into one-second ticks (ten PLC cycles each) with injectable events."""

    def __init__(self, program_name: str = "today", start_hour: float = 10.0, manual_gensets: int = 3, replay=None):
        self.lock = threading.RLock()
        self.replay = replay                               # ReplaySource or None
        self.start_hour = start_hour
        self.speed = 1.0
        self.paused = False
        self.scenario = SCENARIOS[0]["name"]
        self.program_name = program_name
        self.manual_gensets = manual_gensets
        self.auto_pm = False
        self.min_gensets = 1
        self.ups_math_fix = False
        self.ups_shed_delay_s = 0.0
        self.grid_override = None
        self.params = dict(DEFAULT_PARAMS)
        self._init_state()

    def _init_state(self):
        """Everything back to zero: a fresh PLC, a fresh plant, empty history and counters; the knobs stay."""
        self.sc = pm.Scenario("lab", manual_gensets=self.manual_gensets)
        self.day = pm.Day("lab day", clearness=1.0)
        self.t = self.replay.t0 if self.replay else self.start_hour * 3600.0
        self.plc = None
        self._new_plc(self.program_name)
        self.rnd = random.Random(14)
        self.running = self.manual_gensets or 0
        self.pv_actual = 0.0
        self.sau_value = 0.0                               # what the SAU would answer right now
        self.sau_reading = 0.0                             # the value the controller holds (refreshed every 1 s while answered)
        self.received_setpoint = 0.0                       # the last setpoint that reached the SAU
        self.curtail = 30.0
        self.pv_cb = [1, 1]
        self.ups_closed = [0, 0]
        self.fail_since = None
        self.last_transfer = -1e9
        self.warn_timer = 0.0
        self.load_offset = 0.0
        self.load_noise_kw = 0.0
        self.clouds = []                                   # (start, end, depth)
        self.silent_until = [-1.0, -1.0]                   # the SAU gives no replies on session i until t
        self.last_reply = [self.t, self.t]
        self.comm_flag = [0, 0]
        self.off_until = -1.0
        self.fault_reset_pending = False
        self.start_timer = self.stop_timer = 0
        self.ups_shed_timer = [0.0, 0.0]
        self.schedule = []                                 # (t, what, value) from the 14 October replay
        self.totals = dict(seconds=0, pv_used_kwh=0.0, pv_available_kwh=0.0, gen_kwh=0.0, fuel_l=0.0, comm_fails=0,
                           zero_setpoints=0, warnings=0, ups_sheds=0, min_gen_load_pct=None, max_gen_load_pct=0.0)
        self._prev_comm_any = False
        # a steady start: the regulator already sits where it keeps the gensets at Gen P Min
        if not self.replay:
            load0 = pm.load_profile_kw(int(self.t / 60))
            avail0 = pm.pv_available_kw(self.t / 60, self.day, [])
            run_nom0 = 0.0 if self.on_grid() else sum(pm.GEN_NOMS_KW[:max(self.running, 1)])
            wanted = max(0.0, min(avail0, load0 - self.params["gen_p_min_pct"] / 100 * run_nom0)) if run_nom0 else min(avail0, load0)
            self.curtail = 0.0 if self.on_grid() else max(0.0, min(100.0, wanted / pm.SAU_MAX_AVAILABLE_KW * 100))
            self.pv_actual = self.sau_value = self.sau_reading = self.received_setpoint = wanted
        self.hist = {k: deque(maxlen=HISTORY_S) for k in ("t", "load", "pv_available", "pv_actual", "setpoint", "received", "request",
                                                            "gen_kw", "gen_load_pct", "comm", "silent", "recorded_setpoint")}
        self.events = deque(maxlen=200)
        self._last_bits = {}
        self.ticks = 0
        self.snapshot = {}
        self._warm_up()

    # ---- program -------------------------------------------------------------------------------
    def _new_plc(self, name: str):
        loader, setpoints = PROGRAMS[name]
        self.plc = Plc(loader(), setpoints, breaker_timeout_s=15)
        self.program_name = name

    def set_program(self, name: str):
        with self.lock:
            self._new_plc(name)
            self._warm_up()
            self.log(f"program: {name}")

    def _warm_up(self):
        for _ in range(30):
            self._tick(warm=True)

    def apply_scenario(self, name: str):
        sc = SCENARIO_BY_NAME[name]
        with self.lock:
            self.scenario = name
            self.program_name = sc["program"]
            self.manual_gensets = sc["gensets"] if sc["gensets"] is not None else 3
            self.auto_pm = bool(sc.get("auto_pm"))
            self.min_gensets = int(sc.get("min_gensets", 1))
            self.ups_math_fix = bool(sc.get("ups_math_fix"))
            self.ups_shed_delay_s = float(sc.get("ups_shed_delay_s", 0.0))
            self.grid_override = True if sc.get("grid") else None
            self.params = dict(DEFAULT_PARAMS)
            self.params.update(sc.get("params", {}))
            self._init_state()
            self.log(f"scenario: {name}")

    def reset(self):
        with self.lock:
            self._init_state()
            self.log("reset: plant and program restarted from zero")

    # ---- helpers ---------------------------------------------------------------------------------
    def log(self, text: str):
        self.events.appendleft((self._clock(), text))

    def _clock(self) -> str:
        s = int(self.t) % 86400
        return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"

    def on_grid(self) -> bool:
        if self.grid_override is not None:
            return self.grid_override
        return pm.grid_on(self.t / 60, self.day)

    def _cloud_factor(self) -> float:
        f = 1.0
        edge = max(1.0, self.params["cloud_edge_s"])
        for (a, b, depth) in self.clouds:
            if a - edge <= self.t <= b + edge:
                if self.t < a:
                    d = depth * (self.t - (a - edge)) / edge
                elif self.t > b:
                    d = depth * ((b + edge) - self.t) / edge
                else:
                    d = depth
                f *= (1 - d)
        return f

    def _sessions(self, dt: float):
        """The two PV logger sessions: a reply every second while the SAU answers; the comm fail flag of a session
        rises after timeout x (retries + 1) seconds without a reply and clears at the first reply."""
        declare_after = self.params["timeout_s"] * (self.params["retries"] + 1)
        for i in range(2):
            silent = self.t < self.silent_until[i]
            if not silent:
                self.last_reply[i] = self.t
            flag = int(self.t - self.last_reply[i] >= declare_after)
            self.comm_flag[i] = flag
        return [self.t < s for s in self.silent_until]

    # ---- one second ----------------------------------------------------------------------------------
    def _tick(self, warm: bool = False):
        for (ts, what, value) in [s for s in self.schedule if s[0] <= self.t]:
            self.schedule.remove((ts, what, value))
            self.inject(what, value, scheduled=True)
        sc, plc, P = self.sc, self.plc, self.params
        dt = 1.0
        if self.replay:
            self._tick_replay(warm)
            return
        minute = self.t / 60
        if P["load_noise"] > 0:
            self.load_noise_kw += self.rnd.gauss(0, 3) - 0.02 * self.load_noise_kw
        base_load = pm.load_profile_kw(int(minute))
        load = base_load + self.load_offset + self.load_noise_kw * P["load_noise"]
        avail = pm.pv_available_kw(minute, self.day, []) * self._cloud_factor()
        on_grid = self.on_grid()
        if self.grid_override is None and on_grid != pm.grid_on((self.t - dt) / 60, self.day):
            self.last_transfer = self.t
        if on_grid:
            self.running = 0
            run_nom = 0.0
        else:
            if self.running == 0:
                self.running = max(self.min_gensets, self.manual_gensets or 3)
            run_nom = sum(pm.GEN_NOMS_KW[:self.running])
        pvcb_close = int(not (self.t < self.off_until or self.t - self.last_transfer < 120))
        silent = self._sessions(dt)
        comm = list(self.comm_flag)
        comm_any = bool(comm[0] or comm[1])
        fault_reset = 0
        if self.fault_reset_pending or (self.fail_since is not None and self.t - self.fail_since >= 120):
            fault_reset, self.fail_since, self.fault_reset_pending = 1, None, False
        pv_live = bool(pvcb_close and any(self.pv_cb))
        if not pv_live:
            self.pv_actual = 0.0
        # the controller's copy of the SAU values refreshes once a second while session 1 answers; stale otherwise
        if not silent[0]:
            self.sau_reading = self.sau_value
        neo_pv_term = self.sau_reading if (pv_live or self.ups_math_fix) else 0.0
        shed_load = sum(sc.ups_critical_share * load for i in range(2) if not self.ups_closed[i])
        load_on_bus = load - shed_load
        gen_kw = 0.0 if on_grid else max(0.0, load_on_bus - self.pv_actual)
        mains_each = (load_on_bus - self.pv_actual) / 3 if on_grid else 0.0
        inputs = plant_inputs(pv_kw=self.sau_reading, max_avail_kw=pm.SAU_MAX_AVAILABLE_KW, curtailment_pct=self.curtail,
                              gensets_kw=gen_kw, running_nominal_kw=run_nom, mains_import_kw=(mains_each,) * 3,
                              on_grid=on_grid, gens_loaded=self.running, pv_cb=tuple(self.pv_cb), ups_fb=tuple(self.ups_closed),
                              comm_fail=(comm[0], comm[1]), pvcb_close=pvcb_close, fault_reset=fault_reset,
                              pv_valid=not comm_any, neo_pv_sample_kw=neo_pv_term)
        for _ in range(int(round(dt / PLC_CYCLE_S))):          # ten PLC cycles per second, inputs refreshed once a second
            plc.step(inputs, PLC_CYCLE_S)
        setpoint = plc.get(21267) or 0.0
        request = plc.get(21265) or 0.0
        cb_cmd = [plc.get(10425, 2) or 0, plc.get(10426, 2) or 0]
        ups_cmd = [plc.get(10427, 2) or 0, plc.get(10434, 2) or 0]
        self.warn_timer = self.warn_timer + dt if plc.get(10433, 2) else 0.0
        if any(plc.get(n, b) for n, b in ((10425, 7), (10426, 0), (10426, 7), (10427, 0), (10427, 7), (10428, 0), (10434, 7), (10435, 0))):
            self.fail_since = self.fail_since if self.fail_since is not None else self.t
        self.pv_cb = cb_cmd
        for i in range(2):                                   # the UPS breakers follow the commands, shedding after the optional delay
            if ups_cmd[i]:
                self.ups_shed_timer[i] = 0.0
                self.ups_closed[i] = 1
            else:
                self.ups_shed_timer[i] += dt
                if self.ups_shed_timer[i] >= self.ups_shed_delay_s:
                    if self.ups_closed[i] and not warm:
                        self.totals["ups_sheds"] += 1
                    self.ups_closed[i] = 0
        # the setpoint reaches the SAU through session 1 every second, through session 2 every 10 s, only while
        # that session gets replies (unless the hypothesis switch says writes land anyway)
        through = P["writes_through_silence"] or (not silent[0]) or (not silent[1] and int(self.t) % int(SESSION2_WRITE_S) == 0)
        if through:
            self.received_setpoint = setpoint
        # the inverters: first-order response to the setpoint they received, with an optional rate limit
        if pvcb_close and any(self.pv_cb):
            target = min(avail, self.received_setpoint)
            step = (target - self.pv_actual) * (1 - math.exp(-dt / max(0.5, P["inverter_tau_s"])))
            lim = P["inverter_ramp_kw_s"] * dt
            if lim > 0:
                step = max(-lim, min(lim, step))
            self.pv_actual += step
        else:
            self.pv_actual = 0.0
        self.sau_value = self.pv_actual
        if not on_grid and run_nom:
            gen_load_now = max(0.0, load_on_bus - self.pv_actual) / run_nom * 100
            self.curtail = max(0.0, min(100.0, self.curtail + P["curtail_rate"] * (gen_load_now - P["gen_p_min_pct"]) * dt))
        elif on_grid:
            self.curtail = 0.0
        if self.auto_pm and not on_grid and int(self.t) % 60 == 0:
            self._power_management(run_nom, gen_kw)
        if warm:
            return
        self.t += dt
        self.ticks += 1
        gl = 100 * gen_kw / run_nom if run_nom else 0.0
        self._totals(dt, avail, gen_kw, run_nom, gl, comm)
        self._record(load, avail, setpoint, request, gen_kw, gl, comm_any, any(silent), None)
        self.snapshot = dict(clock=self._clock(), load=round(load), pv_available=round(avail), pv_actual=round(self.pv_actual),
                             setpoint=round(setpoint, 1), received=round(self.received_setpoint, 1), request=round(request, 1),
                             gen_kw=round(gen_kw), run_nom=round(run_nom), gen_load_pct=round(gl, 1), gensets=self.running,
                             on_grid=on_grid, curtail=round(self.curtail, 1), pv_cb=self.pv_cb, ups=self.ups_closed, comm=comm,
                             silent=[int(s) for s in silent], pvcb_close=pvcb_close, warning=int(bool(plc.get(10433, 2))),
                             latch=int(bool(plc.get(10424, 4))), replay=False)
        self.snapshot["totals"] = self._totals_view()
        self._edges(comm)

    def _totals_view(self):
        T = self.totals
        return dict(hours=round(T["seconds"] / 3600, 2), pv_used_kwh=round(T["pv_used_kwh"]), pv_available_kwh=round(T["pv_available_kwh"]),
                    pv_curtailed_kwh=round(max(0.0, T["pv_available_kwh"] - T["pv_used_kwh"])), gen_kwh=round(T["gen_kwh"]),
                    fuel_l=round(T["fuel_l"]), comm_fails=T["comm_fails"], zero_setpoints=T["zero_setpoints"], warnings=T["warnings"],
                    ups_sheds=T["ups_sheds"], min_gen_load_pct=None if T["min_gen_load_pct"] is None else round(T["min_gen_load_pct"], 1),
                    max_gen_load_pct=round(T["max_gen_load_pct"], 1))

    def _totals(self, dt, avail, gen_kw, run_nom, gl, comm):
        T = self.totals
        T["seconds"] += dt
        T["pv_used_kwh"] += self.pv_actual * dt / 3600
        T["pv_available_kwh"] += avail * dt / 3600
        T["gen_kwh"] += gen_kw * dt / 3600
        if run_nom and self.running:
            for i in range(self.running):                     # load shared in proportion to nominal
                share = pm.GEN_NOMS_KW[i] / run_nom
                T["fuel_l"] += pm.fuel_l_per_min(gen_kw * share, pm.GEN_NOMS_KW[i]) * dt / 60
            T["min_gen_load_pct"] = gl if T["min_gen_load_pct"] is None else min(T["min_gen_load_pct"], gl)
            T["max_gen_load_pct"] = max(T["max_gen_load_pct"], gl)
        if self.warn_timer == 15:                             # the "AHI" alarm has its own 15 s delay
            T["warnings"] += 1
        if any(comm) and not self._prev_comm_any:
            T["comm_fails"] += 1
        self._prev_comm_any = bool(any(comm))
        sp = self.hist["setpoint"]
        if len(sp) > 1 and sp[-1] == 0 and sp[-2] > 0:
            T["zero_setpoints"] += 1

    _prev_comm_any = False

    def _power_management(self, run_nom, gen_kw):
        sc = self.sc
        reserve = sc.start_reserve_kw + sc.solar_coverage * self.pv_actual
        if run_nom - gen_kw < reserve and self.running < pm.GENSETS:
            self.start_timer += 60
            if self.start_timer >= sc.next_start_delay_s:
                self.running += 1; self.start_timer = 0; self.log(f"power management: genset {self.running} started")
        else:
            self.start_timer = 0
        if self.running > self.min_gensets and sum(pm.GEN_NOMS_KW[:self.running - 1]) - gen_kw >= sc.stop_reserve_kw + sc.solar_coverage * self.pv_actual \
                and gen_kw >= sc.min_run_power_kw:
            self.stop_timer += 60
            if self.stop_timer >= sc.next_stop_delay_s:
                self.log(f"power management: genset {self.running} stopped"); self.running -= 1; self.stop_timer = 0
        else:
            self.stop_timer = 0

    def _record(self, load, avail, setpoint, request, gen_kw, gl, comm_any, silent_any, recorded_setpoint):
        for k, v in (("t", self._clock()), ("load", round(load)), ("pv_available", round(avail)), ("pv_actual", round(self.pv_actual)),
                     ("setpoint", round(setpoint)), ("received", round(self.received_setpoint)), ("request", round(request)),
                     ("gen_kw", round(gen_kw)), ("gen_load_pct", round(gl, 1)), ("comm", int(comm_any)), ("silent", int(silent_any)),
                     ("recorded_setpoint", recorded_setpoint)):
            self.hist[k].append(v)

    def _edges(self, comm):
        plc = self.plc
        for name, key in (("PV logger group 1 comm fail", ("comm", 0)), ("PV logger group 2 comm fail", ("comm", 1)),
                          ("PV prod > PV req warning", (10433, 2)), ("PV CB1 ON command", (10425, 2)), ("UPS1 ON command", (10427, 2)),
                          ("UPS2 ON command", (10434, 2)), ("PV comm lost", (10436, 2))):
            v = comm[key[1]] if key[0] == "comm" else int(bool(plc.get(*key)))
            old = self._last_bits.get(name)
            if old is not None and old != v:
                self.log(f"{name}: {'on' if v else 'off'}")
            self._last_bits[name] = v
        sp = self.hist["setpoint"]
        if len(sp) > 1 and sp[-1] == 0 and sp[-2] > 0:
            self.log("PV setpoint forced to 0")

    # ---- replay of a recorded trace --------------------------------------------------------------------
    def _tick_replay(self, warm: bool):
        row = self.replay.row_at(self.t)
        if row is None:
            self.paused = True
            return
        g = self.replay.get
        pv = g(row, "pv_prod_kw", 0.0)
        gen_kw = g(row, "power_kw", 0.0)
        run_nom = g(row, "running_nominal_kw", 0.0)
        curtail = g(row, "pv_curtailment_pct", 0.0)
        mains = g(row, "mains_power_kw", 0.0)
        comm = [int(g(row, "ecu2_comm_fail", 0)), int(g(row, "ecu3_comm_fail", 0))]
        on_grid = bool(g(row, "mcb_status", 0))
        pv_cb = (int(g(row, "pvcb_closed", 1)), int(g(row, "pv2cb_closed", 1)))
        ups = (int(g(row, "ups1_feedback", 0)), int(g(row, "ups2_feedback", 0)))
        gens = sum(int(g(row, f"g{i}_running", 0)) for i in range(1, 6))
        inputs = plant_inputs(pv_kw=pv, max_avail_kw=pm.SAU_MAX_AVAILABLE_KW, curtailment_pct=curtail, gensets_kw=gen_kw,
                              running_nominal_kw=run_nom, mains_import_kw=(mains / 3,) * 3, on_grid=on_grid, gens_loaded=gens,
                              pv_cb=pv_cb, ups_fb=ups, comm_fail=(comm[0], comm[1]), pvcb_close=int(g(row, "pv_run_request", 1)),
                              pv_valid=not (comm[0] or comm[1]), neo_pv_sample_kw=None)
        for _ in range(int(round(1.0 / PLC_CYCLE_S))):
            self.plc.step(inputs, PLC_CYCLE_S)
        setpoint = self.plc.get(21267) or 0.0
        request = self.plc.get(21265) or 0.0
        self.pv_actual = pv
        self.received_setpoint = setpoint
        self.running = gens
        self.curtail = curtail
        recorded = self.replay.recorded_setpoint(self.t)
        if warm:
            return
        self.t += 1.0
        self.ticks += 1
        gl = 100 * gen_kw / run_nom if run_nom else 0.0
        load = gen_kw + pv + max(0.0, mains)
        self._record(load, pv, setpoint, request, gen_kw, gl, bool(comm[0] or comm[1]), False, recorded)
        self.snapshot = dict(clock=self._clock(), load=round(load), pv_available=None, pv_actual=round(pv), setpoint=round(setpoint, 1),
                             received=recorded, request=round(request, 1), gen_kw=round(gen_kw), run_nom=round(run_nom),
                             gen_load_pct=round(gl, 1), gensets=gens, on_grid=on_grid, curtail=round(curtail, 1), pv_cb=list(pv_cb),
                             ups=list(ups), comm=comm, silent=[0, 0], pvcb_close=1, warning=int(bool(self.plc.get(10433, 2))),
                             latch=int(bool(self.plc.get(10424, 4))), replay=True, replay_file=self.replay.name, totals=self._totals_view())
        self._edges(comm)

    # ---- the thread ---------------------------------------------------------------------------------
    def run(self, stop: threading.Event):
        while not stop.is_set():
            if self.paused:
                time.sleep(0.1)
                continue
            t0 = time.perf_counter()
            with self.lock:
                self._tick()
            wait = 1.0 / self.speed - (time.perf_counter() - t0)
            if wait > 0:
                time.sleep(wait)

    # ---- injections ------------------------------------------------------------------------------------
    def inject(self, what: str, value: float = 0, scheduled: bool = False):
        with self.lock:
            if what in ("comm1", "comm2", "comm12"):          # the SAU stops answering that session for N seconds
                n = value or 10
                if what != "comm2":
                    self.silent_until[0] = self.t + n
                if what != "comm1":
                    self.silent_until[1] = self.t + n
            elif what == "cloud":
                self.clouds.append((self.t, self.t + (value or 120), 0.5))
                self.clouds = [c for c in self.clouds if c[1] + 60 > self.t]
            elif what == "load":
                self.load_offset += value
            elif what == "off":
                self.off_until = self.t + (value or 60)
            elif what == "grid":
                self.grid_override = not self.on_grid()
            elif what == "fault_reset":
                self.fault_reset_pending = True
            elif what == "speed":
                self.speed = max(0.2, min(120.0, value))
            elif what == "pause":
                self.paused = not self.paused
            elif what == "auto_pm":
                self.auto_pm = not self.auto_pm
            elif what == "gensets":
                self.running = int(value); self.manual_gensets = int(value); self.sc.manual_gensets = int(value)
            elif what == "reset":
                self._init_state()
            elif what == "time":
                self.t = float(value) * 3600
                self.last_reply = [self.t, self.t]
            elif what == "replay14":
                self.t = 8 * 3600.0
                self.last_reply = [self.t, self.t]
                self.schedule = []
                for (m0, minutes, kind) in pm.INTERRUPTIONS_14_OCT:
                    if kind == "off":
                        self.schedule.append((m0 * 60.0, "off", minutes * 60.0))
                    else:                                   # a burst: the SAU silent for 10 s every 30 s, as the history reads
                        for k in range(minutes * 2):
                            self.schedule.append((m0 * 60.0 + k * 30.0, "comm12", 10.0))
                self.log("14 October: the recorded toggles and comm-fail bursts are scheduled from 08:00")
            elif what.startswith("param:"):
                key = what[6:]
                if key in self.params:
                    self.params[key] = float(value)
            if not scheduled:
                self.log(f"{what} {value:g}" if value else what)

    # ---- the sheet view ---------------------------------------------------------------------------------
    def sheet_view(self, index: int) -> dict:
        prog, plc = self.plc.prog, self.plc
        sheet = next((s for s in prog.sheets if s.index == index), prog.sheets[0])
        produced = {}
        blocks = []
        for b in sheet.blocks:
            ins = [p for p in b.inputs if p.connected or p.raw_const is not None]
            outs = [p for p in b.outputs if p.connected]
            bid = f"b{b.order}"
            for j, o in enumerate(outs):
                produced[o.key] = (bid, j)
            blocks.append(dict(id=bid, type=b.type_name, x=b.x, y=b.y,
                               inputs=[dict(name=prog.name_of(p) if p.connected else (p.name or "const"), value=_fmt(plc._get(p)),
                                            neg=p.negated, const=p.raw_const is not None, key=_key(p)) for p in ins],
                               outputs=[dict(name=prog.name_of(p), value=_fmt(plc.values.get(p.key)), key=_key(p)) for p in outs]))
        wires = []
        for b, blk in zip(sheet.blocks, blocks):
            for i, p in enumerate([q for q in b.inputs if q.connected or q.raw_const is not None]):
                if p.connected and p.key in produced:
                    src, j = produced[p.key]
                    wires.append(dict(frm=src, out=j, to=blk["id"], inp=i))
        return dict(index=sheet.index, name=sheet.name, blocks=blocks, wires=wires,
                    sheets=[dict(index=s.index, name=s.name) for s in prog.sheets])


class ReplaySource:
    """A trace file of the master (plantcollector trace, neo-01_<date>.csv) and optionally of the SAU array
    (pv-array_<date>.csv, for the recorded setpoint words). Rows are looked up by second of day."""

    def __init__(self, neo_csv: str, sau_csv: str | None = None):
        self.name = os.path.basename(neo_csv)
        self.rows = {}
        with open(neo_csv, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                self.rows[self._sec(r["ts"])] = r
        if not self.rows:
            raise ValueError(f"no rows in {neo_csv}")
        self.t0 = float(min(self.rows))
        self.sau = {}
        if sau_csv:
            with open(sau_csv, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    self.sau[self._sec(r["ts"])] = r

    @staticmethod
    def _sec(ts: str) -> int:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return d.hour * 3600 + d.minute * 60 + d.second

    def row_at(self, t: float):
        s = int(t) % 86400
        for k in (s, s - 1, s + 1, s - 2):
            if k in self.rows:
                return self.rows[k]
        return None

    @staticmethod
    def get(row, key, default):
        v = row.get(key, "")
        if v in ("", None):
            return default
        try:
            return float(v)
        except ValueError:
            return default

    def recorded_setpoint(self, t: float):
        """AP Set as the group 1 session writes it: 32-bit at 2100-2101, x0.1 kW."""
        s = int(t) % 86400
        r = self.sau.get(s) or self.sau.get(s - 1)
        if not r:
            return None
        try:
            return round(((int(float(r["set_2100"])) << 16) | int(float(r["set_2101"]))) * 0.1, 1)
        except (KeyError, ValueError):
            return None


def _fmt(v):
    if v is None:
        return "####"
    if isinstance(v, float):
        return f"{v:.1f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:.0f}"
    return str(v)


def _key(p):
    return f"{p.comobj}.{p.bit}" if p.is_binary else str(p.comobj)


# ---- web ------------------------------------------------------------------------------------------------
PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>PLC lab</title>
<style>
:root{--bg:#f4f5f3;--fg:#1d2229;--muted:#5c6570;--panel:#fff;--rule:#d6d9d4;--on:#1f5fbf;--warn:#a86d00;--bad:#b3261e}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.4 'Segoe UI',system-ui,sans-serif}
header{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;padding:10px 16px;background:var(--panel);border-bottom:1px solid var(--rule)}
header b{font-size:16px}
.num{display:inline-block;min-width:92px}.num span{display:block;color:var(--muted);font-size:11px}
.num strong{font-size:18px;font-variant-numeric:tabular-nums}
.bar{display:flex;flex-wrap:wrap;gap:6px;padding:8px 16px;border-bottom:1px solid var(--rule);align-items:center}
.bar label{font-size:12px;color:var(--muted)}
button,select,input{font:13px 'Segoe UI',system-ui,sans-serif;padding:4px 10px;border:1px solid var(--rule);background:var(--panel);border-radius:3px;cursor:pointer}
input{width:64px;cursor:text}
button.on{background:var(--on);color:#fff;border-color:var(--on)}
main{display:grid;grid-template-columns:1fr 280px;gap:0}
#chart{width:100%;height:170px;display:block;background:var(--panel);border-bottom:1px solid var(--rule)}
#sheet{overflow:auto;background:var(--panel);min-height:600px}
aside{border-left:1px solid var(--rule);padding:8px 12px;font-size:12px;overflow:auto;max-height:calc(100vh - 260px)}
aside div{padding:2px 0;border-bottom:1px dotted var(--rule);font-variant-numeric:tabular-nums}
.tabs{display:flex;flex-wrap:wrap;gap:4px;padding:6px 16px;background:var(--bg)}
.tabs button.on{background:var(--fg);border-color:var(--fg)}
svg text{font:11px 'Segoe UI',system-ui,sans-serif}
.blk{fill:#fbfbfa;stroke:#1d2229;stroke-width:1}
.pin1{fill:var(--on);font-weight:600}.pin0{fill:var(--muted)}.pinv{fill:#1d2229}
.wire{fill:none;stroke:#1f5fbf;stroke-width:1.2;opacity:.75}
.hdr{font-weight:600}
#replay{color:var(--warn);font-weight:600}
</style></head><body>
<header><b>PLC lab</b> <span id="replay"></span>
<span class="num"><span>time</span><strong id="clock">--:--:--</strong></span>
<span class="num"><span>load kW</span><strong id="load">-</strong></span>
<span class="num"><span>solar available</span><strong id="pv_available">-</strong></span>
<span class="num"><span>solar actual</span><strong id="pv_actual">-</strong></span>
<span class="num"><span>setpoint computed</span><strong id="setpoint">-</strong></span>
<span class="num"><span>setpoint at SAU</span><strong id="received">-</strong></span>
<span class="num"><span>gensets kW</span><strong id="gen_kw">-</strong></span>
<span class="num"><span>gen load %</span><strong id="gen_load_pct">-</strong></span>
<span class="num"><span>sets / nominal</span><strong id="gensets">-</strong></span>
<span class="num"><span>curtail %</span><strong id="curtail">-</strong></span>
<span class="num"><span>SAU silent / comm fail</span><strong id="comm">-</strong></span>
<span class="num"><span>PV CB / UPS</span><strong id="cbs">-</strong></span>
<span class="num"><span>warning</span><strong id="warning">-</strong></span>
</header>
<div class="bar">
<label>scenario</label><select id="scenario"></select>
<button onclick="ctl('reset')">reset, start from zero</button>
<label>program</label><select id="program"></select>
<button onclick="ctl('pause')" id="pause">pause</button>
<label>speed</label><button onclick="ctl('speed',1)">1x</button><button onclick="ctl('speed',10)">10x</button><button onclick="ctl('speed',60)">60x</button>
<span style="width:12px"></span>
<button onclick="ctl('comm1',10)">SAU silent g1 10 s</button>
<button onclick="ctl('comm2',10)">g2 10 s</button>
<button onclick="ctl('comm12',10)">both 10 s</button>
<button onclick="ctl('comm12',90)">both 90 s</button>
<button onclick="ctl('comm12',400)">both 400 s</button>
<button onclick="ctl('cloud',120)">cloud 50 % 2 min</button>
<button onclick="ctl('load',300)">load +300</button>
<button onclick="ctl('load',-300)">load -300</button>
<button onclick="ctl('off',60)">operator OFF 60 s</button>
<button onclick="ctl('grid')">grid on/off</button>
<button onclick="ctl('fault_reset')">fault reset</button>
<button onclick="ctl('gensets',2)">2 sets</button><button onclick="ctl('gensets',3)">3 sets</button><button onclick="ctl('gensets',4)">4 sets</button>
<button onclick="ctl('auto_pm')" id="auto_pm">power management AUTO</button>
<button onclick="ctl('time',8.8)">08:48</button><button onclick="ctl('time',12)">12:00</button>
<button onclick="ctl('replay14')">replay 14 October from 08:00</button>
</div>
<div class="bar" id="params"></div>
<div class="bar" id="note" style="color:var(--muted)"></div>
<div class="bar" id="totals"></div>
<canvas id="chart"></canvas>
<div class="tabs" id="tabs"></div>
<main><div id="sheet"></div><aside id="events"></aside></main>
<script>
let sheet=10, state=null;
const PARAMS=[['timeout_s','timeout s (archive 3)'],['retries','retries before comm fail (assumed)'],['inverter_tau_s','inverter response s (assumed)'],
 ['inverter_ramp_kw_s','SAU ramp limit kW/s, 0 = none'],['curtail_rate','regulator %/s per % (calibrated)'],['gen_p_min_pct','Gen P Min %'],
 ['load_noise','load noise x'],['cloud_edge_s','cloud edge s'],['writes_through_silence','writes land while silent 0/1 (hypothesis)']];
async function ctl(what,value){await fetch('/api/control',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({what,value:value||0})});}
async function poll(){
  try{const r=await fetch('/api/state?sheet='+sheet); state=await r.json(); render();}catch(e){}
  setTimeout(poll,300);
}
function render(){
  const s=state.plant; if(!s.clock) return;
  for(const k of ['clock','load','pv_available','pv_actual','setpoint','received','gen_kw','gen_load_pct','curtail']) document.getElementById(k).textContent=(s[k]===null||s[k]===undefined)?'-':s[k];
  document.getElementById('gensets').textContent=s.gensets+' / '+s.run_nom;
  document.getElementById('comm').textContent=s.silent.join('')+' / '+s.comm.join('');
  document.getElementById('cbs').textContent=s.pv_cb.join('')+' / '+s.ups.join('');
  document.getElementById('warning').textContent=s.warning?'PV prod > req':(s.on_grid?'on grid':'-');
  document.getElementById('pause').textContent=state.paused?'play':'pause';
  document.getElementById('auto_pm').className=state.auto_pm?'on':'';
  document.getElementById('replay').textContent=s.replay?('replay of '+s.replay_file+': the PLC runs on the recorded values; "setpoint at SAU" is the recorded one'):'';
  const sc=document.getElementById('scenario');
  if(sc.options.length==0){for(const x of state.scenarios){const o=document.createElement('option');o.value=x.name;o.textContent=x.name;sc.appendChild(o);} sc.onchange=()=>fetch('/api/scenario',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:sc.value})});}
  if(sc.value!=state.scenario) sc.value=state.scenario;
  const note=(state.scenarios.find(x=>x.name==state.scenario)||{}).note||''; document.getElementById('note').textContent=note;
  totals(s.totals);
  const sel=document.getElementById('program');
  if(sel.options.length==0){for(const p of state.programs){const o=document.createElement('option');o.value=p;o.textContent=p;sel.appendChild(o);} sel.onchange=()=>fetch('/api/program',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:sel.value})});}
  if(sel.value!=state.program) sel.value=state.program;
  params(state.params);
  chart(state.hist);
  tabs(state.sheet.sheets);
  drawSheet(state.sheet);
  document.getElementById('events').innerHTML=state.events.map(e=>'<div>'+e[0]+' '+e[1]+'</div>').join('');
}
function totals(T){
  if(!T) return; const d=document.getElementById('totals');
  const items=[['since start',T.hours+' h'],['solar used',T.pv_used_kwh+' kWh'],['solar available',T.pv_available_kwh+' kWh'],['solar thrown away',T.pv_curtailed_kwh+' kWh'],
    ['gensets',T.gen_kwh+' kWh'],['fuel (model)',T.fuel_l+' l'],['comm fails',T.comm_fails],['setpoint to 0',T.zero_setpoints],['overproduction warnings',T.warnings],
    ['UPS sheds',T.ups_sheds],['gen load min / max',(T.min_gen_load_pct===null?'-':T.min_gen_load_pct)+' / '+T.max_gen_load_pct+' %']];
  d.innerHTML=items.map(([k,v])=>'<span class="num"><span>'+k+'</span><strong style="font-size:15px">'+v+'</strong></span>').join('');
}
function params(p){
  const d=document.getElementById('params'); if(d.children.length) return;
  for(const [k,label] of PARAMS){const l=document.createElement('label');l.textContent=label+' ';const i=document.createElement('input');i.value=p[k];i.id='p_'+k;
    i.onchange=()=>ctl('param:'+k,parseFloat(i.value)); l.appendChild(i); d.appendChild(l);}
}
function chart(h){
  const c=document.getElementById('chart'); const W=c.width=c.clientWidth*devicePixelRatio, H=c.height=170*devicePixelRatio; const g=c.getContext('2d');
  g.clearRect(0,0,W,H); const n=h.load.length; if(n<2) return; const max=Math.max(2200,...h.load,...h.pv_available.filter(v=>v!==null))*1.05;
  const x=i=>i/(900-1)*W, y=v=>H-v/max*H;
  for(let i=0;i<n;i++){ if(h.silent[i]){g.fillStyle='rgba(168,109,0,.12)'; g.fillRect(x(i),0,Math.max(1,W/900),H);} if(h.comm[i]){g.fillStyle='rgba(168,109,0,.22)'; g.fillRect(x(i),0,Math.max(1,W/900),H);} }
  const line=(arr,col,w,dash)=>{g.beginPath();g.setLineDash(dash||[]);g.strokeStyle=col;g.lineWidth=w*devicePixelRatio;let pen=false;for(let i=0;i<n;i++){const v=arr[i];if(v===null||v===undefined){pen=false;continue;} pen?g.lineTo(x(i),y(v)):g.moveTo(x(i),y(v));pen=true;}g.stroke();g.setLineDash([]);};
  line(h.load,'#1d2229',1); line(h.pv_available,'#9aa3ad',1); line(h.pv_actual,'#2a8a3c',2); line(h.setpoint,'#1f5fbf',1.2); line(h.received,'#1f5fbf',1,[4,3]); line(h.recorded_setpoint,'#7b3fa0',1.5); line(h.gen_kw,'#b3261e',1.5);
  g.fillStyle='#5c6570'; g.font=(11*devicePixelRatio)+'px sans-serif';
  g.fillText('black load, grey solar available, green solar actual, blue setpoint computed (dashed: at the SAU), purple recorded setpoint, red gensets, amber = SAU silent / comm fail declared, last 15 min',8*devicePixelRatio,14*devicePixelRatio);
}
function tabs(list){
  const t=document.getElementById('tabs'); if(t.children.length) {for(const b of t.children) b.className=(+b.dataset.i==sheet)?'on':''; return;}
  for(const s of list){const b=document.createElement('button');b.dataset.i=s.index;b.textContent=s.index+' '+s.name;b.onclick=()=>{sheet=s.index;};t.appendChild(b);}
}
function drawSheet(sh){
  const CX=34, RY=16, BW=170, M=240, MY=20; let maxx=0,maxy=0; const pos={};
  for(const b of sh.blocks){ const rows=Math.max(b.inputs.length,b.outputs.length,1); const h=RY*(rows+1)+6; const px=M+(b.x-4)*CX, py=MY+b.y*RY; pos[b.id]={x:px,y:py,h:h,b:b}; maxx=Math.max(maxx,px+BW+260); maxy=Math.max(maxy,py+h+10); }
  let out=`<svg width="${maxx}" height="${maxy}" xmlns="http://www.w3.org/2000/svg">`;
  for(const w of sh.wires){ const a=pos[w.frm], b=pos[w.to]; if(!a||!b) continue; const x1=a.x+BW, y1=a.y+RY*(w.out+1)+10, x2=b.x, y2=b.y+RY*(w.inp+1)+10; const dx=Math.max(30,(x2-x1)/2);
    out+=`<path class="wire" d="M${x1},${y1} C${x1+dx},${y1} ${x2-dx},${y2} ${x2},${y2}"/>`; }
  for(const id in pos){ const p=pos[id], b=p.b;
    out+=`<rect class="blk" x="${p.x}" y="${p.y}" width="${BW}" height="${p.h}" rx="2"/><text class="hdr" x="${p.x+6}" y="${p.y+12}">${esc(b.type)}</text>`;
    b.inputs.forEach((i,k)=>{ const y=p.y+RY*(k+1)+14; const cls=i.value==='1'?'pin1':(i.value==='0'?'pin0':'pinv');
      out+=`<text class="${cls}" x="${p.x+6}" y="${y}">${esc(i.value)}</text><text x="${p.x-4}" y="${y}" text-anchor="end" fill="#5c6570">${i.neg?'NOT ':''}${esc(i.name)}</text>`; });
    b.outputs.forEach((o,k)=>{ const y=p.y+RY*(k+1)+14; const cls=o.value==='1'?'pin1':(o.value==='0'?'pin0':'pinv');
      out+=`<text class="${cls}" x="${p.x+BW-6}" y="${y}" text-anchor="end">${esc(o.value)}</text><text x="${p.x+BW+4}" y="${y}" fill="#5c6570">${esc(o.name)}</text>`; });
  }
  out+='</svg>'; document.getElementById('sheet').innerHTML=out;
}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
poll();
</script></body></html>"""


def create_app(plant: LivePlant):
    app = FastAPI(title="PLC lab")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return PAGE

    @app.get("/api/state")
    def state(sheet: int = 10):
        with plant.lock:
            return JSONResponse(dict(plant=plant.snapshot, paused=plant.paused, speed=plant.speed, program=plant.program_name,
                                     programs=list(PROGRAMS), auto_pm=plant.auto_pm, params=plant.params,
                                     scenario=plant.scenario, scenarios=[dict(name=x["name"], note=x["note"]) for x in SCENARIOS],
                                     hist={k: list(v) for k, v in plant.hist.items()}, events=list(plant.events),
                                     sheet=plant.sheet_view(sheet)))

    @app.post("/api/control")
    async def control(req: Request):
        body = await req.json()
        plant.inject(str(body.get("what", "")), float(body.get("value") or 0))
        return {"ok": True}

    @app.post("/api/scenario")
    async def scenario(req: Request):
        body = await req.json()
        name = body.get("name")
        if name in SCENARIO_BY_NAME:
            plant.apply_scenario(name)
        return {"ok": name in SCENARIO_BY_NAME}

    @app.post("/api/program")
    async def program(req: Request):
        body = await req.json()
        name = body.get("name")
        if name in PROGRAMS:
            plant.set_program(name)
        return {"ok": name in PROGRAMS}

    return app


def main() -> int:
    ap = argparse.ArgumentParser(description="the plant and its PLC program, live in the browser")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--hour", type=float, default=10.0, help="start time of day")
    ap.add_argument("--replay", help="a trace file of the master (data/trace/neo-01_<date>.csv): the PLC runs on the recorded values")
    ap.add_argument("--replay-sau", help="the SAU array trace of the same day, for the recorded setpoint")
    a = ap.parse_args()
    import uvicorn
    replay = ReplaySource(a.replay, a.replay_sau) if a.replay else None
    plant = LivePlant(start_hour=a.hour, replay=replay)
    plant.speed = a.speed
    stop = threading.Event()
    threading.Thread(target=plant.run, args=(stop,), daemon=True).start()
    print(f"PLC lab: http://localhost:{a.port}   (Ctrl+C stops)")
    try:
        uvicorn.run(create_app(plant), host="127.0.0.1", port=a.port, log_level="warning")
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
