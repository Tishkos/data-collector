# Handover notes - read this first

For whoever continues this work, a person or a tool. Everything learned, decided and still open, in one
place, as of 15 October 2026. Facts carry their source; anything marked *model* or *inferred* is not a fact.

## 1. The job

Erbil Coca-Cola hybrid plant (CCI, Iraq). A ComAp InteliNeo 5500 master runs five gensets (IG4 200) and
three mains incomers (InteliMains 210 G2) and curtails a 3.2 MW Sineng PV plant through a Sineng SAU-100
gateway. Two UPS of 350 kVA exist on paper. The plant burns diesel while curtailing solar; the contract is to
measure, understand, and then propose and document the configuration changes that cut fuel, with the plant
writing every change itself.

This repository is the read-only data collector for the plant PC, the Modbus server for the DT Turkey / CCI
monitoring platform, the interpreter and model of the master's PLC program, and the folder of proposed
changes. The sister repository `qs-ai-hybird` is not to be touched.

## 2. Rules that stay in force

- Read-only on every plant device. No code path writes; `tests` check that. Nothing is restarted, no setpoint
  touched, nothing changed on live equipment without the plant's written approval. In InteliConfig never
  **Write**, **Program** or **OK and Restart**; the plant writes any change itself on a copy of the archive,
  with the archive of 14 October as rollback.
- No credentials in files or chat. Controller, site and SAU passwords stay on site. No bypassing of
  licensing, passwords or the encrypted parts of archives. No experiments against the real controller.
- Repository hygiene: no mention of assistants, tools' temp paths or the sister project's name in files or
  commit messages; commits as `Tishko <sabirtiishko@gmail.com>` with no attribution lines.
- Documents and emails: plain language, short, black headings, no names of the previous integrator or his
  company. Statuses "Ready" / "Pending commissioning". Internal pending items are not disclosed to CCI.
  Nothing is claimed that is not verified; every number says where it comes from.
- WhatsApp drafts in the owner's style: lowercase except the first letter, commas instead of full stops,
  no numbered lists, no dashes or quotes, short.

## 3. The plant as known (sources: archive of 14 Oct, Modbus export, history 29 Sep - 14 Oct, site session)

- Network 192.168.0.0/24 (ComAp side). Plant PC `192.168.0.200` (Siemens IPC, Windows 10 IoT, internet
  over the CCI Wi-Fi 172.17.76.x). Master `.112` (Modbus unit 1, CAN address 9). Gensets `.105 .106 .113
  .114 .115`. Mains `.107 .111 .110`. SAU-100 `.128` (ETH0; ETH1 192.168.1.123), ten SP-320K-H inverters
  on RS485 behind it (unit ids 1-10 assumed), the array as unit 245. A sixth target 192.168.0.1 in the
  master's connection table has no definition (*inferred*: leftover).
- Running nominal 3,150 kW with three sets, 4,150 kW with four (history). Gen P Min 25 % (setpoint).
  Load about 2 MW on 14 Oct, 570-680 kW at night on the grid. Gensets run by hand since 1 October
  (history: no set in power management) - reason unknown.
- Export cap 1,300 kW per closed mains feeder (PLC setpoint). On 1 Oct the plant exported on three feeders.
- The master's PLC program: 11 sheets, 88 blocks, exported as `docs/site/neo_plc_2026-10-14/`. It is
  executed exactly by `tools/neo_plc.py` and reproduces history values (1,545.3 kW request on 1 Oct,
  "Gen Nom" 2,940 on 14 Oct). Summary: sheet 1 site sums and MCB/GCB logic; 2 UPS math (Running nominal
  Gensets = Running Nominal Of All - PV Actual P, Gen load %, shedding comparators 35/70/80 %); 3 PV and
  UPS breaker commands with fail latches; 4 PVCB status; 5-6 composers and validators; 7 grid request
  (site load + feeders x 1,300 kW, capped at the SAU's max available); 8 single-breaker cap 2,200 kW;
  9 Max Available x curtailment %, grid/gen/manual selection; 10 step protection (actual + 100 kW), PV comm
  fail = ECU2 OR ECU3 OR negative request -> request 0 -> ramp 25 kW/s up, 1,500 down -> ECU write;
  overproduction warning; 11 reactive request forced to 0.
- The master talks to the SAU over Modbus TCP in two sessions ("PV logger group 1/2", ECU 2 and ECU 3,
  timeout 3,000 ms). Group 1 writes AP Set as a 32-bit value at 2100-2101 every 1 s; group 2 writes AP Set
  at 2101-2102 every 10 s (and RP Set 2102 vs 2103, PF 2105, mode 1007 vs 1008, coil 4002 vs 4003). The
  two write lists are one register apart; with 32-bit values they overlap. Either the SAU has a second
  register set at +1 or one definition is off by one. **Open, measured by the trace, not to be discussed
  outside until measured.** The SAU allows five Modbus TCP links in all.
- Comm fails: 133 "PV Logger group 1/2 Comm Fail" warnings in the history 29 Sep - 14 Oct, up to 33 a day
  (9 Oct); on 14 Oct 20 between 08:52 and 09:58 in 5 bursts, mostly both sessions within seconds. The
  previous integrator says the SAU fails to respond for 6-9 s and proposes the root-cause route (pause
  before request, longer timeout). We agree the root cause comes first.
- Climb-back after the PV goes to zero: about 300 kW/min, measured on 14 Oct after two PV breaker closes
  (0 -> 685 kW in about 2.5 min). After a comm-fail zero it may be faster; the per-fail loss is between a dip
  and about five minutes and is **not measured** yet.
- The UPS shedding alarms in the history are an artefact: on PV drops the master's PV term goes stale and
  "Running nominal Gensets" collapses for seconds, so Gen load % jumps above the shed thresholds. Whether the
  UPS and their breakers are wired at all is **unverified**; the history shows UPS breaker fail warnings.
- Reactive control is dead by design (sheet 11). Grid export is allowed.
- The OFF/AUTO toggles on 14 Oct were energy-meter calibration by the plant, not alarm clearing.

## 4. What the model says (tools/plant_model.py; *model*, calibrated only on 14 Oct)

Fuel from a Willans line (ASSUMED 3,500-4,500 l/day absolute), curtailment regulator ASSUMED (0.03 %/s per
% error, fitted to 8 overproduction warnings), PV response 10 s. Relative results for 14 Oct: today about
4,130 l; two gensets in the sun -26 to -28 %; power management AUTO -32 to -45 %; one genset alone is only
safe without total-PV-loss events; a 2 x 350 kVA UPS buffer covers clouds, not a total loss. Change 01 alone
is worth about 140 kWh / 45 l a day today, about 350 kWh / 100 l with power management on. Treat all of
these as directions, not numbers, until the collector has weeks of data.

## 5. Change 01 - PV logger comm-fail tolerance (docs/changes/change-01_comm_fail_tolerance/)

Why: any comm fail on either session forces the PV request to 0 at once; when the link is back it climbs
from 0 again. What: sheet 10 keeps everything and holds the last request instead (Ana Switch fed by its own
output; the negative-request input removed from the OR), plus a "PV comm lost" delay. Variants: v2 (hold until
the link returns, 300 s delay as constant or setpoint 65514), v3 timed (hold at most 60 s, then 0 exactly as
today) built after the integrator's objection. Sheet 3 v2 (optional, stage 2): open the PV breakers after
5 min without the logger. Built in InteliConfig's own export format by `tools/neo_plc_change01.py`, tested in
`tests/test_change01.py` (identical with a healthy link; short fail ridden through; long fail safe; a real
zero request still reaches the SAU). The PLC has no Analog Switch left (7 of 7); the first import was refused
for that and rebuilt. v2 was imported on a copy of the archive on site; v3 is not yet imported. Request PDF
`change-01_request.pdf` (two pages, plain language) exists; it says "about five minutes" which is the upper
bound. **Status: parked until the collector has two weeks of data; then back with measured hold time and
margin.** Reverse power: the held value is at most 100 kW above the inverters' output at that moment (step
protection), the gensets sit around 25 % (about 788 kW with three sets), so reverse power inside a 60 s
hold needs a load drop of roughly 700 kW within that minute; load steps are not measured; IG4 reverse-power
settings are unread. Never claim the IG4 protection "covers" it.

## 6. Tools in this repository

| What | How |
|---|---|
| Hourly collector + dashboard + Turkey Modbus server | `START-PLANT.bat` (first `CHECK-PLANT.bat`) |
| Who answers on Modbus | `PROBE-PLANT.bat` (`plantcollector.cli probe`) |
| Fast read-only trace: 1 s master, 2 s SAU, setpoint read-back, response times, event log | `TRACE-PLANT.bat` (`plantcollector.cli trace`) -> `data\trace\` |
| Read the master's archive (plain parts) | `tools/neo_archive.py` |
| List and execute the PLC program | `tools/neo_plc.py` (`--out`, `--demo`) |
| Run a day of the plant | `tools/plant_model.py` (scenarios S0-S6, days) |
| The PLC lab: plant and program live in the browser, every sheet with live pin values, events injectable | `PLC-LAB.bat` (`tools/plc_lab.py`) -> http://localhost:8090 |
| Build the change-01 sheets | `tools/neo_plc_change01.py` |
| The request PDF | `tools/change01_note.py` |
| Plant drawing | `tools/plant_diagram.py` -> `docs/plant_overview.svg` |
| History export to CSV | `tools/neo_history.py` |
| Tests (38) | `python -m pytest -q` |

Docs: `NEO_CONFIGURATION.md` (how the configuration works), `SIMULATION.md` (exact vs assumed, scenarios),
`INFO_NEEDED.md` (open items), `SAFETY.md`, `DATA_FLOW.md`, `TURKEY_MODBUS.md`, `DEVICES.md`,
`site/visit_checklist.md` (the read-only export steps), `changes/` (every proposed change with its files,
tests, screenshots and rollback).

## 7. The staged plan for the contract

0. Know: archives, tables and histories of all nine controllers, the SAU settings and point table, the
   single-line diagram, the UPS wiring, the fuel log.
1. See: the collector on the plant PC, the trace for event studies, a daily report, alerts, remote access.
2. Change 01 with measured numbers, written by the plant on a copy, rollback ready.
3. Two gensets in the sun (power management on, settings as measured).
4. One genset plus the UPS as buffer (change 02: UPS math fix, shed delay, bridge) once the UPS are real.
5. Hand-over: documentation, training, the model recalibrated on measured data.

## 8. Open questions (who answers)

- Why the gensets run by hand since 1 Oct; the two mode changes on normal days (plant).
- UPS: in service, what they feed, wiring of the master's commands and feedbacks, make, model, ports (plant).
- SAU: point table (2100/2101), max client connections, firmware, RS485 counters, watchdog or fallback when
  the master goes silent, ramp rates (SAU web page, installer or Sineng).
- IG4: nominal ratings, reverse-power threshold and delay, Modbus servers on or off (archives, probe).
- Which inverter group sits behind PV CB1 and PV CB2; where the SAU meters M1 and M3 measure.
- Network: switch, cable path, Wi-Fi links, 192.168.0.1; remote access CCI allows.
- Plant: production hours, biggest load steps, fuel log, single-line diagram.

## 9. Next steps

1. Remote session with the plant engineer: PROBE, CHECK, START, TRACE on the plant PC; archives, Modbus
   tables and histories of the eight other controllers; SAU screenshots; UPS and genset photos; the questions.
2. Build the IG4 and IM210 profiles from their exported tables; a report tool over the trace files (comm
   fails per session with durations, response-time percentiles, setpoint word pattern, mode changes, UPS
   feedbacks, load steps); then the daily report and alerts.
3. After two weeks of data: Change 01 with numbers; recalibrate the model; the offer for stages 3-5.

## 10. Lessons already paid for

Overclaims cost trust with the plant: "covered by the IG4 reverse-power protection" (unverified, and the trip
is the feared outcome), "five to thirty times a day", "the toggles were to clear alarms" (they were meter
calibration), "the zero never reaches the SAU" (only when both sessions are down). State the source of every
number, mark the rest as estimate, and measure before proposing.
