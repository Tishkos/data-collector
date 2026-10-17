"""Change 01 v4 (hold + guard): the sheets build within the PLC's free resources, the program is identical to
today's while the link is healthy, and in closed loop it rides through pauses, survives the AP race and keeps the
gensets out of reverse power on load drops a plain hold does not survive."""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from neo_plc import Plc, load_program, plant_inputs  # noqa: E402
import change01_sim as sim  # noqa: E402

OUT = os.path.join(ROOT, "docs", "changes", "change-01_comm_fail_tolerance")
V4 = os.path.join(OUT, "program_v4")
KEYS = [21265, 21269, 21267, 21263, (10435, 6), (10435, 7), (10425, 2), (10426, 2), (10433, 2), 21249]


def settle(plc, inputs, seconds, dt=0.1):
    for _ in range(int(round(seconds / dt))):
        plc.step(inputs, dt)
    return plc


def snap(plc):
    return [plc.get(*k) if isinstance(k, tuple) else plc.get(k) for k in KEYS]


def test_v4_builds_within_free_resources():
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "neo_plc_change01.py")], check=True, capture_output=True)
    for f in ("sheet10_PV_req_step_protection_v4_guard.xml", "sheet06_Validation_v4.xml", "program_v4.md"):
        assert os.path.exists(os.path.join(OUT, f))
    raw = open(os.path.join(OUT, "sheet10_PV_req_step_protection_v4_guard.xml"), "rb").read()
    assert raw[:2] == b"\xff\xfe"
    today, v4 = load_program(), load_program(V4)
    assert len(v4.blocks) == len(today.blocks) + 12
    assert sum(b.type_id == 5 for b in v4.blocks) == 7                  # no Analog Switch added (none left)
    outs = [o.key for b in v4.blocks for o in b.outputs if o.connected]
    assert len(outs) == len(set(outs))                                   # every output on its own object
    old = {o.key for b in today.blocks for o in b.outputs if o.connected}
    new_bits = {k for k in set(outs) - old if isinstance(k, tuple)}
    assert new_bits <= {(10436, 2), (10436, 3), (10436, 4), (10436, 5), (10436, 6), (10436, 7), (10430, 0)}


def test_floor_follows_the_loaded_sets():
    plc = Plc(load_program(V4))
    for sets, floor in (((0, 3, 4), 150 + 150 + 172), ((3, 4), 322), ((2,), 105), ((0, 1, 2, 3, 4), 727), ((), 0)):
        inp = plant_inputs(pv_kw=1000, curtailment_pct=31.0, gensets_kw=790, running_nominal_kw=3150)
        for i in range(5):
            inp[(10196, i)] = int(i in sets)
        settle(plc, inp, 0.3)
        assert plc.get(21291) == floor


def test_identical_to_today_while_the_link_is_healthy():
    cases = [dict(pv_kw=1000, curtailment_pct=31.0, gensets_kw=790, running_nominal_kw=3150),
             dict(pv_kw=1000, curtailment_pct=60.0, gensets_kw=790, running_nominal_kw=3150),
             dict(pv_kw=1000, curtailment_pct=31.0, gensets_kw=200, running_nominal_kw=3150),   # gensets low, link fine
             dict(pv_kw=1445, curtailment_pct=0, gensets_kw=0, running_nominal_kw=0, gens_loaded=0, on_grid=True,
                  mains_import_kw=(-399, -370, -341)),
             dict(pv_kw=1000, curtailment_pct=0.0, gensets_kw=790, running_nominal_kw=3150)]
    a, b = Plc(load_program()), Plc(load_program(V4))
    for c in cases:
        settle(a, plant_inputs(**c), 120)
        settle(b, plant_inputs(**c), 120)
        assert snap(a) == snap(b)
        assert b.get(10436, 6) == 0                                       # the guard sleeps


def test_closed_loop_pause_is_ridden_through():
    sc = sim.scenarios()[0][1]
    today, v4 = sim.run("today", sc), sim.run("v4", sc)
    assert today.max_step_kw > 1000                                       # today: the 2.5 s pause throws ~1.1 MW on the gensets
    assert v4.max_step_kw < 50 and not v4.tripped


def test_closed_loop_ap_race():
    sc = sim.scenarios()[1][1]
    v3, v4 = sim.run("v3", sc), sim.run("v4", sc)
    assert v3.max_step_kw > 900                                           # v3 holds the corrupted request
    assert v4.max_step_kw < 200                                           # v4 holds what was sent: one ramp cycle at most


def test_closed_loop_load_drop_one_session_lost():
    sc = sim.load_drop(1300, sets=(0, 3, 4))
    v3, v4 = sim.run("v3", sc), sim.run("v4", sc)
    assert v3.tripped                                                     # plain hold: reverse power trip
    assert not v4.tripped and v4.rp_seconds < 4                           # the dip is physics; the guard ends it inside the 5 s delay
    sc = sim.load_drop(900, sets=(3, 4))
    assert sim.run("v3", sc).tripped and not sim.run("v4", sc).tripped


def test_guard_sleeps_when_the_gensets_are_fine():
    for sc in (sim.scenarios()[2][1], sim.load_drop(200, sets=(0, 3, 4))):
        r = sim.run("v4", sc)
        held = [row[4] for row in r.trace if 14 <= row[0] <= 49]
        assert max(held) - min(held) < 2                                  # nothing lowered
    grid = sim.scenarios()[6][1]
    assert not sim.run("v4", grid).tripped


def test_long_fail_still_goes_to_zero():
    r = sim.run("v4", sim.scenarios()[4][1])
    assert all(row[4] == 0 for row in r.trace if 75 <= row[0] <= 80)      # 60 s after the fail is declared: 0, as today


def test_stuck_invalid_value_holds_instead_of_capping():
    sc = sim.scenarios()[5][1]
    today, v4 = sim.run("today", sc), sim.run("v4", sc)
    assert today.max_step_kw > 1000 and v4.max_step_kw < 50
