# Design review: the comm-fail handling, v3 and v4

Four reviewers, four roles, two rounds, 16 October 2026. The modifier proposed, the questioner attacked, the risk
officer rated, the evidence lead listed what must be known. Nothing here is measured yet unless it says so.

## What is under review

- **v3** - after a PV logger comm fail is declared, hold the last PV request for N seconds, then force 0 as today.
- **v4** - v3 plus a ceiling: during the fail, if the gensets fall below their floor (Gen P Min x genset nominal,
  minus a deadband), lower the held request by the shortfall at once, lowering only; after 5 min open the PV
  breakers (stage 2, optional).

## Where the four agree

1. **Root cause first.** Timeout 3 -> 5 s, a pause before each request, one session instead of two: settings, no
   sheet, reversible, the previous integrator's own route. The trace runs before and after, and the fails that
   survive that fix decide whether v3 is worth a PLC sheet at all.
2. **v3 is a second step, not an alternative.** A longer timeout only hides fails shorter than itself; the ones
   that are declared still zero the request. N comes from the measured fail lengths, not from a round number.
3. **With both sessions silent nothing we write reaches the SAU**, today or with any version. The hold neither
   causes nor prevents reverse power in that case; only the SAU's own fallback (unknown) or the inverters'
   frequency response (unread) acts there.
4. **The hold's real exposure is one session alive plus a load drop.** Today the zero gets through the other
   session within 10 s; a hold waits N seconds. With three sets the margin is about 700 kW of load drop in a
   minute; with one set it is under a normal production step. No hold with one set.
5. **Never say the IG4 reverse-power protection "covers" it.** It is the feared outcome, and the settings are unread.

## Where the questioner was right and the design changed

| Objection | Answer in the revised v4 |
|---|---|
| The floor must not come from "Running Nominal Of All" (#10658): it carries the PV term that goes stale in a fail, the same artefact that sheds the UPS. | Genset nominal = Gen Loaded 1-5 bits (#10196) x per-set nominal constants from the IG4 archives, no PV term. Smoothing dropped. |
| Gen P Min is the regulator's target, not a protection; the sets dip below it daily. A lowering-only ratchet needs a release. | Deadband below Gen P Min from the p1 of healthy seconds, so the ceiling sleeps on daily dips; release 1 kW/s during the fail; on return the switch goes live at 25 kW/s under the step protection. Nothing outlives the fail. |
| If the AP validator's invalid flag rises before the comm-fail bit, the held value is 0 + 100 kW. | "PV comm fail" = ECU 2 OR ECU 3 OR AP invalid OR Max Available invalid (PLC-BOUT 9.8 and 10.1 already exist), stretched 2 s on the falling edge. An invalid read holds the last good value whichever flag rises first. |
| Does the hold even survive the link's return? The request comes from the controller's Curtailment Output (#14364); if that regulator collapses while its PV input is invalid, the switch goes live on return and the 1,500 kW/s down-ramp undoes the hold in a second. | Unmeasured. Kill criterion 1 below. If it collapses, the cure is a controller setting, not a sheet, and v3's value is only the fail's length. |
| Stage 2 (breaker opening after 5 min) before the SAU's silent-master behaviour is known. | Dropped from v4 until that is read. |

## The revised v4 in ten lines

- PV comm fail = ECU 2 OR ECU 3 OR AP invalid OR Max Available invalid, stretched 2 s down; drives the hold switch.
- Genset nominal = Mux(Gen Loaded 1-3) + Mux(Gen Loaded 4-5) over per-set constants, Math Fc add.
- Floor = nominal x Gen P Min / 100 - deadband (AxB/C+D).
- Shortfall+ = max(floor - Gensets P #20183 - gate, 0); gate = Comp 4(NOT fail, Any MCB close) x 100000:
  the ceiling acts only during a fail and only on gensets.
- Request = max(held - Shortfall+, 0) x PV allowed (N s) into the existing Ramp; up-rate pin 25 kW/s healthy,
  1 kW/s in a fail; down 1,500 kW/s untouched; ECU write 200044 untouched.
- About 13 blocks over v3; no Analog Switch needed; the import on a copy is the only check of the block budget.
- Healthy link: identical to today, output by output.

## Kill criteria (measurements that come first)

1. **#14364 Curtailment Output, #15594 PV Actual P, #20183 Gensets P, Gen Loaded and the four flags at 1 s
   through fail events.** v4 dies, and v3 is worth little, if the regulator drops to 0 or well below its pre-fail
   value and is still there when the fail bit clears; v4 dies if #20183 freezes during a fail.
2. **AP Set read-back (2100/2101) through one-session fails.** v4 dies if the surviving session's write does not
   show in the read-back within 10 s; then v3 with a short N only.

## What still stands against v4 (the questioner's last word)

1. Latency: with group 1 dead, the lowering reaches the SAU on group 2's 10 s write and the inverters follow
   with their own delay; the ceiling is no faster than today's zero, only smaller. Whether 10-20 s beats the IG4
   reverse-power delay is unread. Read the IG4 settings and the inverter frequency curve before loading.
2. Per-set constants go stale when a set is re-rated or swapped: read the nominal over CAN if the PLC can,
   otherwise note the constants on the rollback sheet.
3. Block count: the plant's engineer must be able to read the sheet on the PLC monitor and explain it; one page
   in plain words with the pin names before the import.
4. The deadband's p1 needs a date and a source.
5. Nothing here changes what the SAU does while the master is silent.

## Risk register, the three not accepted before evidence and the three acceptable now

Not before evidence: one genset by day (UPS verified and sized, IG4 settings read, load steps measured);
automatic breaker opening (SAU silent-master behaviour, breaker-to-inverter mapping, a separate 300 s delay for
v3); any v4 lowering before the 2100/2101 read-back is on the trace.

Acceptable now: v3 with three sets, written by the plant on a copy with the 14 October rollback, once N comes from
data and #14364 is shown to survive; timeout 5,000 ms and pause before request; the hourly collector and bounded
trace runs with the fail count watched.

## The five items that unlock the most decisions (the evidence lead)

1. Fail lengths per session with the SAU's response times (root cause, N, v4).
2. The setpoint word pattern 2100/2101 plus the SAU point table (root cause, N, v4, one set).
3. The IG4 archives: reverse power, overload, nominal, start-to-load time (everything from v3 on).
4. The UPS reality: nameplates, wiring, what they feed (one set, change 02, the model).
5. The load at 1 s for two weeks: steps and the daytime profile (N, v4, two sets, one set).

Scripts to turn the trace into those numbers: fails (start, end, length, session, overlap, kWh lost), SAU latency
percentiles aligned with the master's fails, setpoint word cadence and decoding, load steps over 1/10/60 s,
inverter ramp from decoded setpoint vs output, regulator fit from curtail % vs gen load.

## One sentence for the plant's management

During a short logger communication fault the master keeps the solar at its last value instead of sending it to
zero, lowers it at once if the gensets fall toward their minimum, and returns to today's behaviour after a time
taken from the plant's own records; the plant's engineer loads it on a copy with the 14 October archive as the way
back, and it neither adds nor removes any genset protection.
