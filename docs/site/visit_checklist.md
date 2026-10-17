# Site / remote session checklist (read-only)

Everything on this page reads. Nothing is written to any device, nothing is restarted, no setpoint is touched.
In InteliConfig: never **Write**, never **Program**, never **OK and Restart**. If a controller asks for a
password to open its configuration, stop and ask the plant.

## Before anything
- [ ] Screenshot of the HMI state (mode, running sets, breakers) and the time
- [ ] Say it once to the plant engineer: reading only, nothing changes today
- [ ] Is InteliConfig on the PC you are using? If not, the plant exports with its own laptop while you guide

## The eight controllers (about 10 minutes each)
Gensets IG4 200: 192.168.0.105, .106, .113, .114, .115 - Mains IM210 G2: 192.168.0.107, .111, .110

For each one, in InteliConfig, exactly as done for the master on 14 October:
- [ ] connect by IP, open the configuration from the controller, **save the archive** to a file
- [ ] **export the Modbus register list** (same export as the master's `neo_modbus_table`)
- [ ] **export the history**
- [ ] screenshots: genset protection setpoints (reverse power threshold and delay, overload), mains protection
      on the IMs, and the communication / Modbus server settings on all
- [ ] file names: `<gen1|gen2|...|main1|main2|main3>_<what>_<yyyy-mm-dd>.*`, one folder per controller

## The SAU-100 (web page at 192.168.0.128, the plant's login)
**Downloading or exporting a log from the SAU page has made the SAU stop answering the master and dropped the
solar (5-6 Sep, reproduced with Modbus Poll). While the sun is up: screenshots only, one page at a time, no
downloads, no exports, no "delete record", and write down the clock time of every page you open so the trace
can show whether the fail rate moved while the page was open.** Exports only after sunset and with the plant
engineer's yes.
Screenshots of:
- [ ] firmware version (the application is "SmartCMS"; the watchdog is "SauGuard")
- [ ] the system status page: CPU load (1/5/15 min), memory, the message queue number (22,487 was seen at a fail)
- [ ] Device Monitor > Communication diagnostic information: **Network interface** tab (RX/TX errors, dropped,
      collisions, speed and duplex of ETH0 and ETH1) - the most valuable single screenshot; then Network
      Connections (send queues of the :502 sessions, how many clients), Routing table
- [ ] in the real-time RS485 log: count per minute the broadcast frames `00 10 FE D1 ...` and compare with how
      often the master's setpoint changes (every write may become a broadcast plus a wait on the bus)
- [ ] active power control mode setting (66900: Remote scheduling expected), the anti-reflux parameters
      67191-67214 if shown, and what 192.168.1.50 is in the forwarding / platform / NTP settings
- [ ] Modbus TCP server settings, above all the maximum number of client connections
- [ ] RS485 settings and statistics to the inverters (baud rate, poll cycle, **response delay**, error counters,
      the communication success rate per inverter)
- [ ] the real-time RS485 log page (Send/Recv frames, timeouts) while it runs: screenshots, and count the timeouts per minute
- [ ] the device list with the communication status of every device: inverters, meters, PID 101/102, RMU 202,
      and the two without a model (200 "PDC#2 UPS", 201 "PDC#1 dehumidifier"), the weather station
- [ ] the history log with the status flips and the "AC voltage low / abnormal" flags (no export by day)
- [ ] the nameplate: SAU-100 or SAU-300 (the support email says SAU-300, protocol SAU300U211)
- [ ] power control / ramp settings, the two "valid delay" 3,000 ms settings, communication watchdog or
      fallback when the master goes silent
- [ ] event or alarm log
- [ ] network settings of ETH0 and ETH1, and **look** whether a cable is plugged into ETH1 and where it goes
      (two ports of one box on one switch is a known cause of intermittent unreachability) - look, do not unplug
- [ ] one inverter's parameter page (grid protection limits, frequency response, ramp)
- [ ] ask the installer or Sineng for the **SAU-100 Modbus TCP point table** document

## The MikroTik router (RB4011) and the switch
- [ ] export the Netwatch log (SAU up/down events with times) and the ping statistics page; it is an
      independent witness of the SAU stalls and costs the plant nothing
- [ ] the port counters of the port the SAU side comes in on (rx/tx errors, FCS errors, drops) and of the
      master's port; the switch's port counters too if it is managed
- [ ] if the router can sniff (Tools > Packet Sniffer, stream to the PC), a capture of master <-> SAU during the
      session is free and touches nothing; `tools/pcap_modbus.py` reads it
- [ ] note the router's own clock against the PC clock
- [ ] photos of the media converters fitted on 24 Sep and their management IPs (an IP conflict until 26 Sep)

## Files that already exist (ask the solar engineer, no new work)
- [ ] `SauGuard.txt`, `SmartCMSHU121.txt`, `Device data service.txt` (SAU logs of 6-7 Sep)
- [ ] `20260908SAU-TCP.pcapng` (capture at the SAU, 8 Sep) and the 10 Oct Wireshark capture
- [ ] `Problem SAU 300.pdf`, the daily report PDFs, the inverter alarm history exports (CSV / XLS / zip)
- [ ] `power sld salah.pdf`, the MV station SLD, `Si-Box Diesel microgrid configuration Guide.pdf`

## The plant PC
- [ ] `SESSION.bat` in its own window and leave it: probe, check, 30 min master only, then the SAU every 5 s
      plus the stall catcher (one ping and one one-register read of the SAU every second, on the same link),
      report and zip in `bundles\` after two hours, then the lighter long run with a zip every morning.
      It watches the master's comm-fail rate itself and drops the SAU if the rate rises. The report gives a
      verdict per comm fail: SAU unreachable / alive but silent / slow / answered us normally.
- [ ] `START-PLANT.bat` - the hourly collector for the Turkey platform (separate from the session)
- [ ] note the comm-fail count in the master's history at the start and at the end (the session logs its own too)
- [ ] at the end: copy the newest zip from `bundles\` (and `data\` if there is room) to the stick

## At the panels (photos)
- [ ] the two UPS nameplates and the cables in and out of them
- [ ] the master's terminals for UPS1 ON/OFF, UPS2 ON/OFF and the two UPS feedbacks (terminal numbers from the
      archive's I/O list) and where those wires go
- [ ] genset nameplates and engine type plates
- [ ] the switch the master, the SAU and the PC sit on, and the cable path between them

## To ask
- [ ] why the gensets run by hand since 1 October
- [ ] what the two mode changes on normal days are for (the 14 Oct ones were meter calibration)
- [ ] what the UPS feed, new or existing, make and model, autonomy
- [ ] production hours of the lines and chillers; the plant says the load is stable with no rush current -
      the trace's one-minute load falls will show it
- [ ] single-line diagram, fuel log (deliveries and daily tank readings)
- [ ] the HMI clock against local time (the controller ran one hour behind on 10 Oct)
- [ ] does the dehumidifier in the PV station cabinet run at all; how hot is the SAU's cabinet in the afternoon
- [ ] whether Sineng's R&D came back after 11 Oct, and whether a newer SAU firmware exists

## At the end
- [ ] screenshot of the HMI state and the time
- [ ] everything in one folder, zipped, one folder per device, named with the date
