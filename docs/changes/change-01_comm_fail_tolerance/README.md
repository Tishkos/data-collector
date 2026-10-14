# Change 01 - PV logger communication tolerance

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
