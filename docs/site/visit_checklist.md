# Site / remote session checklist (read-only)

Everything on this page reads. Nothing is written to any device, nothing is restarted, no setpoint is touched.
In InteliConfig: never **Write**, never **Program**, never **OK and Restart**. If a controller asks for a
password to open its configuration, stop and ask the plant.

## Before anything
- [ ] Screenshot of the HMI state (mode, running sets, breakers) and the time
- [ ] Say it once to the plant engineer: reading only, nothing changes today
- [ ] Is InteliConfig on the PC you are using? If not, the plant exports with its own laptop while you guide

## The eight controllers (about 10 minutes each; they do not fit in one hour with everything else)
Gensets IG4 200: 192.168.0.105, .106, .113, .114, .115 - Mains IM210 G2: 192.168.0.107, .111, .110

Order of value: (1) **before 16:00, offline, no plant time**: open the 14 Oct archive copy of the master in
InteliConfig and read the PV logger / ECU module settings and their help text: Response timeout, Pause before
request, retries, and the rule that raises "Comm Fail" and how an exception reply counts - the capture shows
the master abandoning a request after 1.9 s and pairing replies by arrival, so this page decides whether a
timeout change has any mechanism at all. (2) One genset IG4 (.105): reverse power threshold and delay,
overload, nominal, start-to-load. (3) The rest another day. For each one connected, exactly as done for the
master on 14 October:
- [ ] connect by IP, open the configuration from the controller, **save the archive** to a file
- [ ] **export the Modbus register list** (same export as the master's `neo_modbus_table`)
- [ ] **export the history**
- [ ] screenshots: genset protection setpoints (reverse power threshold and delay, overload), mains protection
      on the IMs, and the communication / Modbus server settings on all
- [ ] file names: `<gen1|gen2|...|main1|main2|main3>_<what>_<yyyy-mm-dd>.*`, one folder per controller

## The SAU-100 (web page at 192.168.0.128, the plant's login)
**The page itself is the biggest load the SAU sees** (its dashboard polls five endpoints every 5 s plus a 31 KB
POST every 6 s; a log export froze the whole application for 2.5 s on 8 Sep and dropped the solar). So, while
the sun is up: **two visits of about one minute, at 16:05 and 16:55**, screenshots only, then close the tab.
No download, no export, no "delete record", nothing under Logs. Write down the clock time of each visit. Use a
viewer login or let the plant engineer drive: a high-privilege account changes a setting with one click.
Each visit, in this order (Device Monitor > Communication diagnostic information):
- [ ] **Network Connections**, scrolled so the Remote address column is fully visible: count the :502 sockets and
      their remote addresses (the limit is five; the master holds two, our trace one, 127.0.0.1 one), HTTP
      clients on :80/:8080 and on which port (ETH0 192.168.0.x, ETH1 192.168.1.x), any SYN_SENT, any send
      queue above a few hundred bytes
- [ ] **Network interface**: per port speed/duplex, RX/TX errors, dropped, collisions
- [ ] Routing table and Domain Name Server (is the gateway/DNS reachable; what is 192.168.1.50)
- [ ] the inverter list with the **communication success rate per inverter** (60-min window)
- [ ] Run info once: uptime, load, temperature if shown; firmware/About once
- [ ] the second visit also: the device list with the status of every device (inverters, meters, PID 101/102,
      RMU 202, the two without a model 200/201, the weather station) and the power-scheduling page (active
      power control mode, expected Remote scheduling; the anti-reflux parameters if shown)
After sunset only, with the plant's yes: RS485 settings and statistics (baud, poll cycle, response delay, the
two 3,000 ms "valid delay" settings), the real-time RS485 log (count timeouts and `00 10 FE D1` broadcasts per
minute), the history log, Modbus TCP server settings (max clients), network settings of ETH0/ETH1, one
inverter's parameter page, exports.
- [ ] Only look, never unplug: is a cable in ETH1 and where does it go (ask, or a photo of the switch ports)

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
- [ ] `SESSION.bat` in its own window and leave it: probe, check, 15 min master only, then the SAU every 5 s
      plus the stall catcher (one ping and one one-register read of the SAU every second, on the same link),
      report and zip in `bundles\` after one hour, then the lighter long run with a zip every morning.
      It watches the master's comm-fail rate itself and drops the SAU if the rate rises. The report gives a
      verdict per comm fail: SAU unreachable / alive but silent / slow / answered us normally.
- [ ] `START-PLANT.bat` - the hourly collector for the Turkey platform: start it **after** the hour, or leave the
      scheduled task as it is; it takes one of the SAU's five Modbus links for a few seconds every hour. If the
      16:05 socket count shows five :502 sockets, stop it (STOP.bat) for the day
- [ ] note the comm-fail count in the master's history at the start and at the end (the session logs its own too)
- [ ] once: `arp -a` in a command window (every 192.168.0.x host with its MAC; media converter management IPs; any
      192.168.1.x entry = the two subnets share one switch; .128 should show one MAC only) and
      `tracert 192.168.0.128` (one hop = the router is not routing it); paste both into a text file
- [ ] if a fail happens: write the time, what you had open, and in InteliConfig's value view the two bits
      ECU 2 / ECU 3 Comm Fail, PV logger AP, Max Available, Online/Work/Alarm inverter numbers - how long the
      bits stay up (about 2 s = the abandon-and-mispair mode seen on 8 Sep; tens of seconds = a real outage)
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
