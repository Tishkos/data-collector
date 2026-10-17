# Change 01 v4 (the guard): closed-loop test

The master's PLC program run at 100 ms against a small plant model (tools/change01_sim.py). Reverse power trips the gensets at -10 % of the running nominal for 5 s (IG4, 17 Oct).

## Scenarios

| Scenario | Program | Lowest genset load | Reverse power | Trip | Biggest step on the gensets (15 s) | Export over limit | PV energy in window |
|---|---|---|---|---|---|---|---|
| 8 Sep pause | today | 780 kW (25 %) | 0.0 s | no | 1104 kW | 0 s | 32.6 kWh |
| 8 Sep pause | v3 | 785 kW (25 %) | 0.0 s | no | 1 kW | 0 s | 40.4 kWh |
| 8 Sep pause | v4 | 785 kW (25 %) | 0.0 s | no | 1 kW | 0 s | 40.4 kWh |
| 8 Sep pause, AP read as 0 | today | 780 kW (25 %) | 0.0 s | no | 1104 kW | 0 s | 32.6 kWh |
| 8 Sep pause, AP read as 0 | v3 | 780 kW (25 %) | 0.0 s | no | 1004 kW | 0 s | 33.8 kWh |
| 8 Sep pause, AP read as 0 | v4 | 780 kW (25 %) | 0.0 s | no | 151 kW | 0 s | 40.3 kWh |
| group 2 lost 40 s | today | 780 kW (25 %) | 0.0 s | no | 1214 kW | 0 s | 20.4 kWh |
| group 2 lost 40 s | v3 | 785 kW (25 %) | 0.0 s | no | 1 kW | 0 s | 40.5 kWh |
| group 2 lost 40 s | v4 | 785 kW (25 %) | 0.0 s | no | 1 kW | 0 s | 40.5 kWh |
| group 2 lost, cloud and sun back | today | 780 kW (25 %) | 0.0 s | no | 1214 kW | 0 s | 20.4 kWh |
| group 2 lost, cloud and sun back | v3 | 776 kW (25 %) | 0.0 s | no | 714 kW | 0 s | 35.1 kWh |
| group 2 lost, cloud and sun back | v4 | 776 kW (25 %) | 0.0 s | no | 714 kW | 0 s | 35.1 kWh |
| all lost 70 s | today | 785 kW (25 %) | 0.0 s | no | 1114 kW | 0 s | 33.0 kWh |
| all lost 70 s | v3 | 785 kW (25 %) | 0.0 s | no | 1117 kW | 0 s | 33.0 kWh |
| all lost 70 s | v4 | 785 kW (25 %) | 0.0 s | no | 1114 kW | 0 s | 33.0 kWh |
| AP invalid 30 s, no fail bit | today | 780 kW (25 %) | 0.0 s | no | 1115 kW | 0 s | 25.4 kWh |
| AP invalid 30 s, no fail bit | v3 | 780 kW (25 %) | 0.0 s | no | 1115 kW | 0 s | 25.4 kWh |
| AP invalid 30 s, no fail bit | v4 | 785 kW (25 %) | 0.0 s | no | 1 kW | 0 s | 40.4 kWh |
| on the grid, group 2 lost, load drop | today | 0 kW (0 %) | 0.0 s | no | 0 kW | 6 s | 33.5 kWh |
| on the grid, group 2 lost, load drop | v3 | 0 kW (0 %) | 0.0 s | no | 0 kW | 45 s | 75.9 kWh |
| on the grid, group 2 lost, load drop | v4 | 0 kW (0 %) | 0.0 s | no | 0 kW | 6 s | 33.5 kWh |

Scenario notes:

- **8 Sep pause**: The SAU silent 2.5 s on both sessions, load 2,000 kW, three gensets (Gen 1, 4, 5).
- **8 Sep pause, AP read as 0**: As above, and for 0.3 s after the SAU is back the AP read returns 0 (the mispaired frame) before the fail bit.
- **group 2 lost 40 s**: Only the reading session fails for 40 s; group 1 keeps writing. No load change.
- **group 2 lost, cloud and sun back**: Group 2 lost 40 s; a cloud takes the sun down to 500 kW for 15 s and passes.
- **all lost 70 s**: Both sessions silent 70 s: longer than the 60 s hold.
- **AP invalid 30 s, no fail bit**: The AP value invalid for 30 s while both ECU fail bits stay 0.
- **on the grid, group 2 lost, load drop**: On the grid through MCB 1 only (export limit 1,300 kW), exporting at the limit; group 2 lost 40 s and the load drops 1,000 kW at 15 s. A hold would push the export over the limit; v4 does what today does.

## Load drop while one session is lost (the hold's real exposure)

Group 2 lost from 10 s to 50 s, group 1 still writing; the load drops at 15 s. The figure is the largest drop (100 kW steps) the gensets survive without a reverse-power trip.

| Gensets running | Load | today | v3 (plain hold) | v4 (hold + guard) |
|---|---|---|---|---|
| Gen 4 + Gen 5 (2,150 kW) | 1500 kW | 1300 kW | 700 kW | 1100 kW |
| Gen 4 + Gen 5 (2,150 kW) | 2000 kW | 1400 kW | 700 kW | 1200 kW |
| Gen 1 + 4 + 5 (3,150 kW) | 1500 kW | 1500 kW | 1100 kW | 1400 kW |
| Gen 1 + 4 + 5 (3,150 kW) | 2000 kW | >= 1800 kW | 1100 kW | 1500 kW |

With group 1 lost (no writes reach the SAU) every program is equally blind: whatever the PLC computes cannot reach the inverters until the link is back.

Group 1 lost, three sets, load 2,000 kW: today 1100 kW, v3 1100 kW, v4 1100 kW.

## What limits the guard

The guard lowers the request faster than the inverters can follow, so the limit is how fast the inverters come down (166 kW/s measured on 8 Sep) and how long a write takes to act (1 s assumed). Largest survivable drop for v4, Gen 4 + Gen 5, load 2,000 kW:

| Inverter fall rate | v4 |
|---|---|
| 100 kW/s | 1000 kW |
| 166 kW/s | 1200 kW |
| 300 kW/s | 1200 kW |
