"""Change 01 (PV logger communication tolerance): the modified sheets build, behave like the plant's program
while the link is healthy, and ride through short comm fails."""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from neo_plc import Plc, load_program, plant_inputs  # noqa: E402

OUT = os.path.join(ROOT, "docs", "changes", "change-01_comm_fail_tolerance")
V2 = os.path.join(OUT, "program_v2")
SP = {65514: 90.0}
BASE = dict(pv_kw=1000, curtailment_pct=31.0, gensets_kw=790, running_nominal_kw=3150)
KEYS = [21265, 21269, 21267, 21263, (10435, 6), (10435, 7), (10425, 2), (10426, 2), (10433, 2), 21249]


def settle(plc, inputs, seconds, dt=0.1):
    for _ in range(int(seconds / dt)):
        plc.step(inputs, dt)
    return plc


def snap(plc):
    return [plc.get(*k) if isinstance(k, tuple) else plc.get(k) for k in KEYS]


def test_change01_builds_and_reads_back():
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "neo_plc_change01.py")], check=True, capture_output=True)
    for f in ("sheet06_Validation_v2.xml", "sheet10_PV_req_step_protection_v2.xml", "sheet10_PV_req_step_protection_v2_const.xml",
              "sheet03_PV_breaker_control_v2.xml", "README.md", "program_v2.md"):
        assert os.path.exists(os.path.join(OUT, f))
    raw = open(os.path.join(OUT, "sheet06_Validation_v2.xml"), "rb").read()
    assert raw[:2] == b"\xff\xfe" and b"\r\n" in raw.decode("utf-16").encode("utf-8")   # UTF-16 with BOM, CRLF
    prog = load_program(V2)
    assert len(prog.blocks) == 88 + 5
    assert prog.setpoints[65514] == "PV comm fail hold"


def test_identical_while_the_link_is_healthy():
    cases = [BASE, dict(pv_kw=1000, curtailment_pct=60.0, gensets_kw=790, running_nominal_kw=3150),
             dict(pv_kw=1445, curtailment_pct=0, gensets_kw=0, running_nominal_kw=0, gens_loaded=0, on_grid=True, mains_import_kw=(-399, -370, -341)),
             dict(pv_kw=1041, curtailment_pct=29.6, gensets_kw=1829, running_nominal_kw=3150, neo_pv_sample_kw=0, pvcb_close=0)]
    a, b = Plc(load_program()), Plc(load_program(V2), SP)
    for c in cases:
        settle(a, plant_inputs(**c), 120)
        settle(b, plant_inputs(**c), 120)
        assert snap(a) == snap(b)


def test_short_fail_is_ridden_through_long_fail_is_safe():
    old, new = Plc(load_program()), Plc(load_program(V2), SP)
    for p in (old, new):
        settle(p, plant_inputs(**BASE), 120)
    fail = plant_inputs(**BASE, comm_fail=(1, 0), pv_valid=False)
    settle(old, fail, 1)
    settle(new, fail, 1)
    assert old.get(21267) == 0                         # today: setpoint 0 after one lost second
    assert abs(new.get(21267) - 1091.2) < 0.01         # change 01: unchanged
    assert new.get(21281) == 1000                      # SAU value held
    settle(new, fail, 80)
    assert abs(new.get(21267) - 1091.2) < 0.01         # still held at 81 s
    settle(new, fail, 15)
    assert new.get(21267) == 0                         # request 0 after the 90 s hold
    assert new.get(10436, 4) == 0 and new.get(10425, 2) == 1
    settle(new, fail, 210)
    assert new.get(10436, 4) == 1                      # PV comm lost after 300 s
    assert new.get(10425, 2) == 0 and new.get(10426, 2) == 0   # sheet 3 v2: PV breakers opened
    settle(new, plant_inputs(**BASE, fault_reset=1), 1)
    settle(new, plant_inputs(**BASE), 60)
    assert new.get(10425, 2) == 1 and abs(new.get(21267) - 1091.2) < 1
