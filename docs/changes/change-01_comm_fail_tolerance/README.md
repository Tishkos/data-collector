# Change 01 - PV logger communication tolerance

Built from the sheet exports of 2026-10-14 by `tools/neo_plc_change01.py`. Same export format, so the way
in is: InteliConfig on a copy of the archive -> PLC editor -> import sheet -> compare -> save as a new archive.
Nothing here writes to the controller; the plant loads the new archive itself, at low load, with the
14 Oct archive at hand as the way back.

## v4: the hold with a guard (the version to use; built and tested offline on 17 Oct, not imported yet)

v4 replaces v2 and v3. Both of those let a negative request through to the SAU (they took the "negative request"
out of the fail OR), hold a value that can already be corrupted, and have no protection against a load drop
during the hold. The sections further down describe v2 and v3 and stay for the record.

Files: `sheet10_PV_req_step_protection_v4_guard.xml` and `sheet06_Validation_v4.xml` (**both** must be imported;
sheet 6 only declares its two existing invalid flags as sheet outputs, no block changes there), `program_v4/` the
whole program as the model runs it, `program_v4.md` its listing, `v4_simulation.md` the closed-loop results
(`tools/change01_sim.py`), tests in `tests/test_change01_v4.py`.

| | today | v4 |
|---|---|---|
| healthy link | - | identical to today, output by output (also with a negative manual request: 0) |
| what counts as a fail | ECU 2 OR ECU 3 comm fail OR negative request | ECU 2 OR ECU 3 OR AP invalid OR Max Available invalid (the validators of sheet 6); the negative request still gives 0 while healthy |
| during a fail, on two or more gensets | request forced to 0 | the value **actually sent** to the SAU is held (the ramp output: one corrupted cycle costs at most one ramp step of 150 kW) |
| the guard | - | while held, if Gensets P falls below 15 % of the nominal of the sets actually loaded (Gen Loaded bits x per-set nominal), the held value is lowered by 4 % every second cycle (about 15 % per second) until the gensets are 50 kW above that floor again; only while ECU 2 (the session that writes) has no fail; at most 4 s in a row per fail. Lowering only |
| on the grid, with one genset, or 60 s after the fail | 0 | 0, as today |

Resources: 13 new blocks (no Analog Switch; Mux 2 -> 6, Comp Hyst +2, AND/OR +2, Math Fc +2, Delay +2,
AxB/C+D +1, and v3's Comp 4 / AxB are not used), the seven binary PLC outputs that were still free (PLC-BOUT 13
bits 2-7, PLC-BOUT 7 bit 0) and PLC-AOUT 21286-21292. All new blocks sit after the plant's last column, so the
order they are listed in is also the order of their positions, as in every plant sheet.

Per-set nominals in the two Mux tables: Gen 1 1,000, Gen 2 1,000 (ASSUMED: Gen 2 was not read, password), Gen 3
700, Gen 4 1,000, Gen 5 1,150 kW (IG4 archives 17 Oct). If a set is re-rated or swapped, rebuild with the new
figures. "Two sets loaded" means a floor above 213 kW (largest single-set floor 172, smallest pair 255).

What the closed-loop model shows (details in `v4_simulation.md`; plant model with the measured session timing,
writes through group 1 only, inverters falling 166 kW/s, reverse power 10 % / 5 s):
- a 2.5 s pause like 8 Sep: today about 1.1 MW onto the gensets; v4 nothing;
- the same pause with the AP read corrupted for 0.3 s before the fail bit: v3 about 1 MW, v4 150 kW;
- one session lost and a load drop: largest drop the gensets survive without a reverse-power trip, Gen 4 + 5 at
  2,000 kW load: v3 700 kW, v4 1,200 kW (today 1,400 kW, because it has already thrown the solar away - at the
  price of a ~1.2 MW step at every fail); three sets: v3 1,100, v4 1,500 (today 1,800+);
- the guard is faster than the inverters; its limit is how fast the inverters come down (166 kW/s on 8 Sep) and
  the write delay. On a big drop the reverse-power dip itself is instant; the guard ends it inside the 5 s delay;
- on the grid and with one genset v4 does exactly what today does;
- if the writing session goes silent and its flag comes late, the guard lowers blind for at most 4 s; the step
  when the link returns then stays within about 100 kW of today's.
Known small effects: the held value alternates by up to about 2.5 kW from cycle to cycle (the hold loop runs over
two cycles because the new blocks sit after the switch); the guard may lower somewhat more than needed (the write
every second and the inverters' delay), which costs solar, never safety.

Checks before it goes near the plant:
1. Import both sheets on a **copy** of the archive (never the live controller). InteliConfig must accept the block
   budget and the object numbers (only the Analog Switch limit is known).
2. In the editor: the two floor Mux tables read 0/150/150/300/105/255/255/405 and 0/150/172/322; "PV guard factor"
   reads 100/100/100/100/100/96/0/0; "PV req final" reads held/held/0/0/held/0/held/0; the switch's second input is
   "PV req guarded"; the ramp reads "PV req final" and still shows 25 and 1500 per s; "PV guard spent" shows 4 s up
   and 60 s down.
3. From the recorder: AP and Max Available are never invalid outside comm fails (otherwise the hold also starts
   then; still safe, it ends in 0 after 60 s as today); Gensets P stays valid during logger fails.
4. The PLC cycle is 100 ms (the guard's rate assumes it).
5. The kill criteria of the design review still stand: #14364 through a fail, and the read-back through
   one-session fails.
6. Compare with the 14 Oct archive: only sheets 6 and 10 differ; keep the 14 Oct archive as the way back.

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
