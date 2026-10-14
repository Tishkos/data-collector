# neo.aig4
controller InteliNeo 5500, firmware 1.2.0.43, 603,847 bytes

## Modbus-master device definitions (ECU datapoints)
- part at 410604: encrypted (method 99, 3315 bytes)
- part at 411691: encrypted (method 99, 19118 bytes)
- part at 414032: encrypted (method 99, 1374 bytes)
- part at 414725: encrypted (method 99, 1268 bytes)
- part at 415384: encrypted (method 99, 1268 bytes)
- part at 416043: encrypted (method 99, 1374 bytes)
- part at 416736: encrypted (method 99, 1374 bytes)
- definition 1 (at 417429):
    FC 3 addr  1046  type 2  x1  kW    LAOD P
    FC 3 addr  1001  type 1  x1  kW    Mains Import  P
    FC 3 addr  1048  type 2  x1  kVAr  LAOD Q
    FC 3 addr  1050  type 1  x0.01        LAOD PF
    FC 3 addr  1006  type 1  x1  kVAr  Mains Import  Q
    FC 3 addr  1014  type 1  x0.01        Mains PF
    FC 3 addr  1051  type 1  x0.1  V     BAT
    FC 1 addr     0  type 6  x1        MCB Feedback
- definition 2 (at 417875):
    FC 3 addr  1000  type 2  x0.1  kW    AP(Active Power)
    FC 3 addr  1002  type 2  x0.1  kVAr  RP(Reactive Power)
    FC 3 addr  1004  type 2  x0.001  kVAr  PF(Power Factor)
    FC 6 addr  1007  type 4  x1        Reactive power scheduling mode  [WRITE by the controller]
    FC16 addr  2100  type 5  x0.1  kW    AP(Active Power) Set  [WRITE by the controller]
    FC16 addr  2102  type 2  x0.1  kVAr  RP(Reactive Power) Set  [WRITE by the controller]
    FC 5 addr  4002  type 6  x1        Inverter ON/OFF  [WRITE by the controller]
- definition 3 (at 418399):
    FC 3 addr  1000  type 2  x0.1  kW    AP(Active Power)
    FC 3 addr  1002  type 2  x0.1  kVAr  RP(Reactive Power)
    FC 3 addr  1004  type 2  x0.001  kVAr  PF(Power Factor)
    FC 3 addr  1009  type 2  x0.1  kWh   Daily yield
    FC 3 addr  1011  type 2  x1  kWh   Total yield
    FC 3 addr  1063  type 5  x0.1  kW    Maximum Active Power
    FC 3 addr  1065  type 5  x0.1  kVAr  Maximum Reactive Power
    FC 3 addr  1067  type 2  x0.1  kVAr  Mininimum Reactive Power
    FC 3 addr  1093  type 5  x0.1  kW    Maximum Active Power Available
    FC 3 addr  1095  type 5  x0.1  kVAr  Maximum Reactive Power Available
    FC 3 addr  1097  type 2  x0.1  kVAr  Mininimum Reactive Power Availab
    FC 3 addr  1017  type 2  x1        Total Inverter Number
    FC 3 addr  1019  type 2  x1        Online Inverter Number
    FC 3 addr  1021  type 2  x1        Work Inverter Number
    FC 3 addr  1187  type 2  x1        Stop Inverter Number
    FC 3 addr  1189  type 2  x1        St-by Inverter Number
    FC 3 addr  1193  type 2  x1        Faulty Inverter Number
    FC 3 addr  1195  type 2  x1        Alarm Inverter Number
    FC 6 addr  1008  type 4  x1        Reactive power scheduling mode  [WRITE by the controller]
    FC16 addr  2101  type 5  x0.1  kW    AP(Active Power) Set  [WRITE by the controller]
    FC16 addr  2103  type 2  x0.1  kVAr  RP(Reactive Power) Set  [WRITE by the controller]
    FC16 addr  2105  type 2  x0.001        PF(Power Factor) Set  [WRITE by the controller]
    FC16 addr  2018  type 5  x0.001  %     AP - rated power percentage Set  [WRITE by the controller]
    FC16 addr  2020  type 2  x0.001  %     RP - rated power percentage Set  [WRITE by the controller]
    FC 5 addr  4003  type 6  x1        Inverter ON/OFF  [WRITE by the controller]
- definition 4 (at 419183):
    FC 3 addr  1046  type 2  x1  kW    LAOD P
    FC 3 addr  1001  type 1  x1  kW    Mains Import  P
    FC 3 addr  1048  type 2  x1  kVAr  LAOD Q
    FC 3 addr  1050  type 1  x0.01        LAOD PF
    FC 3 addr  1006  type 1  x1  kVAr  Mains Import  Q
    FC 3 addr  1014  type 1  x0.01        Mains PF
    FC 3 addr  1051  type 1  x0.1  V     BAT
    FC 1 addr     0  type 6  x1        MCB Feedback
- definition 5 (at 419629):
    FC 3 addr  1046  type 2  x1  kW    LAOD P
    FC 3 addr  1001  type 1  x1  kW    Mains Import  P
    FC 3 addr  1048  type 2  x1  kVAr  LAOD Q
    FC 3 addr  1050  type 1  x0.01        LAOD PF
    FC 3 addr  1006  type 1  x1  kVAr  Mains Import  Q
    FC 3 addr  1014  type 1  x0.01        Mains PF
    FC 3 addr  1051  type 1  x0.1  V     BAT
    FC 1 addr     0  type 6  x1        MCB Feedback

## Modbus-master connections (ip:port, timeout)
- 192.168.0.128:502  timeout 3000 ms
- 192.168.0.128:502  timeout 3000 ms
- 192.168.0.107:502  timeout 1000 ms
- 192.168.0.1:502  timeout 1000 ms
- 192.168.0.111:502  timeout 1000 ms
- 192.168.0.110:502  timeout 1000 ms

## PLC sheets
- IM210 data aquisition
- UPS breakers math
- PV Breaker controll
- MCB & PV Breakers restriction power
- Validation
- Grid || request
- PV breakers restriction
- PV request Mains/Gen
- PV req step protection
- PV Q request

## User setpoints
- PV setpoints
- Short Del
- MAN PV P req
- Max Export 1 transf
- IM1
- IM2
- IM3
- Max AP
- Max RP
- MIN RP
- Total
- St-by
- Work
- Online
- Nominal
- Actual P
- Actual Q
- P Site
- Q Site
- PF site
- PV P req
- MCB1
- ON/OFF
- MCB2
- MCB3
- PV process setpoints
- PV Interpol X1
- PV interpol X2
- PV Q min import
- MAN PV Q req
- MCB and GCB close exp request
- PV req step protetion
- PV Prod > PV req
- PV Prod > PV req del PV prod > PV req Warning AHI Del
- Reverse power Warning AHI Del
- UPS 1 Load Shedd ON
- UPS 1 Load Shedd OFF
- UPS 2 Load Shedd ON
- UPS 2 Load Shedd OFF
- UPS1 ON command delay

## History - text records (mode / setpoint / forced value / group events)
- 2026-09-29 13:37:13  2 - 1
- 2026-09-29 13:37:13  Forced Value(1) ON
- 2026-09-29 13:37:57  Forced Value(1) OFF
- 2026-09-30 10:01:06  2 + 1
- 2026-09-30 11:53:05  2 - 1
- 2026-09-30 11:53:05  Forced Value(1) ON
- 2026-09-30 11:54:41  Forced Value(1) OFF
- 2026-09-30 13:06:25  2 + 1
- 2026-09-30 18:06:28  2 - 1
- 2026-09-30 18:06:28  Forced Value(1) ON
- 2026-09-30 18:10:49  Forced Value(1) OFF
- 2026-10-01 08:06:35  U=0,T=ETH CA2,CON(8315)=0
- 2026-10-01 08:06:57  Config loaded
- 2026-10-01 08:06:58  2 + 1
- 2026-10-01 08:06:59  2 - 1
- 2026-10-01 08:07:53  Config loaded
- 2026-10-01 08:07:54  2 + 1
- 2026-10-01 08:07:56  2 - 1
- 2026-10-01 08:13:53  U=255,T=ICD,CON(8315)=2
- 2026-10-01 08:31:30  2 + 1
- 2026-10-01 08:42:58  2 - 1
- 2026-10-01 08:50:18  Forced Value(1) ON
- 2026-10-01 08:50:41  2 + 1
- 2026-10-01 08:50:41  Forced Value(1) OFF
- 2026-10-01 09:01:16  2 - 1
- 2026-10-01 09:06:37  Forced Value(1) ON
- 2026-10-01 09:06:46  2 + 1
- 2026-10-01 09:06:46  Forced Value(1) OFF
- 2026-10-01 11:39:19  U=0,T=ETH CA1,CON(14384)=20
- 2026-10-01 11:40:10  U=0,T=ETH CA1,CON(14384)=15
- 2026-10-01 11:43:13  U=255,T=SYNC,CON(9584)=50
- 2026-10-01 11:44:14  U=255,T=SYNC,CON(9584)=210
- 2026-10-01 11:45:25  U=0,T=ETH CA1,CON(14384)=25
- 2026-10-01 16:22:09  2 - 1
- 2026-10-01 16:22:09  Forced Value(1) ON
- 2026-10-01 16:22:48  Forced Value(1) OFF
- 2026-10-03 09:37:21  2 + 1
- 2026-10-03 18:00:29  2 - 1
- 2026-10-03 18:00:29  Forced Value(1) ON
- 2026-10-03 18:01:14  Forced Value(1) OFF
- 2026-10-04 07:01:23  U=0,T=ETH CA1,FaultReset command
- 2026-10-04 09:50:35  2 + 1
- 2026-10-04 10:22:50  2 - 1
- 2026-10-04 15:53:53  2 + 1
- 2026-10-04 16:07:25  U=255,T=ICD,CON(8315)=0
- 2026-10-04 16:07:29  U=255,T=ICD,CON(8315)=2
- 2026-10-04 18:04:12  2 - 1
- 2026-10-04 18:04:12  Forced Value(1) ON
- 2026-10-04 18:04:46  Forced Value(1) OFF
- 2026-10-05 10:30:18  2 + 1
- 2026-10-05 12:17:09  U=0,T=ETH CA1,FaultReset command
- 2026-10-05 12:21:14  U=0,T=ETH CA1,FaultReset command
- 2026-10-05 12:21:16  U=0,T=ETH CA1,FaultReset command
- 2026-10-05 13:01:36  U=0,T=ETH CA1,FaultReset command
- 2026-10-05 13:24:30  2 - 1
- 2026-10-05 13:24:30  Forced Value(1) ON
- 2026-10-05 13:25:06  Forced Value(1) OFF
- 2026-10-06 09:10:58  2 + 1
- 2026-10-06 14:33:04  2 - 1
- 2026-10-07 09:27:27  2 + 1
- 2026-10-07 09:43:40  U=255,T=ICD,CON(8315)=0
- 2026-10-07 09:43:45  U=255,T=ICD,CON(8315)=2
- 2026-10-07 14:16:52  2 - 1
- 2026-10-07 14:16:52  Forced Value(1) ON
- 2026-10-07 14:17:44  Forced Value(1) OFF
- 2026-10-07 16:05:27  2 + 1
- 2026-10-07 16:29:12  U=255,T=ICD,CON(8315)=0
- 2026-10-07 16:29:16  U=255,T=ICD,CON(8315)=2
- 2026-10-07 18:06:54  2 - 1
- 2026-10-07 18:06:54  Forced Value(1) ON
- 2026-10-07 18:07:21  Forced Value(1) OFF
- 2026-10-08 09:35:01  2 + 1
- 2026-10-08 09:40:38  U=255,T=ICD,CON(8315)=0
- 2026-10-08 09:40:43  U=255,T=ICD,CON(8315)=2
- 2026-10-08 18:08:54  2 - 1
- 2026-10-08 18:08:54  Forced Value(1) ON
- 2026-10-08 18:09:49  Forced Value(1) OFF
- 2026-10-09 08:23:26  2 + 1
- 2026-10-09 11:14:17  U=255,T=ICD,CON(8315)=0
- 2026-10-09 11:14:21  U=255,T=ICD,CON(8315)=2
- 2026-10-09 18:14:52  2 - 1
- 2026-10-09 18:14:52  Forced Value(1) ON
- 2026-10-09 18:17:32  Forced Value(1) OFF
- 2026-10-10 07:01:18  2 + 1
- 2026-10-10 07:05:19  U=255,T=ICD,CON(8315)=0
- 2026-10-10 07:05:24  U=255,T=ICD,CON(8315)=2
- 2026-10-10 09:36:13  U=0,T=ETH CA1,FaultReset command
- 2026-10-10 09:46:22  U=0,T=ETH CA1,FaultReset command
- 2026-10-10 10:01:01  U=0,T=ETH CA1,FaultReset command
- 2026-10-10 10:21:21  U=0,T=ETH CA1,FaultReset command
- 2026-10-10 18:04:46  2 - 1
- 2026-10-10 18:04:46  Forced Value(1) ON
- 2026-10-10 18:05:27  Forced Value(1) OFF
- 2026-10-11 05:56:08  2 + 1
- 2026-10-11 05:58:35  U=255,T=ICD,CON(8315)=0
- 2026-10-11 05:59:43  U=255,T=ICD,CON(8315)=2
- 2026-10-11 18:01:14  2 - 1
- 2026-10-11 18:01:14  Forced Value(1) ON
- 2026-10-11 18:03:10  Forced Value(1) OFF
- 2026-10-12 06:36:01  2 + 1
- 2026-10-12 17:57:32  2 - 1
- 2026-10-13 06:13:27  2 + 1
- 2026-10-13 11:00:43  U=255,T=ICD,CON(8315)=0
- 2026-10-13 11:01:03  U=255,T=ICD,CON(8315)=2
- 2026-10-13 18:06:22  2 - 1
- 2026-10-13 18:06:22  Forced Value(1) ON
- 2026-10-13 18:06:52  Forced Value(1) OFF
- 2026-10-14 06:12:01  2 + 1
- 2026-10-14 08:04:57  U=255,T=ICD,CON(8315)=0
- 2026-10-14 08:14:26  U=255,T=ICD,CON(8315)=2
- 2026-10-14 09:19:40  U=255,T=ICD,CON(8315)=0
- 2026-10-14 09:20:57  U=255,T=ICD,CON(8315)=2
- 2026-10-14 09:23:30  U=255,T=ICD,CON(8315)=0
- 2026-10-14 09:27:01  U=255,T=ICD,CON(8315)=2
- 2026-10-14 09:37:34  U=255,T=ICD,CON(8315)=0
- 2026-10-14 09:38:54  U=255,T=ICD,CON(8315)=2
- 2026-10-14 12:25:17  U=255,T=ICD,CON(8315)=0
- 2026-10-14 12:25:52  U=255,T=ICD,CON(8315)=2
