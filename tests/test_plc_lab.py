"""The live plant behind the PLC lab: ticks, injections, program switch, sheet view."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import plc_lab  # noqa: E402


def run(p, seconds):
    for _ in range(seconds):
        p._tick(1.0)


def test_today_zeroes_on_a_comm_fail_and_the_timed_variant_holds():
    p = plc_lab.LivePlant(start_hour=10.0)
    run(p, 60)
    before = p.snapshot["setpoint"]
    assert before > 300 and p.snapshot["gensets"] == 3 and not p.snapshot["on_grid"]
    p.inject("comm12", 10)
    run(p, 3)
    assert p.snapshot["setpoint"] == 0 and p.snapshot["comm"] == [1, 1]
    run(p, 20)
    assert p.snapshot["comm"] == [0, 0] and 0 < p.snapshot["setpoint"] < before     # climbing back from 0
    assert any("PV setpoint forced to 0" in e[1] for e in p.events)
    p.set_program("change-01 v3 (hold 60 s, then 0)")
    run(p, 120)
    held = p.snapshot["setpoint"]
    p.inject("comm12", 30)
    run(p, 10)
    s10 = p.snapshot["setpoint"]
    run(p, 15)
    assert abs(s10 - held) < 20 and p.snapshot["setpoint"] == s10                   # held through a 25 s fail (the ramp finishes its last step, then freezes)
    p.inject("comm12", 100)
    run(p, 70)
    assert p.snapshot["setpoint"] == 0                                              # longer than 60 s: 0 as today


def test_sheet_view_lists_blocks_pins_values_and_wires():
    p = plc_lab.LivePlant(start_hour=12.0)
    run(p, 5)
    v = p.sheet_view(10)
    assert v["name"].startswith("PV req step") and len(v["blocks"]) == 16 and len(v["wires"]) > 10
    ramp = next(b for b in v["blocks"] if b["type"] == "Ramp")
    assert ramp["inputs"][1]["value"] == "25" and ramp["inputs"][2]["value"] == "1500"   # the ramp constants, scaled
    assert ramp["outputs"][0]["name"] == "PV req out ramp"
    assert all(len(s) == 2 for s in [(b["x"], b["y"]) for b in v["blocks"]])
    assert [s["index"] for s in v["sheets"]] == list(range(1, 12))


def test_injections_change_the_plant():
    p = plc_lab.LivePlant(start_hour=11.0)
    run(p, 30)
    load = p.snapshot["load"]
    p.inject("load", 300)
    run(p, 2)
    assert p.snapshot["load"] == load + 300
    p.inject("off", 30)
    run(p, 5)
    assert p.snapshot["pvcb_close"] == 0 and p.snapshot["pv_actual"] == 0
    p.inject("grid")
    run(p, 2)
    assert p.snapshot["on_grid"] and p.snapshot["gensets"] == 0
    p.inject("zero_hypothesis")
    assert p.zero_reaches_sau_in_fail is False
