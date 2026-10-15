# InteliNeo 5500 PLC program (change 01 applied to the sheet exports of 2026-10-14)

Blocks are listed in execution order. `[#n]` is the controller communication object, `.b` a bit; `setpoint` marks a user setpoint. Constants are shown in engineering units.

## Sheet 1: IM210 data aquisition

1. **Math Fc** - Mains Import P site = add(Mains Import  P [#19419], Mains Import  P [#19412], Mains Import  P [#19405])
2. **Math Fc** - Mains Import Q site = add(Mains Import  Q [#19418], Mains Import  Q [#19411], Mains Import  Q [#19404])
3. **Math Fc** - Mains Import PF site = op4 (minimum or average)(Mains PF [#19417], Mains PF [#19410], Mains PF [#19403])
4. **Math Fc** - Load P site = add(LAOD P [#19436], LAOD P [#19415], LAOD P [#19408])
5. **Math Fc** - Load Q site = add(LAOD Q [#19435], LAOD Q [#19414], LAOD Q [#19407])
6. **Math Fc** - Load PF site = op4 (minimum or average)(LAOD PF [#19420], LAOD PF [#19413], LAOD PF [#19406])
7. **AND/OR** - MCB #1 = MCB Feedback [#10169.0] AND CAN CU 6 [#8546.5]
8. **AND/OR** - MCB #2 = MCB Feedback [#10170.0] AND CAN CU 7 [#8546.6]
9. **AND/OR** - MCB #3 = MCB Feedback [#10752.0] AND CAN CU 8 [#8546.7]
10. **AND/OR** - Any GCB close = Gen Loaded 1 [#10196.0] OR Gen Loaded 2 [#10196.1] OR Gen Loaded 3 [#10196.2] OR Gen Loaded 4 [#10196.3] OR Gen Loaded 5 [#10196.4]
11. **Convert** - Reactive power scheduling mode = Convert(Reactive power scheduling mode [#65534 setpoint]) (overflow flag -> PLC-BOUT 9.4)
12. **Convert** - Daily Yield no decimals = Convert(Daily yield [#19400]) (overflow flag -> PLC-BOUT 10.6)
13. **AND/OR** - Any MCB close = MCB #1 [#10424.7] OR MCB #2 [#10425.0] OR MCB #3 [#10425.1]
14. **AND/OR** - GroupLink = NOT Any MCB close [#10424.6] AND NOT Any MCB close [#10424.6]
15. **AND/OR** - GCB and MCB close = Any MCB close [#10424.6] AND Any GCB close [#10433.3]

Outputs wired to controller functions:
- Mains Import P site -> controller function 430
- Mains Import Q site -> controller function 431
- GroupLink -> controller function 59
- Any MCB close -> controller function 65
- GCB and MCB close -> controller function 19
- Reactive power scheduling mode -> ECU write: reactive power scheduling mode (SAU)
- Daily Yield no decimals -> controller function 361

## Sheet 2: UPS breakers math

1. **Math Fc** - Running nominal Gensets = subtract(Running Nominal Power Of All [#10658], PV Actual P [#15594])
2. **Interpolation** - Gen load = Interpolation(Gensets P [#20183]; X1 0 -> Y1 0, X2 Running nominal Gensets [#21284] -> Y2 100) (out of range -> PLC-BOUT 12.5, invalid -> PLC-BOUT 12.6)
3. **Ana Switch** - Gen Load = Ana Switch(1 when switch 0, Gen load [#21285] when switch 1; switch Any GCB close [#10433.3])
4. **Comp Hyst** - UPS 1 CB command = Comp Hyst(Gen Load [#21263]; on UPS 1 Load Shedd OFF [#65520 setpoint], off UPS 1 Load Shedd ON [#65521 setpoint])
5. **Comp Hyst** - UPS 2 CB command = Comp Hyst(Gen Load [#21263]; on UPS 2 Load Shedd OFF [#65518 setpoint], off UPS 2 Load Shedd ON [#65519 setpoint])

## Sheet 3: PV Breaker controll

1. **AND/OR** - PV CB1 command = User Button 1 [#20743.0] AND NOT PV CB1 Fail to close [#10428.2] AND NOT PV CB1 Fail to open [#10428.3] AND NOT PV CB1 other fail [#10428.4] AND PVCB Close/Open [#9145.7] AND NOT PV comm lost [#10436.2]
2. **AND/OR** - PV CB2 command = User Button 2 [#20743.1] AND NOT PV CB2 fail to close [#10428.7] AND NOT PV CB2 fail to open [#10429.0] AND NOT PV CB2 other fail [#10429.1] AND PVCB Close/Open [#9145.7] AND NOT PV comm lost [#10436.2]
3. **AND/OR** - UPS 1 = User Button 3 [#20743.2] AND NOT UPS1 fail to close [#10429.2] AND NOT UPS1  fail to open [#10429.3] AND NOT UPS1  other fail [#10429.4] AND UPS1 ON cmd del [#10436.0]
4. **Delay** - UPS1 ON cmd del = Delay(UPS 1 CB command [#10435.6]; up UPS1 ON command delay [#65517 setpoint] s, down 0 s)
5. **AND/OR** - UPS2 = User Button 4 [#20743.3] AND NOT UPS2 fail to close [#10433.7] AND NOT UPS2 fail to open [#10434.0] AND NOT UPS2  other fail [#10434.1] AND UPS2 ON cmd del [#10436.1]
6. **Delay** - UPS2 ON cmd del = Delay(UPS 2 CB command [#10435.7]; up UPS2 ON command delay [#65516 setpoint] s, down 0 s)
7. **Breaker** - PV CB1 ON / PV CB1 OFF = Breaker(command PV CB1 command [#10428.5], feedback PV1 feedback [#8235.0]); status -> PV CB1 status, fail to close -> PV CB1 Fail to close plc, fail to open -> PV CB1 Fail to open plc, other fail -> PV CB1 other fail plc
8. **Breaker** - PV CB2 ON / PV CB2 OFF = Breaker(command PV CB2 command [#10428.6], feedback PV2 feedback [#8235.1]); status -> PV CB2 status plc, fail to close -> PV CB2 Fail to close plc, fail to open -> PV CB2 Fail to open plc, other fail -> PV CB2 other fail plc
9. **Breaker** - UPS1  ON / UPS1  OFF = Breaker(command UPS 1 [#10429.5], feedback UPS1 feedback [#8235.2]); status -> UPS1  status, fail to close -> UPS1  fail to close plc, fail to open -> UPS1  fail to open plc, other fail -> UPS1  other fail plc
10. **Breaker** - UPS2 ON / UPS2 OFF = Breaker(command UPS2 [#10435.2], feedback UPS2 feedback [#8235.3]); status -> UPS2 status, fail to close -> UPS2 fail to close plc, fail to open -> UPS2 fail to open plc, other fail -> UPS2 other fail plc
11. **RS** - PV CB1 Fail to close = RS(set PV CB1 Fail to close plc [#10425.7], reset FltRes Button Echo [#11897.0]; reset priority 1)
12. **RS** - PV CB1 Fail to open = RS(set PV CB1 Fail to open plc [#10426.0], reset FltRes Button Echo [#11897.0]; reset priority 1)
13. **RS** - PV CB1 other fail = RS(set PV CB1 other fail plc [#10426.1], reset FltRes Button Echo [#11897.0]; reset priority 1)
14. **RS** - PV CB2 fail to close = RS(set PV CB2 Fail to close plc [#10426.7], reset FltRes Button Echo [#11897.0]; reset priority 1)
15. **RS** - PV CB2 fail to open = RS(set PV CB2 Fail to open plc [#10427.0], reset FltRes Button Echo [#11897.0]; reset priority 1)
16. **RS** - PV CB2 other fail = RS(set PV CB2 other fail plc [#10427.1], reset FltRes Button Echo [#11897.0]; reset priority 1)
17. **RS** - UPS1 fail to close = RS(set UPS1  fail to close plc [#10427.7], reset FltRes Button Echo [#11897.0]; reset priority 1)
18. **RS** - UPS1  fail to open = RS(set UPS1  fail to open plc [#10428.0], reset FltRes Button Echo [#11897.0]; reset priority 1)
19. **RS** - UPS1  other fail = RS(set UPS1  other fail plc [#10428.1], reset FltRes Button Echo [#11897.0]; reset priority 1)
20. **RS** - UPS2 fail to close = RS(set UPS2 fail to close plc [#10434.7], reset FltRes Button Echo [#11897.0]; reset priority 1)
21. **RS** - UPS2 fail to open = RS(set UPS2 fail to open plc [#10435.0], reset FltRes Button Echo [#11897.0]; reset priority 1)
22. **RS** - UPS2  other fail = RS(set UPS2 other fail plc [#10435.1], reset FltRes Button Echo [#11897.0]; reset priority 1)

Outputs wired to controller functions:
- PV CB1 ON -> Binary output 1: PV CB1 ON
- PV CB1 OFF -> Binary output 4: PV CB1 OFF
- PV CB2 ON -> Binary output 2: PV CB2 ON
- PV CB2 OFF -> Binary output 5: PV CB2 OFF
- UPS1  ON -> Binary output 3: UPS1 ON
- UPS1  OFF -> Binary output 6: UPS1 OFF
- UPS2 ON -> Binary output 7: UPS2 ON
- UPS2 OFF -> Binary output 8: UPS2 OFF

## Sheet 4: (no name)

1. **AND/OR** - Any PV CB on = PV CB1 status [#10425.6] OR PV CB2 status plc [#10426.6] OR UPS1  status [#10427.6]
2. **AND/OR** - PVCB function status = Any PV CB on [#10429.6] AND PVCB Close/Open [#9145.7]

Outputs wired to controller functions:
- PVCB function status -> controller binary input function (PVCB feedback, ASSUMED)

## Sheet 5: MCB & PV Breakers restriction power

1. **Comp 4** - PV feedback number = Comp 4(PV1 feedback [#8235.0] x 1 + PV2 feedback [#8235.1] x 2)
2. **Comp 4** - MCB feedback numbers = Comp 4(MCB Feedback [#10169.0] x 1 + MCB Feedback [#10170.0] x 2 + MCB Feedback [#10752.0] x 4)
3. **Decomp 4** - PV feedback bit0, PV feedback bit1, PV feedback bit2, PV feedback bit3 = bits 0*4+0..3 of PV feedback number [#21259]
4. **Decomp 4** - MCB feedback bit0, MCB feedback bit1, MCB feedback bit2, MCB feedback bit3 = bits 0*4+0..3 of MCB feedback numbers [#21271]

## Sheet 6: Validation

1. **Validator** - AP(active Power) validate = Validator(AP(Active Power) [#19433]; default 0) (invalid flag -> PLC-BOUT 9.8)
2. **Validator** - Max Active Power Available valid = Validator(Maximum Active Power Available [#19424]; default 0) (invalid flag -> PLC-BOUT 10.1)

## Sheet 7: Grid || request

1. **Math Fc** - Site load no gen = add(PV Actual P [#15594], Mains Import P site [#21253])
2. **AxB/C+D** - Max Export transformers = MCB feedback numbers [#21271] x Max Export 1 transf [#65535 setpoint] / 1 + 0  (invalid flag -> PLC-BOUT 8.5)
3. **Math Fc** - Site Load with GEN = subtract(Site load no gen [#21275], Gensets P [#20183])
4. **Math Fc** - PV req math = add(Site Load with GEN [#21276], Max Export transformers [#21274])
5. **Convert** - PV req math conv = Convert(PV req math [#21273]) (overflow flag -> PLC-BOUT 8.7)
6. **Comp Hyst** - PV req Max power = Comp Hyst(PV req math conv [#21277]; on Max Active Power Available valid [#21282], off Max Active Power Available valid [#21282])
7. **Ana Switch** - PV P req grid = Ana Switch(PV req math conv [#21277] when switch 0, Max Active Power Available valid [#21282] when switch 1; switch PV req Max power [#10431.5])

## Sheet 8: PV breakers restriction

1. **Comp Hyst** - PLC-BOUT 6.8 = Comp Hyst(PV P req grid [#21278]; on MAX P req 1 PV breaker [#65515 setpoint], off MAX P req 1 PV breaker [#65515 setpoint])
2. **Ana Switch** - 1 breaker = Ana Switch(PV P req grid [#21278] when switch 0, MAX P req 1 PV breaker [#65515 setpoint] when switch 1; switch PLC-BOUT 6.8 [#10429.7])
3. **Mux** - PV req PV CB restriction = Mux(select bits PV feedback bit0 [#10430.2], PV feedback bit1 [#10430.3]; inputs 0..3: 0, 1 breaker [#21262], PV P req grid [#21278], PV P req grid [#21278])

## Sheet 9: PV request Mains/Gen

1. **Interpolation** - PV Output req gen kW = Interpolation(Curtailment Output [#14364]; X1 PV Interpol X1 [#65529 setpoint] -> Y1 0, X2 PV interpol X2 [#65528 setpoint] -> Y2 Maximum Active Power Available [#19424]) (out of range -> PLC-BOUT 7.2, invalid -> PLC-BOUT 7.7)
2. **Mux** - PV req (kW) = Mux(select bits NOT Any MCB close [#10424.6], User Button 10 [#20743.9]; inputs 0..3: PV req PV CB restriction [#21261], PV Output req gen kW [#21264], MAN PV P req [#65527 setpoint], MAN PV P req [#65527 setpoint])

## Sheet 10: PV req step protection

1. **Math Fc** - PV AP + step prot = add(AP(active Power) validate [#21281], PV req step protetion [#65525 setpoint])
2. **Comp Hyst** - NOT PV negative request = Comp Hyst(PV req (kW) [#21265]; on 0, off 0)
3. **Math Fc** - PV pro > PV req = add(PV req (kW) [#21265], PV Prod > PV req [#65523 setpoint])
4. **Comp Hyst** - NOT reverse power = Comp Hyst(Load P site [#21256]; on -10, off -10)
5. **Delay** - PV comm lost = Delay(PV comm fail [#10432.1]; up PV comm lost delay [#65514 setpoint] s, down 0 s)
6. **Comp Hyst** - PV req > PV power = Comp Hyst(PV req (kW) [#21265]; on PV AP + step prot [#21268], off PV AP + step prot [#21268])
7. **AND/OR** - PV comm fail = ECU 2 Comm Fail [#11900.1] OR ECU 3 Comm Fail [#11900.2]
8. **Comp Hyst** - PV Prod > PV req comp = Comp Hyst(AP(active Power) validate [#21281]; on PV pro > PV req [#21270], off PV pro > PV req [#21270])
9. **AND/OR** - Reverse power Warning = NOT Any MCB close [#10424.6] AND reverse power [#10432.4]
10. **Ana Switch** - PV req protected = Ana Switch(PV req (kW) [#21265] when switch 0, PV AP + step prot [#21268] when switch 1; switch PV req > PV power [#10431.1])
11. **AND/OR** - PV prod > PV req Warning = Reverse power Warning [#10432.5] OR PV Prod > PV req comp [#10424.2]
12. **Delay** - PV prod > PV req del = Delay(PV Prod > PV req comp [#10424.2]; up PV Prod > PV req del [#65522 setpoint] s, down 0 s)
13. **Delay** - Reverse Power PV not react = Delay(Reverse power Warning [#10432.5]; up 15 s, down 0 s)
14. **Ramp** - PV req out ramp = Ramp(PV req prot comm fail [#21279]; up 25 per s, down 1500 per s)
15. **Ana Switch** - PV req prot comm fail = Ana Switch(PV req protected [#21269] when switch 0, PV req prot comm fail [#21279] when switch 1; switch PV comm fail [#10432.1])
16. **AND/OR** - PV Fail BRK Open = PV prod > PV req del [#10424.3] OR Reverse Power PV not react [#10433.1]
17. **RS** - PV prod > PV req BRKR open = RS(set PV Fail BRK Open [#10432.6], reset FltRes Button Echo [#11897.0]; reset priority 1)

Outputs wired to controller functions:
- PV req out ramp -> ECU write: PV active power setpoint (SAU)

## Sheet 11: PV Q request

1. **Math Fc** - Site Q = add(PV Actual Q [#15595], Mains Import Q site [#21254], Gensets Q [#20182])
2. **Math Fc** - Q min math = subtract(Site Q [#21266], PV Q min import [#65524 setpoint])
3. **Comp Hyst** - Q min comp = Comp Hyst(Q min math [#21250]; on 0, off 0)
4. **Comp Hyst** - PV Actual > 100kW = Comp Hyst(PV Actual P [#15594]; on 100, off 100)
5. **Comp Hyst** - Q > 0 = Comp Hyst(Gensets Q [#20182]; on -1, off -1)
6. **AND/OR** - Q fucntion start = Q min comp [#10424.0] AND PV Actual > 100kW [#10431.7] AND Q > 0 [#10435.3]
7. **Ana Switch** - Q min req = Ana Switch(0 when switch 0, Q min math [#21250] when switch 1; switch Q fucntion start [#10432.0])
8. **Convert** - Q min req (kvar) = Convert(Q min req [#21251]) (overflow flag -> #10424)
9. **Ana Switch** - PV Q req = Ana Switch(Q min req (kvar) [#21252] when switch 0, 0 when switch 1; switch Logical 1 [#9149.13])
10. **Ramp** - PV Q req out ramp = Ramp(PV Q req [#21248]; up 5 per s, down 250 per s)

Outputs wired to controller functions:
- PV Q req out ramp -> ECU write: PV reactive power setpoint (SAU)

## Values the program reads from the controller

- Mains Import  P [#19419] - Mains Import P (IM1)
- Mains Import  P [#19412] - Mains Import P (IM2)
- Mains Import  P [#19405] - Mains Import P (IM3)
- Mains Import  Q [#19418] - Mains Import Q (IM1)
- Mains Import  Q [#19411] - Mains Import Q (IM2)
- Mains Import  Q [#19404] - Mains Import Q (IM3)
- Mains PF [#19417] - Mains PF (IM1)
- Mains PF [#19410] - Mains PF (IM2)
- Mains PF [#19403] - Mains PF (IM3)
- LAOD P [#19436] - LAOD P (IM1)
- LAOD P [#19415] - LAOD P (IM2)
- LAOD P [#19408] - LAOD P (IM3)
- LAOD Q [#19435] - LAOD Q (IM1)
- LAOD Q [#19414] - LAOD Q (IM2)
- LAOD Q [#19407] - LAOD Q (IM3)
- LAOD PF [#19420]
- LAOD PF [#19413] - LAOD PF (IM2)
- LAOD PF [#19406] - LAOD PF (IM3)
- MCB Feedback [#10169.0] - MCB Feedback (IM1, ECU-BIN 1)
- CAN CU 6 [#8546.5] - CAN16 (controller present bits)
- MCB Feedback [#10170.0] - MCB Feedback (IM2, ECU-BIN 2)
- CAN CU 7 [#8546.6] - CAN16 (controller present bits)
- MCB Feedback [#10752.0] - MCB Feedback (IM3, ECU-BIN 3)
- CAN CU 8 [#8546.7] - CAN16 (controller present bits)
- Gen Loaded 1 [#10196.0] - Gen Loaded 1..5
- Gen Loaded 2 [#10196.1] - Gen Loaded 1..5
- Gen Loaded 3 [#10196.2] - Gen Loaded 1..5
- Gen Loaded 4 [#10196.3] - Gen Loaded 1..5
- Gen Loaded 5 [#10196.4] - Gen Loaded 1..5
- Reactive power scheduling mode [#65534] (setpoint)
- Daily yield [#19400] - Daily yield (SAU)
- Running Nominal Power Of All [#10658]
- PV Actual P [#15594] - PV Actual P (controller)
- Gensets P [#20183]
- UPS 1 Load Shedd OFF [#65520] (setpoint)
- UPS 1 Load Shedd ON [#65521] (setpoint)
- UPS 2 Load Shedd OFF [#65518] (setpoint)
- UPS 2 Load Shedd ON [#65519] (setpoint)
- User Button 1 [#20743.0] - User Buttons
- PVCB Close/Open [#9145.7] - Log Bout 3 (bit 7 = PVCB Close/Open)
- User Button 2 [#20743.1] - User Buttons
- User Button 3 [#20743.2] - User Buttons
- UPS1 ON command delay [#65517] (setpoint)
- User Button 4 [#20743.3] - User Buttons
- UPS2 ON command delay [#65516] (setpoint)
- PV1 feedback [#8235.0] - Binary Inputs
- PV2 feedback [#8235.1] - Binary Inputs
- UPS1 feedback [#8235.2] - Binary Inputs
- UPS2 feedback [#8235.3] - Binary Inputs
- FltRes Button Echo [#11897.0] - Log Bout 10 (bit 0 = FltRes Button Echo)
- AP(Active Power) [#19433] - AP (SAU active power)
- Maximum Active Power Available [#19424] - Maximum Active Power Available (SAU)
- Max Export 1 transf [#65535] (setpoint)
- MAX P req 1 PV breaker [#65515] (setpoint)
- Curtailment Output [#14364] - Curtailment Output (%)
- PV Interpol X1 [#65529] (setpoint)
- PV interpol X2 [#65528] (setpoint)
- User Button 10 [#20743.9] - User Buttons
- MAN PV P req [#65527] (setpoint)
- PV req step protetion [#65525] (setpoint)
- PV Prod > PV req [#65523] (setpoint)
- PV comm lost delay [#65514] (setpoint)
- ECU 2 Comm Fail [#11900.1] - Log Bout 13 (bits 1,2 = ECU 2/3 Comm Fail)
- ECU 3 Comm Fail [#11900.2] - Log Bout 13 (bits 1,2 = ECU 2/3 Comm Fail)
- PV Prod > PV req del [#65522] (setpoint)
- PV Actual Q [#15595] - PV Actual Q (controller)
- Gensets Q [#20182]
- PV Q min import [#65524] (setpoint)
- Logical 1 [#9149.13] - Log Bout 7 (bit 13 = Logical 1)

## User setpoints used by the program

- PV comm lost delay [#65514]
- MAX P req 1 PV breaker [#65515] = 2200 kW
- UPS2 ON command delay [#65516] = 10 s
- UPS1 ON command delay [#65517] = 10 s
- UPS 2 Load Shedd OFF [#65518] = 35 %
- UPS 2 Load Shedd ON [#65519] = 80 %
- UPS 1 Load Shedd OFF [#65520] = 35 %
- UPS 1 Load Shedd ON [#65521] = 70 %
- PV Prod > PV req del [#65522] = 60 s
- PV Prod > PV req [#65523] = 75 kW
- PV Q min import [#65524] = 1000 kVAr
- PV req step protetion [#65525] = 100 kW
- MAN PV P req [#65527] = 0 kW
- PV interpol X2 [#65528] = 100 %
- PV Interpol X1 [#65529] = 0 %
- Reactive power scheduling mode [#65534] = 0  
- Max Export 1 transf [#65535] = 1300 kW

## Reading notes (to confirm in InteliConfig)

- Math Fc operation codes: 0 add, 1 subtract (code 4 is only used for the site power-factor displays).
- Comp Hyst with the ON level below the OFF level works inverted (output 1 below ON, 0 above OFF); this is how the UPS blocks use it.
- Mux: the first connected binary inputs are the select bits (first = least significant), the analog inputs are the data inputs 0..n.
- Decomp 4 with group 0 gives bits 0..3 of the number; Comp 4 builds the number from bits 0..3.
- Breaker block: ON/OFF follow the command, status follows the feedback; the fail outputs come when feedback does not follow the command (timeout not visible in the export).
- Ramp constants are rates per second in the input's unit; Delay constants are in 0.1 s.
