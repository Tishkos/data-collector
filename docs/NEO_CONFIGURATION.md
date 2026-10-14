# How the InteliNeo configuration works

From the controller archive of 2026-10-14 (`InteliNeo 5500_INEO 5500_20261014_01.aig4`, firmware
1.2.0.43, application "Standard HC"), the Values/Setpoints exports of the same day and the controller's
own Modbus table. `tools/neo_archive.py` extracts the readable parts of an archive; the extracted text
is in `docs/site/neo_archive_2026-10-14/`.

Confidence marks: **[fact]** read directly from the files; **[inferred]** follows from names, live
values and history together; **[open]** needs the PLC editor or the site.

## 1. What the controller is in this plant

The InteliNeo 5500 is the **hybrid master**: it does not control a genset or a breaker of its own
(BESS "Not Installed"). It sits on the CAN bus with the five genset controllers (IG4 200, addresses
1-5) and the three mains controllers (InteliMains 210 G2, addresses 6-8) as address 9 — one more than
the firmware's limit of 8, hence the permanent alarm **Max Num CU on CAN Exceeded** [fact].

Its job, as configured, is the **PV side** of the plant [fact]:

| Function | How |
|---|---|
| Read the PV plant | Modbus TCP master to the SAU-100 (192.168.0.128:502), two "PV Logger" definitions, 1-2 s refresh |
| Limit the PV plant | writes the active-power setpoint ("AP Set", registers 2100/2101) and reactive settings to the SAU — this is the curtailment |
| Switch the PV in and out | binary outputs **PV CB1 ON/OFF, PV CB2 ON/OFF** with feedbacks **PV1/PV2 feedback** (two PV breakers, one per transformer winding) |
| Control two UPS breakers | binary outputs **UPS1 ON/OFF, UPS2 ON/OFF** with feedbacks **UPS1/UPS2 feedback** |
| Read the mains incomers | Modbus TCP master to MAIN 1-3 (192.168.0.107 / .111 / .110): mains import P/Q/PF, load P/Q/PF, battery, MCB feedback (coil 0) |
| Measure the bus | its own voltage/frequency inputs: "Mains/Bus" 417 V, 50.01 Hz |
| Power management | enabled (kW mode, start reserve 300 kW, stop reserve 400 kW, min run power 210 kW) — but no genset was "in PM" on 14 Oct [fact], see §5 |

## 2. The devices it talks to — and their registers [fact]

These definitions are the plant's own register maps, taken from the archive. They are now
`profiles/pv_array_sau.yaml` and `profiles/im210_mains.yaml`.

**SAU-100 virtual array** (one unit for the whole PV plant; the Neo's "PV Logger group 1" and
"group 2" both point at 192.168.0.128:502):

| Register | Value | Scale |
|---|---|---|
| 1000 | AP active power | ×0.1 kW, int32 |
| 1002 | RP reactive power | ×0.1 kVAr |
| 1004 | power factor | ×0.001 |
| 1009 | daily yield | ×0.1 kWh — reads 635,283 on 14 Oct: **not plausible, verify** |
| 1011 | total yield | ×1 kWh — reads 3,010: **verify** |
| 1017 / 1019 / 1021 | inverters total / online / working | |
| 1063 / 1065 / 1067 | maximum active / reactive / min reactive power | ×0.1 (3,520 kW = 10 × 352) |
| **1093** | **maximum active power available** — what the sun allows right now | ×0.1 kW |
| 1095 / 1097 | reactive available / min reactive available | ×0.1 |
| 1187 / 1189 / 1193 / 1195 | inverters stopped / standby / faulty / alarm | |
| 2100-2101 (write) | AP Set — the PV power limit sent by the Neo | ×0.1 kW |
| 2102-2103, 2105, 2018, 2020, 1007/1008, coils 4002/4003 (write) | reactive / PF / percentage setpoints, mode, inverter ON/OFF | |

Register 1093 is the key number for curtailment analysis: *available − actual = PV thrown away*.

**InteliMains 210 G2** (each of MAIN 1-3): 1001 Mains Import P (int16), 1006 Mains Import Q,
1014 Mains PF ×0.01, 1046 LAOD P (int32), 1048 LAOD Q, 1050 LAOD PF ×0.01, 1051 battery ×0.1 V,
coil 0 MCB feedback.

The connection table also holds a sixth target, 192.168.0.1:502, with no definition attached [fact]
— probably a leftover entry (the old router address), not a device [inferred].

## 3. The PLC program [inferred from sheet names, PLC values, setpoints and protections]

The program body is encrypted in the archive; InteliConfig's PLC editor shows it on the plant PC.
What its vocabulary says it does, sheet by sheet:

1. **IM210 data aquisition** — collects the three InteliMains readings into site totals: *Mains Import
   P/Q/PF site*, *Load P/Q/PF site*, *MCB feedback numbers*, *Any MCB close*. On 14 Oct the three LAOD P
   were 1,472 / 1,471 / 1,442 kW while the gensets carried 1,482 kW in total: the three units very
   probably measure the same bus, and *Load P site* = 4,425 kW is the triple sum [inferred]. Several
   downstream values were negative (−1,473) as a result.
2. **UPS breakers math** — *UPS1 ON / UPS1 OFF / UPS 1 CB command* with *UPS1 ON cmd del* (setpoint
   "UPS1 ON command delay" 10 s), fail-to-close / fail-to-open / other-fail protections with "Short
   Del", and the warnings **UPS 1 Shedding / UPS 2 Shedding**. Setpoints "UPS 1 Load Shedd ON 70 % /
   OFF 35 %" and "UPS 2 Load Shedd ON 80 % / OFF 35 %" act on *Gen Load %* (gensets P ÷ running nominal:
   38 % on 14 Oct). Reading: when the gensets are loaded above 70 % (UPS 1) or 80 % (UPS 2) the Neo
   commands the UPS breaker so that its critical loads are shed from the bus, and reconnects below
   35 %. On 14 Oct both OFF commands were active and both feedbacks were 1, consistent with 38 % load.
3. **PV Breaker controll** — *PV CB1 command / PV CB2 command*, *Any PV CB on*, *PV feedback number*
   (2 = both closed), the fail-to-close/open protections for PV CB1 and PV CB2. The breakers are also
   opened by the protection **PV prod > PV req BRKR open** (see sheet 9).
4. **MCB & PV Breakers restriction power** and 7. **PV breakers restriction** — limits that depend on how
   many breakers are closed: "MAX P req 1 PV breaker" 2,200 kW when only one PV breaker is in, "Max
   Export 1 transf" 1,300 kW per transformer towards the mains (*Max Export transformers*,
   *PV req PV CB restriction*).
5. **Validation** — checks the SAU data (*AP validate*, *Max Active Power Available valid*) and produces
   *PV req prot comm fail*: when the PV logger communication fails, the PV request is forced to 0
   [inferred from the value being 0.0 and the protection names]. This is why a Modbus problem on the
   SAU shows up as a PV trip.
6. **Grid || request** — the PV request when the plant is parallel with the mains: *PV P req grid*,
   *Site load no gen*, *Site Load with GEN*, *GCB and MCB close*, with the setpoints "MCB and GCB close
   exp request" and "PV Q min import" 1,000 kVAr. Not active on 14 Oct (no mains).
8. **PV request Mains/Gen** — selects the request source: *PV Output req gen kW* when on gensets (the
   firmware's own curtailment with "Gen P Min 25 %" also applies), the grid request when on mains,
   "MAN PV P req" when forced manually; result *PV req (kW)*.
9. **PV req step protection** — ramps the request: *PV AP + step prot* = actual PV + "PV req step
   protection" 100 kW, *PV req protected*, *PV req out ramp* → written to the SAU as AP Set. Watchdog:
   if the PV produces more than requested by "PV Prod > PV req" 75 kW for "PV Prod > PV req del" 60 s,
   the warning **PV prod > PV req Warning** (AHI delay 15 s) and then **PV prod > PV req BRKR open** —
   the PV breakers are opened because the inverters are not following the limit. **Reverse power
   Warning** / *Reverse Power PV not react* protect against export into the gensets.
10. **PV Q request** — reactive power for the inverters: "Reactive power scheduling mode" (0 on 14 Oct),
    *Q min math*, *Site Q*, *PV Q req*, *PV Q req out ramp* → RP Set.

Other PLC bits worth knowing: *PV Actual > 100 kW* and *GroupLink* (see §4), *Any GCB close*,
*Q function start*, *PV comm fail*.

## 4. What the history log shows [fact], and what it means [inferred]

Two sources: the 750 records inside the archive (text reasons only) and the full InteliConfig history
export of the same period (`docs/site/neo_history_2026-09-29_2026-10-14.csv`, with the value snapshot of
every event; `tools/neo_history.py` makes these files from any History.xls).

**The daily cycle is a grid cycle, not a sun cycle.** Every evening at about 18:05 the plant transfers to
the **government grid**: gensets stop, `Groups Unlinked` ("2 - 1"), `Forced Value(1)` pulses, and `Other
CB Trip` records the PV breakers opening on the transfer (MCB 1 and 2 closed, 574 kW import). Every
morning (06:12 in mid-October, 08:00-10:30 earlier in the month — the grid's own schedule) the plant
transfers back to gensets with an **open transition**: the bus is dead for a few seconds (`Mains/Bus <f`,
all voltages 0), `BO PV Anti Islanding`, `PVCB Opened`, `Groups Linked` ("2 + 1"), then three gensets
(running nominal 3,150 kW) and `PVCB Closed` a few minutes later. Night load on the grid: 570-680 kW.
Some days have extra grid windows in the afternoon. So the gensets run about 12 hours a day — exactly the
solar hours — which is why curtailment hurts so much here.

**Daytime operation**: 3 gensets by hand most of the day (3,150 kW), a 4th at times (4,150 / 3,851 kW),
genset load 21-33 %. The curtailment rule reproduces the records exactly: at 12:25 on 14 Oct the load was
1,829 kW, the running nominal 3,151 kW, and the PV 1,041 kW = load − 25 % × running nominal. The PLC's
*request* usually sits ~240 kW below that (median of 91 warnings), so the inverters often produce more
than requested → `AHI PV prod > PV req Warning` (93 in 16 days; a warning only, the breakers stayed closed).

**Mode switching by hand**: `SetpointChange CON(8315)=0 → =2` = controller OFF then AUTO from
InteliConfig, up to **ten times a day** (14 Oct: 08:04, 09:19, 09:23, 09:37, 12:25). Each OFF opens the
PV breakers at once (0.3-1.0 MW dumped on the gensets), each AUTO re-closes them with a **voltage dip to
175-216 V** (−10 to −24 %), and on 12:25 the genset load jumped to 57 % and both UPS shed. The toggles
follow `PV Logger Comm Fail` bursts — the operators use OFF/AUTO as a "reset" for the PV communication.

**PV logger communication failures**: 2-33 per day (26 on 5 Oct, 33 on 9 Oct, 23 on 10 Oct, 20 on 14 Oct).
Each one makes the validation sheet force the PV request to 0; the PV recovers at ~300 kW/min. That is the
RS485/SAU problem seen from the controller's side.

**UPS shedding** on 8 of 15 days — always right after a PV loss (trip, toggle, comm fail), when the gensets
suddenly carry the whole load.

**Setpoint trials on 1 Oct** by an Ethernet client (`U=0,T=ETH CA1`): **Gen P Min** 25 → 20 → 15 → 25 %
within six minutes, and **Min Run Power** 210 → 50 → 210 kW. Somebody already tried lowering the genset
floor to let more PV in and went back. Before step 2 of the modification plan, ask what they saw.

**Fault resets** from Ethernet on 4 Oct 07:01 and 10 Oct 09:36. Two `Wrn Production Mode` records (1 Oct
08:07, a controller start with everything at zero) — a firmware message at power-up, not a plant alarm.

## 5. Settings that decide the plant's behaviour [fact]

| Group | Setting | Value | Effect |
|---|---|---|---|
| Curtailment | Curtailment | Enabled | the Neo limits PV to keep the gensets loaded |
| Curtailment | **Gen P Min** | **25 %** | gensets must keep ≥ 25 % of the *running* nominal power; PV takes the rest |
| Curtailment | Gain / Int | 5.00 / 15 s | controller speed |
| Power Management | Mode / reserves | kW, start 300 / stop 400 kW | would run two gensets for 1.5 MW |
| Power Management | Min Run Power | 210 kW | |
| Power Management | Dynamic Spinning Reserve, Solar Coverage Ratio | Enabled, 20 % | extra reserve for PV variability |
| Power Management | Priority / Auto Swap | 32 / Disabled | |
| PV setpoints | Max Export 1 transf | 1,300 kW | per-transformer limit towards the mains |
| PV setpoints | MAX P req 1 PV breaker | 2,200 kW | limit with one PV breaker |
| PV setpoints | UPS 1 / UPS 2 Load Shedd ON / OFF | 70/35 %, 80/35 % | UPS logic thresholds |
| PV setpoints | PV req step protection | 100 kW | ramp step |
| PV setpoints | PV Prod > PV req / del | 75 kW / 60 s | "inverters not following" watchdog |
| User buttons | 1-4 = MAN ON, 10 = MAN OFF | latched | their meaning is in the PLC [open] |
| Basic | Fuel genset consumption | 237.3 g/kWh | used for the fuel statistics |
| Ethernet | AirGate | Enabled, waiting to connect | no internet on the ComAp network |

On 14 Oct **no genset was in power management** (Running Nominal Power In PM = 0 while 3,851 kW were
running) [fact]. Together with four gensets at 38 % load and the manual OFF/AUTO switching, the gensets
look operated by hand [inferred] — to be confirmed on the IG4 panels (MAN/AUTO).

## 6. Protections defined by the plant [fact]

PV CB1/CB2 fail to close / fail to open / other fail; PV prod > PV req Warning; PV prod > PV req BRKR
open; Reverse power Warning; UPS1/UPS2 fail to close / open / other fail; UPS 1 Shedding; UPS 2
Shedding; PV Logger group 1/2 Comm Fail; Modbus TCP IM210 G2 Comm Fail.

"Wrn Production Mode" on the alarm list is a **firmware** message (it sits among the controller's
own UI texts), not a plant-defined protection — its meaning is a question for ComAp [open].

## 7. What is not readable, and how to get it

- The PLC program body and the screens are encrypted in the archive. On the plant PC, InteliConfig →
  Controller configuration → PLC editor shows every sheet; a screenshot per sheet is enough to confirm
  §3 and to find what Forced Value 1 and the user buttons do.
- The per-inverter registers of the SAU (units 1-10) are not in the archive — the Neo only reads the
  array. Still the SAU point table or a register scan.
- The IM210 unit id is not in the definition (ComAp default 1 assumed).

## 8. What this means for the work

- The collector now reads the array (available vs actual PV = curtailment) and the three mains units
  with the plant's own register maps — no guessing, and the same registers the Neo polls every second.
- Step 2 of the plan (gensets back under power management) has a precedent: the 1 Oct trial. Find out
  why it was reverted before repeating it.
- Step 3 (UPS logic) has a concrete base: the UPS breakers are already automated as load shedding at
  70/80 % genset load. A "bridge on PV drop" would be an extension of that sheet, built in the PLC editor
  on a copy of this archive and proven in the emulator before anyone writes it to the controller.
