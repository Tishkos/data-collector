# What the device manuals say (and what it means for the collector)

From the Sineng SAU-200 / SAU-300 user manuals and the ComAp InteliConfig global guide.
The SAU-100 on site shows the same web application and protocol set as the SAU-200.

## Sineng SAU gateway

| Fact | Where | Consequence for us |
|---|---|---|
| Modbus TCP slave on port 502, **max 5 links** (connections) | SAU-200 §4.1, SAU-300 §4.1 | The collector uses **one** connection per gateway and closes it after every poll. Nothing else should hold connections open. The InteliNeo already uses some of the five. |
| "Supports data exchange between Modbus TCP and RTU"; "communication point table configurable" | §4.1, §2 table 2-2 | The registers the SAU answers on port 502 are the SAU's **configured point table**, not automatically the inverter's native map. The table is set in SAU-WEB (Setting). That table, or Sineng's document for it, is the "SAU point table" we need. |
| IEC 60870-5-104 slave on 2404 (max 10), IEC 61850-MMS on 102, NTP | §4.1 | Other services on the box; not used by us. The Neo and the collector use 502 only. |
| Modbus RTU host on 4 (SAU-200) / 6+4 (SAU-300) RS485 ports, **≤ 12 devices per bus**, unique IDs per bus | §4.2 | The ten inverters sit on one or two RS485 buses with addresses 1…n; those addresses are the unit ids the SAU forwards. |
| Default IPs: Eth0 192.168.20.128, **Eth1 192.168.1.123** | SAU-200 §3.2 | Explains the second address seen in the capture: 192.168.0.128 is Eth0 (changed on site), 192.168.1.123 is Eth1 at its default. |
| "Virtualizes multiple inverters of the same array into a single adjustable power device" | §5.2 | The device the InteliNeo talks to as **unit 245** is this virtual array device: totals in, power limit out. Per-inverter data is behind the individual unit ids. |
| SAU-WEB manages the box; separate document "sau-web operation manual.pdf" | SAU-200 ch. 4 | The Dashboard / Device Monitor / Setting / Logs pages are SAU-WEB. Ask Sineng for that manual too. |
| USB export of logs, statistics and records; "Enjoy Solar" cloud (LINK1 LED) | §2 table 2-2, §3.3 | The SAU may already report to Sineng's cloud; the LINK1 LED on the box tells. |
| Troubleshooting: "check if the point table configuration matches the connected device / data points, data formats" | ch. 6 / ch. 4 | A wrong point table is Sineng's own first suspect for communication errors — relevant to the PV-logger comm-fail history. |

## ComAp InteliNeo / InteliConfig

| Fact | Where | Consequence for us |
|---|---|---|
| Modbus registers are a **user-defined table in the controller configuration**: Controller configuration → Others → MODBUS. Three groups: binary objects 0–999, values 1000–2999, setpoints 3000–3999; one register = 2 bytes, bigger objects take consecutive registers. | InteliConfig guide §5.2.8 "MODBUS", image 5.19 | This table *is* `profiles\intelineo_master.yaml`. It is site-specific, so it has to be read from this controller. |
| The MODBUS window has **Export MODBUS definition to file**; the Tools ribbon has **Generate Cfg image (MODBUS)** | §5.2.8, §4.2.2, §6.3 | Either gives the whole list in one file: register, object, name, type. That is the export to do on the plant PC. |
| Opening the Controller configuration window online **needs the controller password** (Others → Access → Enter password) | §5.5.1 note, §5.4 | The person with the controller password opens it; we only read and export. Nothing is written to the controller unless **OK and Restart** is pressed — never press it. |
| Export to CSV of Setpoints / Values | §6.3.1 | Handy for a snapshot of all live values with names, to check our readings against. |
| Communication plug-in modules (CM-Ethernet: "TCP Modbus or SNMP") appear as a Setpoints group | §5.2.6 | The Modbus TCP server settings of the Neo (port 502, device address) are in Setpoints → the CM-Ethernet / Modbus group. |

## Facts from site screenshots (SAU device list, IP scan, InteliNeo control window)

| Fact | Consequence |
|---|---|
| Inverters: **Sineng SP-xxxK-HU, 12-string version**, 10 units; RS485 addresses 1–5 on COM1, 6–10 on COM2 | unit ids 1…10 confirmed; 12 string currents, not 17 |
| RS485 success rate per inverter **53–77 %** (5/10/30/60-minute windows) | the SAU is retrying inverters all day: a plausible root of the slow Modbus TCP answers and the Neo's "PV logger comm fail". Site item: RS485 wiring, termination, baud/parity, point table |
| On the SAU as well: ABB PowerValue 11 UPS (COM7:2, not answering), Acrel PZ96L-E4 meter (COM4:1) and Sfere PD194E meter (COM4:2) = Meter1 generator feeder / Meter2 grid feeder, Acrel AM5SE-T relay (COM5:1), ARTU100 RTU (COM5:2), Sineng AgilePID (COM7:3), two CSQ devices (COM3:2 not answering, COM3:3 ok) | generator and grid power also exist on the SAU; a second source for mains/genset values |
| Address 1 is used on COM1 (inverter), COM4 (meter) and COM7 (the SAU itself) | the SAU's Modbus TCP side is **not** a plain pass-through by unit id; the TCP point table / forwarding configuration (SAU-WEB → Setting) decides which TCP unit id and registers hold what |
| Network: MikroTik router .1; nine ComAp devices (.105 .107 .110 .111 .112 .113 .114 .115 .167); SAU .128 (ZHIYUAN hardware); plant PC DESKTOP-K287K9O .166; "SOLAR" Lenovo laptop .171 | .112 = InteliNeo (InteliConfig connects there); .171 is probably the licensed InteliSCADA laptop |
| InteliNeo 5500 SN 240842DA, firmware 1.2.0.43, application "Standard HC"; 05:28: gensets 1,859 kW, mains 0, PV 0, bus 416 V / 49.98 Hz | plant peak load is around 1.9 MW; the demo is scaled to this |
| Binary inputs PV1/PV2 feedback, UPS1/UPS2 feedback; binary outputs PV CB1/CB2 ON/OFF, **UPS1/UPS2 ON/OFF** | the UPS control outputs already exist in the configuration — starting point for the UPS logic work |
| Alarms: Wrn PV Logger group 1 and 2 Comm Fail, **Max Num CU on CAN Exceeded** (red), Wrn Production Mode | the 8-controller firmware limit is a live alarm, not only a report finding |
| SAU dashboard: installed capacity 3,200 kW, 10/10 inverters online, 2/2 meters | PV rated 3.2 MW |
