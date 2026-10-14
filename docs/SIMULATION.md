# Plant behaviour model — `tools/plant_model.py`

A minute-by-minute model of what the controller's rules decide over a day, so a modification can be
tried on paper before anyone touches the plant. It models the *rules* (as reconstructed from the
archive, `NEO_CONFIGURATION.md`), not the ComAp firmware; its outputs are directions and orders of
magnitude until it has been validated against recorded days (§4).

Run: `python tools/plant_model.py` → table on screen, `docs/simulation/scenarios.csv`, `docs/simulation/one_day.png`.

## 1. What is real and what is assumed

| Item | Value | Source |
|---|---|---|
| Gen P Min | 25 % | archive setpoint (REAL) |
| Start / stop reserve, min run power | 300 / 400 / 210 kW | REAL |
| Next start / stop delay | 20 / 180 s | REAL |
| Dynamic spinning reserve, solar coverage ratio | enabled, 20 % | REAL |
| Watchdog | 75 kW for 60 s | REAL |
| PV caps | 3,200 kW (two breakers), 2,200 kW (one), 1,300 kW export per transformer | REAL |
| UPS shedding thresholds | UPS 1 on 70 % / off 35 %, UPS 2 on 80 % / off 35 %, 10 s delay | REAL |
| Fuel at full load | 237.3 g/kWh → 0.283 l/kWh | REAL setpoint, diesel 0.84 kg/l |
| **Grid cycle** | on the government grid ~18:05 → ~06:12, gensets by day | REAL, history (every day) |
| **Night load** | 650 kW | REAL, mains import at the transfers (574-678 kW) |
| **Genset nominal steps** | 1,150 / 1,000 / 1,000 / 1,000 / 700 kW | from the history's "Gen Nom" values (nameplates to confirm) |
| **Today's operation** | 3 gensets by hand (3,150 kW), a 4th in the afternoon | REAL, history of 14 Oct |
| **Curtailment rule** | PV = load − 25 % × running nominal | REAL — reproduces 1,041 kW at 12:25 on 14 Oct exactly |
| **Request margin** | PLC request ~250 kW below that | CALIBRATED on the 91 "PV prod > PV req" warnings (median 239 kW) |
| **PV recovery ramp** | ~300 kW/min after a trip | REAL, two recoveries on 14 Oct |
| **PV interruptions of 14 Oct** | 8 (five OFF/AUTO toggles, three comm-fail resets) | REAL, history |
| Daytime load | 1,450-1,850 kW through recorded points (07:16, 10:30, 12:25, 13:06, 17:28) | REAL points, ASSUMED in between |
| PV available | clear-sky bell, sunrise 06:05, sunset 17:45, peak 78 % of rated (17 MWh/day) | ASSUMED (SAU register 1093 will replace it) |
| Cloud passages | 8 drops of 40-70 % lasting 5-20 min (cloudy day) | ASSUMED |
| Fuel curve shape | 10 % of full-load fuel at idle, linear to full load (0.37 l/kWh at 25 %) | ASSUMED typical diesel |
| UPS critical load, autonomy | 5 % of plant load per UPS, 30 min | ASSUMED |
| PV response to a lower setpoint | 30 s | ASSUMED |

**Validation (14 Oct as recorded)**: model 5,316 kWh of PV against ~6,000 kWh from the inverters' own counters
(−11 %), 40 engine-hours against ~36-40 (3 gensets × 12 h plus the 4th). Good enough for directions and
rankings; the SAU's "available power" register and a few more recorded days will tighten it.

## 2. Scenarios

- **S0 today** — three gensets by hand, a fourth from 14:00, everything else as configured.
- **S1** — automatic power management with the settings exactly as they are in the controller.
- **S2** — S1 + Gen P Min 20 % (the setting someone tried on 1 Oct).
- **S3** — S1 + UPS bridge: after a PV drop of more than 250 kW/min the UPS carries its critical loads for
  8 minutes, which allows the solar coverage ratio to be lowered from 20 % to 10 %.

Day types: 14 Oct as recorded (with its eight PV interruptions), a clear day without interruptions, a
cloudy day, and a day without any grid.

## 3. Results (per day; gensets run ~12 h because the night is on the grid)

| Scenario | 14 Oct as recorded | Clear day, no interruptions | Cloudy day | No grid at all |
|---|---|---|---|---|
| S0 today | 40 engine-h, **4,720 l**, PV 5.3 MWh (69 % curtailed), gensets at 17 % | 4,630 l, PV 5.7 MWh | 4,670 l, PV 5.5 MWh | 82 h, 7,890 l |
| S1 AUTO as configured | 24 h, **3,550 l (−25 %)**, PV 8.0 MWh (53 %), gensets at 21 % | 3,420 l (−26 %), PV 8.7 MWh | 3,550 l (−24 %) | 36 h, 5,820 l (−26 %) |
| S2 + Gen P Min 20 % | 3,330 l (−29 %), PV 8.9 MWh | 3,190 l (−31 %), PV 9.6 MWh | 3,360 l (−28 %) | 5,590 l (−29 %) |
| S3 + UPS bridge, coverage 10 % | 3,550 l — same as S1 | 3,420 l — same as S1 | 3,530 l | 5,820 l |

What the table says, and how far to trust it:

1. **Power management instead of hand operation is the lever**: about a quarter of the diesel
   (≈ 1,200 l/day, ≈ 400,000 l/year at today's pattern) and 50 % more solar, because two gensets carry the
   daytime load instead of three or four, and Gen P Min then holds back ~540 kW instead of ~790-960 kW.
2. **Gen P Min 20 % adds another 4-5 %.** Worth it, but second.
3. **The UPS bridge gives no fuel benefit at this plant** — with 1 MW genset steps, lowering the reserve by
   10 % of the PV output does not change the number of engines. Justify it on protection grounds only,
   and only once the real UPS logic is known.
4. **The interruptions cost ~370 kWh of PV on 14 Oct (7 %)** plus the voltage dips and the UPS shedding
   that come with them. Stopping the OFF/AUTO "reset" habit and fixing the SAU communication is free
   money and should come before any setpoint change.
5. **Interaction to handle**: with two gensets the load reaches 70-80 % at times, which is where the UPS
   shedding triggers (121 shedding-minutes in S1 on the recorded day). The UPS thresholds have to be reviewed
   together with the move to power management.
6. Gensets at 17-21 % average load in every scenario: the daytime load is simply small for 1 MW machines.
   A smaller "day" genset or a battery would change that picture more than any setpoint.

## 4. Making it right: calibrate, validate, then predict

1. **Exact rules** — one screenshot per PLC sheet (the UPS sheet, Forced Value 1, user buttons).
2. **Recorded inputs instead of assumptions** — the collector's data (hourly), the SAU's data records and
   further history exports at minute resolution for the calibration days; SAU register 1093 for the PV
   available.
3. **Validation days** — reproduce PV used, gensets running, UPS events and trips for 14 Oct and a few
   more days within ~10 %.
4. **Then the modifications** — each one a scenario; deltas against the validated baseline.

## 5. Adding a modification

A modification is a `Scenario(...)` with changed fields (the controller's setpoint names) or a new rule
block in `simulate()`. Keep the REAL/ASSUMED marking, add it to `SCENARIOS`, run the tool; the CSV and
the chart regenerate.
