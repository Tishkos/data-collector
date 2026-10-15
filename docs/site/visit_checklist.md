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
Screenshots or export of:
- [ ] firmware version
- [ ] Modbus TCP server settings, above all the maximum number of client connections
- [ ] RS485 settings and statistics to the inverters (baud rate, poll cycle, error counters)
- [ ] power control / ramp settings, communication watchdog or fallback when the master goes silent
- [ ] event or alarm log
- [ ] network settings of ETH0 and ETH1
- [ ] one inverter's parameter page (frequency response, ramp)
- [ ] ask the installer or Sineng for the **SAU-100 Modbus TCP point table** document

## The plant PC
- [ ] `PROBE-PLANT.bat` - one read of one register per controller; shows who answers on Modbus TCP at all
- [ ] `CHECK-PLANT.bat` - one poll, compare the values with the screens
- [ ] `START-PLANT.bat` - hourly collector (master, three mains, SAU array)
- [ ] `TRACE-PLANT.bat` in its own window for the event study - leave it running; `data\trace\` fills up,
      about 30 MB a day at 1 s. Watch the master's comm-fail count before and after starting it.
- [ ] at the end: copy `data\` to the stick

## At the panels (photos)
- [ ] the two UPS nameplates and the cables in and out of them
- [ ] the master's terminals for UPS1 ON/OFF, UPS2 ON/OFF and the two UPS feedbacks (terminal numbers from the
      archive's I/O list) and where those wires go
- [ ] genset nameplates and engine type plates
- [ ] the switch the master, the SAU and the PC sit on, and the cable path between them

## To ask
- [ ] why the gensets run by hand since 1 October
- [ ] what the two mode changes on normal days are for
- [ ] what the UPS feed, new or existing, make and model, autonomy
- [ ] production hours of the lines and chillers
- [ ] single-line diagram, fuel log (deliveries and daily tank readings)

## At the end
- [ ] screenshot of the HMI state and the time
- [ ] everything in one folder, zipped, one folder per device, named with the date
