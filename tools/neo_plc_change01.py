"""
Change 01 - PV logger communication tolerance. Builds the modified PLC sheets for InteliConfig from the
originals of 2026-10-14, in the same "PLC sheet export" format, so on the plant PC it is: create the user
setpoint, import the sheets into a copy of the archive, compare, save as a new archive.

  python tools/neo_plc_change01.py          writes docs/changes/change-01_comm_fail_tolerance/

What it changes (see README.md in the output folder):
  sheet 6  Validation            the SAU values are held at their last good value while the SAU session is lost
  sheet 10 PV req step protection a lost session only forces the request to 0 after "PV comm fail hold" (90 s);
                                 a session lost for 5 minutes raises "PV comm lost"
  sheet 3  PV Breaker controll   (optional, stage 2) "PV comm lost" opens the two PV breakers
Everything else is untouched; the program behaves exactly as today while the link is healthy.
"""

from __future__ import annotations

import copy
import os
import shutil
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from neo_plc import _text, describe, load_program  # noqa: E402

SRC = os.path.join(ROOT, "docs", "site", "neo_plc_2026-10-14")
OUT = os.path.join(ROOT, "docs", "changes", "change-01_comm_fail_tolerance")

# --- new objects (free in the program of 14 Oct: PLC-BOUT 13 bits 2..7, PLC-AOUT 39.., user setpoint 65514)
SP_HOLD = 65514                 # new user setpoint "PV comm fail hold" (s, 1 decimal) - created in InteliConfig first
B_SAU_FAIL = (10436, 2)         # SAU comm fail (either session)
B_SAU_FAIL_DEL = (10436, 3)     # SAU comm fail held longer than the setpoint
B_COMM_LOST = (10436, 4)        # SAU comm fail held longer than 5 minutes
A_AP_RAW = 21286                # SAU active power as validated (before the hold)
A_MAX_RAW = 21287               # SAU maximum available as validated (before the hold)
ECU2, ECU3 = (11900, 1), (11900, 2)
HOLD_DEFAULT_S = 90.0
LOST_S = 300.0


def read(name: str) -> ET.Element:
    return ET.fromstring(_text(os.path.join(SRC, name)))


def write(root: ET.Element, path: str) -> None:
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    text = '<?xml version="1.0" encoding="utf-16"?>\r\n' + body.replace("\n", "\r\n") + "\r\n"
    with open(path, "wb") as f:
        f.write(text.encode("utf-16"))      # BOM + UTF-16 LE, as InteliConfig writes it


# --- small builders in the export's own vocabulary
def pin_in(name="", comobj=-1, bit=0, *, negated=False, const=None, ctype=-1, cat="Unknown", hidden=False) -> ET.Element:
    e = ET.Element("Input")
    e.set("Name", name)
    e.set("BitIndex", str(bit))
    if negated:
        e.set("IsNegated", "true")
    e.set("ComObject", str(comobj))
    if const is not None:
        e.set("IsConst", "true")
    e.set("ValueOfConstant", str(const if const is not None else 0))
    e.set("ConnectedFunctionType", str(ctype))
    e.set("IsHidden", "true" if hidden else "false")
    e.set("ComObjCategory", cat)
    return e


def pin_out(name, comobj, bit=0, *, cat, res="", dim="", negated=False) -> ET.Element:
    e = ET.Element("Output")
    e.set("Name", name)
    e.set("BitIndex", str(bit))
    if negated:
        e.set("IsNegated", "true")
    e.set("ValueIndex", "0")
    e.set("ComObject", str(comobj))
    e.set("ConnectedFunctionType", "0")
    e.set("ComObjCategory", cat)
    e.set("ResolutionIndex", res)
    e.set("Dimension", dim)
    return e


def block(type_id: int, x: int, y: int, inputs: list, outputs: list, props: list) -> ET.Element:
    b = ET.Element("FunctionBlockExport")
    b.set("Identification", str(type_id))
    pos = ET.SubElement(b, "Position")
    pos.set("X", str(x))
    pos.set("Y", str(y))
    ins = ET.SubElement(b, "Inputs")
    for i in inputs:
        ins.append(i)
    outs = ET.SubElement(b, "Outputs")
    for o in outputs:
        outs.append(o)
    pr = ET.SubElement(b, "Properties")
    for ident, value, vtype in props:
        p = ET.SubElement(pr, "Property")
        p.set("Identification", str(ident))
        p.set("Value", value)
        p.set("ValueType", vtype)
        p.set("ValueIdentification", "-1")
    return b


def bin_in(name, key, negated=False, ctype=0) -> ET.Element:
    return pin_in(name, key[0], key[1], negated=negated, ctype=ctype, cat="BinValue")


def ana_in(name, comobj, ctype=0) -> ET.Element:
    return pin_in(name, comobj, 0, ctype=ctype, cat="AnaValue")


def setpoint_in(name, comobj) -> ET.Element:
    return pin_in(name, comobj, 0, ctype=2, cat="AllSetpoint")


def const_in(value) -> ET.Element:
    return pin_in(const=value)


def hidden_in() -> ET.Element:
    return pin_in(hidden=True)


def or_block(x, y, name, key, *ins) -> ET.Element:
    inputs = list(ins) + [hidden_in()] * (8 - len(ins))
    return block(1, x, y, inputs, [pin_out(name, key[0], key[1], cat="BinValue")], [(0, "1", "UInt")])


def delay_block(x, y, name, key, src: ET.Element, up: ET.Element) -> ET.Element:
    return block(33, x, y, [src, up, const_in(0), pin_in()],
                 [pin_out(name, key[0], key[1], cat="BinValue")], [(6, "False", "Boolean"), (7, "0", "UInt")])


def ana_switch(x, y, name, comobj, in1, in2, sw, dim="kW") -> ET.Element:
    return block(5, x, y, [in1, in2, sw], [pin_out(name, comobj, cat="AnaValue", res="1", dim=dim)], [])


def sheet_io(root, kind, comobj, bit, index, type_, external=None) -> None:
    sec = root.find("PlcProgramExport").find("PlcSheetInputs" if kind == "in" else "PlcSheetOutputs")
    e = ET.SubElement(sec, "SheetInput" if kind == "in" else "SheetOutput")
    e.set("BitIndex", str(bit))
    e.set("Index", str(index))
    e.set("Type", type_)
    if kind == "in":
        e.set("ComObjectNumber", str(comobj))
    else:
        e.set("ComObject", str(comobj))
        ext = ET.SubElement(e, "ExternalOutput")
        for i in external or []:
            ET.SubElement(ext, "int").text = str(i)


def comobj_entry(root, name, number, *, dim="", res="0", bit="", setpoint=False) -> None:
    sec = root.find("CommunicationObjectsExport")
    e = ET.SubElement(sec, "ComObjectExport")
    e.set("Name", name)
    e.set("Dimension", dim)
    e.set("IsUserSetpoint", "true" if setpoint else "false")
    e.set("Resolution", res)
    e.set("ComObjectNumber", str(number))
    e.set("Type", "Setpoint" if setpoint else "Value")
    e.set("BitIndex", str(bit))


def blocks_of(root) -> ET.Element:
    return root.find("PlcProgramExport").find("FunctionBlocks")


# --------------------------------------------------------------------------------------------- the change
def sheet06() -> ET.Element:
    root = read("sheet06_Validation.xml")
    fb = blocks_of(root)
    v_ap, v_max = list(fb)
    # the two validators keep their invalid flags; their values go to new objects
    out_ap = v_ap.find("Outputs")[0]
    out_ap.set("Name", "AP from SAU")
    out_ap.set("ComObject", str(A_AP_RAW))
    out_max = v_max.find("Outputs")[0]
    out_max.set("Name", "Max AP available from SAU")
    out_max.set("ComObject", str(A_MAX_RAW))
    # SAU comm fail = either session lost (the PLC computes it here, sheet 10 reuses the bit)
    fb.append(or_block(6, 20, "SAU comm fail", B_SAU_FAIL, bin_in("ECU 2 Comm Fail", ECU2, ctype=1), bin_in("ECU 3 Comm Fail", ECU3, ctype=1)))
    # hold: while a session is lost the downstream values keep their last good value (switch feeds back its own output)
    fb.append(ana_switch(19, 1, "AP(active Power) validate", 21281, ana_in("AP from SAU", A_AP_RAW),
                         ana_in("AP(active Power) validate", 21281), bin_in("SAU comm fail", B_SAU_FAIL)))
    fb.append(ana_switch(19, 11, "Max Active Power Available valid", 21282, ana_in("Max AP available from SAU", A_MAX_RAW),
                         ana_in("Max Active Power Available valid", 21282), bin_in("SAU comm fail", B_SAU_FAIL)))
    sheet_io(root, "in", ECU2[0], ECU2[1], 8, "Binary")
    sheet_io(root, "in", ECU3[0], ECU3[1], 9, "Binary")
    sheet_io(root, "out", B_SAU_FAIL[0], B_SAU_FAIL[1], 10, "Binary")
    comobj_entry(root, "Log Bout 13", ECU2[0], bit=ECU2[1])
    comobj_entry(root, "Log Bout 13", ECU3[0], bit=ECU3[1])
    root.find("Sheet").set("Height", "600")
    return root


def sheet10(with_setpoint: bool) -> ET.Element:
    root = read("sheet10_PV_req_step_protection.xml")
    fb = blocks_of(root)
    blocks = list(fb)
    comm_fail = blocks[5]                      # the "PV comm fail" OR block (ECU 2 | ECU 3 | negative request)
    assert comm_fail.get("Identification") == "1" and comm_fail.find("Outputs")[0].get("ComObject") == "10432"
    ins = comm_fail.find("Inputs")
    ins[0].set("Name", "SAU comm fail del")
    ins[0].set("ComObject", str(B_SAU_FAIL_DEL[0]))
    ins[0].set("BitIndex", str(B_SAU_FAIL_DEL[1]))
    ins[0].set("ConnectedFunctionType", "0")
    ins[1].set("Name", "")
    ins[1].set("ComObject", "-1")
    ins[1].set("BitIndex", "0")
    ins[1].set("ConnectedFunctionType", "-1")
    ins[1].set("IsHidden", "true")
    ins[1].set("ComObjCategory", "Unknown")
    # the two delays, placed in the first column so they execute before the OR block
    up = setpoint_in("PV comm fail hold", SP_HOLD) if with_setpoint else const_in(int(HOLD_DEFAULT_S * 10))
    fb.insert(4, delay_block(4, 50, "SAU comm fail del", B_SAU_FAIL_DEL, bin_in("SAU comm fail", B_SAU_FAIL), up))
    fb.insert(5, delay_block(4, 56, "PV comm lost", B_COMM_LOST, bin_in("SAU comm fail", B_SAU_FAIL), const_in(int(LOST_S * 10))))
    # sheet terminals: the ECU bits are no longer read here; the new bit and the setpoint are
    sec = root.find("PlcProgramExport").find("PlcSheetInputs")
    for e in list(sec):
        if e.get("ComObjectNumber") == "11900":
            sec.remove(e)
    sheet_io(root, "in", B_SAU_FAIL[0], B_SAU_FAIL[1], 6, "Binary")
    if with_setpoint:
        sheet_io(root, "in", SP_HOLD, 0, 7, "Analog")
    sheet_io(root, "out", B_COMM_LOST[0], B_COMM_LOST[1], 19, "Binary")
    sec = root.find("CommunicationObjectsExport")
    for e in list(sec):
        if e.get("ComObjectNumber") == "11900":
            sec.remove(e)
    comobj_entry(root, "PLC-BOUT 13", B_SAU_FAIL[0], bit=B_SAU_FAIL[1])
    if with_setpoint:
        comobj_entry(root, "PV comm fail hold", SP_HOLD, dim="s", res="1", setpoint=True)
    root.find("Sheet").set("Height", "1260")
    return root


def sheet03() -> ET.Element:
    root = read("sheet03_PV_breaker_control.xml")
    fb = blocks_of(root)
    cb1, cb2 = list(fb)[0], list(fb)[1]
    for b in (cb1, cb2):
        ins = b.find("Inputs")
        slot = next(i for i in ins if i.get("IsHidden") == "true")
        new = bin_in("PV comm lost", B_COMM_LOST, negated=True)
        ins.insert(list(ins).index(slot), new)
        ins.remove(slot)
    sheet_io(root, "in", B_COMM_LOST[0], B_COMM_LOST[1], 28, "Binary")
    comobj_entry(root, "PLC-BOUT 13", B_COMM_LOST[0], bit=B_COMM_LOST[1])
    return root


README = """# Change 01 - PV logger communication tolerance

Built from the sheet exports of 2026-10-14 by `tools/neo_plc_change01.py`. Same export format, so the way
in is: InteliConfig on a copy of the archive -> PLC editor -> import sheet -> compare -> save as a new archive.
Nothing here writes to the controller; the plant loads the new archive itself, at low load, with the
14 Oct archive at hand as the way back.

## Why

One lost Modbus reply on either PV-logger session raises "ECU 2/3 Comm Fail"; sheet 10 switches the PV request
to 0 the same cycle and sheet 6 replaces the SAU values by 0. The 0 never reaches the SAU (the link is down) -
it arrives when the link is back and takes the PV to zero, ~5 minutes to climb back, gensets slammed, UPS shed.
5-33 such events a day in the history.

## What changes

| Sheet | Before | After |
|---|---|---|
| 6 Validation | invalid SAU values -> 0 | values held at the last good value while a session is lost (`SAU comm fail` = ECU 2 OR ECU 3, two analog switches feeding back their own output) |
| 10 PV req step protection | `PV comm fail` = ECU 2 OR ECU 3 OR negative request -> request 0 at once | `SAU comm fail del` = SAU comm fail held longer than **PV comm fail hold** (new setpoint, 90.0 s); `PV comm fail` = SAU comm fail del OR negative request. `PV comm lost` = SAU comm fail held 300 s, for an alarm and for sheet 3 |
| 3 PV Breaker controll (optional, stage 2) | - | PV CB1/CB2 command AND NOT `PV comm lost`: five minutes without the logger opens the PV breakers deliberately |

New objects: PLC-BOUT 13 bits 2, 3, 4 (free in the 14 Oct program), PLC-AOUT 21286 / 21287, user setpoint 65514.
With a healthy link the program behaves exactly as today (checked block by block with `tools/neo_plc.py`).

## Files

- `sheet06_Validation_v2.xml`
- `sheet10_PV_req_step_protection_v2.xml` - uses the new setpoint (create it first, step 1)
- `sheet10_PV_req_step_protection_v2_const.xml` - same with a fixed 90 s, if the setpoint cannot be created
- `sheet03_PV_breaker_control_v2.xml` - optional, only after the plant agrees to automatic breaker opening
- `program_v2/` - the whole program with the three sheets replaced, as the model runs it
- `program_v2.md` - the listing of the modified program

## Steps on the plant PC (copy of the archive, never the live controller)

1. Setpoints -> User setpoints -> add **PV comm fail hold**: unit `s`, 1 decimal, range 0.0-600.0, default **90.0**,
   group "PV setpoints". Note the number InteliConfig gives it; if it is not 65514, open
   `sheet10_PV_req_step_protection_v2.xml` in a text editor and replace `65514` (3 places) before importing,
   or use the `_const` file.
2. PLC editor -> sheet "Validation" -> import `sheet06_Validation_v2.xml`. Check: two validators, one OR, two
   analog switches; `AP(active Power) validate` and `Max Active Power Available valid` keep their numbers.
3. Sheet "PV req step protection" -> import the sheet 10 file. Check: two new Delay blocks in the first
   column, the `PV comm fail` OR now reads `SAU comm fail del` and `PV negative request`, the ramp and the
   SAU write are untouched. If the editor renumbered PLC-BOUT 13 bits, follow its numbering.
4. Alarms: add a warning on `PV comm lost` ("PV logger lost 5 min"); give the existing ECU comm-fail warning
   a delay equal to the hold if the firmware offers one.
5. Compile, compare with the 14 Oct archive (only sheets 6 and 10 differ), save as a new archive with the date.
6. Controller side (settings, no PLC): PV logger sessions - response timeout 3,000 -> 5,000 ms, and one session
   instead of two if the register sets allow it. Check on the SAU whether it has its own "no command ->
   power limit" fail-safe; it must be longer than the hold and shorter than 300 s.

## Test, with the plant's approval, at low load, the collector logging

- SAU Ethernet cable out for 30 s: PV must not move; one warning only. Three times.
- Cable out for 6 minutes: request to 0 after 90 s, `PV comm lost` after 5 min (breakers open only if sheet 3 v2
  is loaded), UPS not shed; cable back, fault reset, PV back within the normal ramp.
- Afterwards: daily count of comm-fail warnings, OFF/AUTO toggles and UPS sheddings from the collector.

## Safety during the blind window

While a session is lost the inverters keep their last setpoint in both the old and the new program - the 0 of
the old program never reaches them. What the hold must not exceed is the time in which the load could fall by
more than the gensets' margin above their floor (25 % of the running nominal: 788 kW with three gensets,
290 kW with one). 90 s is right for two or three gensets; set 60 s when one genset runs. The controller's own
protections (bus, mains, anti-islanding, the gensets' reverse power) do not depend on the logger.

## Rollback

Load the 14 Oct archive, or set **PV comm fail hold** to 0.0 s - that is today's behaviour without reloading.
"""


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    write(sheet06(), os.path.join(OUT, "sheet06_Validation_v2.xml"))
    write(sheet10(True), os.path.join(OUT, "sheet10_PV_req_step_protection_v2.xml"))
    write(sheet10(False), os.path.join(OUT, "sheet10_PV_req_step_protection_v2_const.xml"))
    write(sheet03(), os.path.join(OUT, "sheet03_PV_breaker_control_v2.xml"))
    prog_dir = os.path.join(OUT, "program_v2")
    os.makedirs(prog_dir, exist_ok=True)
    for f in sorted(os.listdir(SRC)):
        if f.endswith(".xml"):
            shutil.copy(os.path.join(SRC, f), os.path.join(prog_dir, f))
    shutil.copy(os.path.join(OUT, "sheet06_Validation_v2.xml"), os.path.join(prog_dir, "sheet06_Validation.xml"))
    shutil.copy(os.path.join(OUT, "sheet10_PV_req_step_protection_v2.xml"), os.path.join(prog_dir, "sheet10_PV_req_step_protection.xml"))
    shutil.copy(os.path.join(OUT, "sheet03_PV_breaker_control_v2.xml"), os.path.join(prog_dir, "sheet03_PV_breaker_control.xml"))
    prog = load_program(prog_dir)
    with open(os.path.join(OUT, "program_v2.md"), "w", encoding="utf-8") as f:
        f.write(describe(prog).replace("sheet exports of 2026-10-14", "change 01 applied to the sheet exports of 2026-10-14"))
    with open(os.path.join(OUT, "README.md"), "w", encoding="utf-8") as f:
        f.write(README)
    print(f"wrote {OUT}: {len(prog.blocks)} blocks in {len(prog.sheets)} sheets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
