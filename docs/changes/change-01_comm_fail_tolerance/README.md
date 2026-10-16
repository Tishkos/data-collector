# Change 01 - PV logger communication tolerance

Built from the sheet exports of 2026-10-14 by `tools/neo_plc_change01.py`. Same export format, so the way
in is: InteliConfig on a copy of the archive -> PLC editor -> import sheet -> compare -> save as a new archive.
Nothing here writes to the controller; the plant loads the new archive itself, at low load, with the
14 Oct archive at hand as the way back.

## Why

One lost Modbus reply on either PV-logger session raises "ECU 2/3 Comm Fail"; sheet 10 switches the PV request
to 0 the same cycle. The 0 never reaches the SAU (the link is down) - it arrives when the link is back and takes
the PV to zero, ~5 minutes to climb back, gensets slammed, UPS shed. 5-33 such events a day in the history.

## What changes (sheet 10 only; no new switch blocks - the PLC has no Analog Switch left)

| | Before | After |
|---|---|---|
| `PV comm fail` | ECU 2 OR ECU 3 OR "negative request" | ECU 2 OR ECU 3 |
| `PV req prot comm fail` (the switch before the ramp) | request while healthy, **0** while PV comm fail | request while healthy, **the last request held** while PV comm fail (the switch reads its own output) |
| new `PV comm lost` | - | Delay of `PV comm fail`, 300 s (setpoint **PV comm lost delay** or a fixed 300 s) - for a warning, and for sheet 3 in stage 2 |
| sheet 3 (optional, stage 2) | - | PV CB1/CB2 command AND NOT `PV comm lost`: five minutes without the logger opens the PV breakers |

The "negative request" guard is not needed any more: a request of 0 now passes to the SAU as 0 (it was
only ever 0, never negative), and holding would otherwise keep an old value when the controller asks for 0.
With a healthy link the program behaves exactly as today (checked output by output with `tools/neo_plc.py`).

## Files

- `sheet10_PV_req_step_protection_v2.xml` - uses the new setpoint (create it first, step 1)
- `sheet10_PV_req_step_protection_v2_const.xml` - same with a fixed 300 s, no setpoint needed
- `sheet03_PV_breaker_control_v2.xml` - optional, only after the plant agrees to automatic breaker opening
- `program_v2/` - the whole program with the sheets replaced, as the model runs it; `program_v2.md` its listing
- `design_review_v4.md` - the four-role review of v3 and v4, the revised v4 and the measurements that come first
- `sheet10_PV_req_step_protection_v3_timed.xml` - the timed variant: hold the last request for 60 s, then 0 exactly
  as today, no setpoint needed; `program_v3/` is this sheet with the plant's own other sheets. Its "PV comm lost"
  flag is the 60 s delay, so the breaker-opening sheet 3 must not be combined with it as it is; stage 2 with v3
  needs its own 300 s delay.

## Steps on the plant PC (copy of the archive, never the live controller)

1. (setpoint version only) Setpoints -> User setpoints -> add **PV comm lost delay**: unit `s`, 1 decimal,
   range 0.0-3600.0, default **300.0**, group "PV setpoints". If InteliConfig gives it a number other than
   65514, replace `65514` in the sheet file (2 places) before importing - or use the `_const` file.
2. PLC editor -> sheet "PV req step protection" -> import the sheet 10 file. Check: one new Delay block
   `PV comm lost` in the first column; the `PV comm fail` OR reads only the two ECU bits; the switch
   `PV req prot comm fail` has its own output on its second input; the ramp and the SAU write are untouched.
3. Alarms: add a warning on `PV comm lost` ("PV logger lost 5 min").
4. Compile, compare with the 14 Oct archive (only sheet 10 differs), save as a new archive with the date.
5. Controller side (settings, no PLC): ECU modules "PV Logger group 1/2" - response timeout 3,000 -> 5,000 ms;
   one module instead of two once the SAU point table confirms that 2100/2101 is one setpoint.
   Check on the SAU whether it has its own "no command received -> power limit" fail-safe; if so it must be
   longer than a normal fail burst and shorter than 300 s.

## Test, with the plant's approval, at low load, the collector logging

- SAU Ethernet cable out for 30 s: PV must not move; one warning only. Three times.
- Cable out for 6 minutes: `PV comm lost` after 5 min (breakers open only if sheet 3 v2 is loaded), UPS not
  shed; cable back, fault reset, PV continues at the held value or the breakers re-close within the normal ramp.
- Afterwards: daily count of comm-fail warnings, OFF/AUTO toggles and UPS sheddings from the collector.

## Safety during the blind window

While a session is lost the inverters keep their last setpoint in both the old and the new program - the 0 of
the old program never reaches them. What changes is only the moment the link returns: today the PV is sent to
zero, now it continues. The load may fall during the blind window; the running gensets can lose 25 % of their
nominal before any reverse power (788 kW with three gensets, 290 kW with one), the controller's own
protections (bus, mains, anti-islanding, the gensets' reverse power) do not depend on the logger, and after
300 s the PV is taken off deliberately (stage 2) or at least reported. Set the delay shorter (120 s) when
one genset runs alone.

## Rollback

Load the 14 Oct archive; or in the editor put the constant 0 back on the switch's second input.
