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
| PV request step, watchdog | 100 kW; 75 kW for 60 s | REAL |
| PV caps | 3,200 kW (two breakers), 2,200 kW (one), 1,300 kW export per transformer | REAL |
| UPS shedding thresholds | UPS 1 on 70 % / off 35 %, UPS 2 on 80 % / off 35 %, 10 s delay | REAL |
| Fuel at full load | 237.3 g/kWh → 0.283 l/kWh | REAL setpoint, diesel 0.84 kg/l |
| Running genset capacity | 3,851 kW with four loaded → **963 kW per genset, all equal** | REAL total, ASSUMED split (nameplates unknown) |
| PV rated | 3,200 kW | REAL |
| Load profile | 1,500 kW night, 1,860-1,900 kW 05:30-13:00, down to 1,480 kW by 17:30 | ASSUMED, anchored on the two readings of 14 Oct |
| PV available | clear-sky bell, sunrise 06:05, sunset 17:45, peak 78 % of rated (17 MWh/day) | ASSUMED for Erbil, mid-October |
| Cloud passages | 8 drops of 40-70 % lasting 5-20 min (cloudy day) | ASSUMED |
| Fuel curve shape | 10 % of full-load fuel at idle, linear to full load (0.37 l/kWh at 25 %) | ASSUMED typical diesel |
| UPS critical load, autonomy | 5 % of plant load per UPS, 30 min | ASSUMED |
| PV response to a lower setpoint | 30 s | ASSUMED (RS485 success 53-99 % seen) |
| Government grid | none (as on 14 Oct) or 22-06 h | scenario |

Sanity check: on 14 Oct inverter 1 produced 601.6 kWh, so the plant made about 6 MWh. The model's
"today" scenario makes 7.7-8.3 MWh. Same order, model slightly optimistic — the real day was probably
not fully clear and the load profile is a guess. This is the first number to calibrate (§4).

## 2. Scenarios

- **S0 today** — four gensets kept running by hand, everything else as configured.
- **S1** — automatic power management with the settings exactly as they are in the controller.
- **S2** — S1 + Gen P Min 20 % (the setting someone tried on 1 Oct).
- **S3** — S1 + UPS bridge: after a PV drop of more than 250 kW/min the UPS carries its critical loads for
  8 minutes, which allows the solar coverage ratio to be lowered from 20 % to 10 %.

## 3. First results (indicative)

| Scenario | Clear day, no grid | Cloudy day, no grid |
|---|---|---|
| S0 today | 96 engine-h, **10,600 l**, PV used 8.3 MWh of 17.0 (51 % curtailed), gensets at 34 % | 96 h, 10,760 l, 7.7 MWh of 14.2 |
| S1 AUTO as configured | 50 engine-h, **8,380 l (−21 %)**, PV used 12.0 MWh (29 % curtailed), gensets at 57 % | 51 h, 8,720 l (−19 %), 10.7 MWh |
| S2 + Gen P Min 20 % | 8,210 l (−23 %), 12.7 MWh | 8,590 l (−20 %), 11.2 MWh |
| S3 + UPS bridge, coverage 10 % | 8,370 l — same as S1 | 8,700 l — same as S1 |

What the table says, and how far to trust it:

1. **Running the gensets under power management is the big lever**: about a fifth of the diesel and
   45 % more solar, because two engines carry the load instead of four and Gen P Min then holds back
   ~480 kW instead of ~960 kW. The size of the number depends on the assumed load profile; the direction
   does not.
2. **Gen P Min 20 % adds little** (another 2 %). It is not where the money is.
3. **The UPS bridge gives no fuel benefit at this plant.** With ~1 MW genset steps, cutting the
   spinning reserve by 10 % of the PV output almost never changes the number of engines running. Its
   value, if any, is safety during PV drops — the model shows no overload minutes even without it. So the
   UPS modification should be justified on protection grounds, not on fuel, and only once the real UPS
   logic is known.
4. **An interaction nobody has mentioned**: with two gensets the load sits at 70-80 % at night, which
   is exactly where the UPS shedding is set to trigger (70 % / 80 %). Running the plant under power
   management with today's UPS thresholds would make the UPS shed critical loads every night. Those
   thresholds have to be reviewed together with step 2 of the plan.

## 4. Making it right: calibrate, validate, then predict

A model is trusted when it reproduces days that already happened. The order:

1. **Exact rules** — one screenshot per PLC sheet from InteliConfig on the plant PC. Each rule in the
   model is a small named block with the controller's own setpoint names, so a wrong guess is replaced
   in one place.
2. **Recorded inputs instead of assumptions** — load, PV available (SAU register 1093), grid state, at
   minute resolution for the calibration days: the SAU's data records and the Neo's history export
   (the collector's own hourly data gives the daily totals).
3. **Validation days** — the model must reproduce, for 14 Oct and a few more days: PV used (6 MWh),
   gensets running, UPS events, watchdog trips. Only then the scenario numbers mean something.
4. **Then the modifications** — each one is a scenario; the report shows the delta against the
   validated baseline, and the same scenario file documents what will be written to the controller.

## 5. Adding a modification

A modification is a `Scenario(...)` with changed fields (setpoints) or a new rule block in
`simulate()`. Keep the controller's setpoint names, keep the REAL/ASSUMED marking, and add the
scenario to `SCENARIOS`. The CSV and the chart regenerate with one command.
