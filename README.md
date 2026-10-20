# Plant Data Collector — Erbil Coca-Cola hybrid plant

New to this work? Read `docs\HANDOVER.md` first: what is known, what is assumed, the rules, and what comes next.

One folder, one click. Reads the plant (ComAp InteliNeo, Sineng inverters through the
SAU gateway, the mains incomer, the meter) once an hour, keeps every value on the plant PC,
shows a dashboard, and exports Excel / CSV — including the tag list DT Turkey asked for.

It only **reads**. There is no code path in this program that writes to any device.

```
START.bat            demo plant (simulated data) — try the dashboard and the exports now
START-PLANT.bat      the real plant (config\site.plant.yaml), read-only polling
CHECK-PLANT.bat      one poll of the real plant, every point printed — the commissioning tool
STOP.bat             stop the collector
UNINSTALL.bat        remove it from Windows startup (the data stays)
OPEN-DASHBOARD.bat   http://localhost:8080
RUN-IN-WINDOW.bat    run in a console window instead of as a Windows task (shows every message; stops when closed)
PROBE-PLANT.bat      which controllers answer on Modbus TCP at all (one read of one register each)
PLC-LAB.bat          the plant and the master's PLC program live in the browser (model only, touches nothing): http://localhost:8090
TRACE-PLANT.bat      fast read-only trace to data\trace: 1 s master, 5 s SAU, response times, event log (Ctrl+C stops)
SESSION.bat          catches every SAU stall with no load on the SAU: master every second, SAU only pinged, Wireshark capture kept around every comm fail, report and zip in bundles\
```

If port 8080 is taken by another program, the dashboard takes the next free port and writes its
address to `data\dashboard.url`; the START script and `OPEN-DASHBOARD.bat` read it from there.
The Modbus port for Turkey is never moved (it is what they were told); if it cannot start, the
collector runs without it, says so on the Settings page, and retries every minute.

The first start installs Python (if missing) and the packages, so it needs internet once.
It then registers the collector as a Windows task that **starts with Windows and restarts
itself if it fails**: shutting the PC down is fine, everything collected so far is in
`data\telemetry.sqlite` and collection continues at the next boot. The click asks for
administrator rights once (for the Windows task); if that is refused it still runs, in the
window, until the window is closed.

## What you get

- **Dashboard** at `http://localhost:8080` (also from any PC on the plant LAN at `http://<pc-ip>:8080`):
  live power flow (grid / solar / gensets → load), today's solar share, fuel, grid hours,
  trends over 6 h … 30 days, every device with its live values and data quality, event log
  (grid lost/back, device not answering, controller alarms), all points with their register.
- **Export**: pick a window and a step → **Excel** (Data + Summary + Points sheets) or **CSV**.
  One click for the **daily report** workbook and for the **DT Turkey tag list with live values**.
- **API** (documented at `http://localhost:8080/api/docs`): `/api/v1/tags` serves the 305
  Turkey tags with value, quality and timestamp; `/api/v1/local/series`, `/export.csv`, … serve the rest.
- **Modbus TCP server for the CCI / DT Turkey platform**: the same 305 tags at fixed holding registers
  (`docs\TURKEY_MODBUS.md`, also at `/api/v1/tags/registers`), refreshed after every site read, read-only.
  Their platform polls this PC over the VPN; the field devices are never polled from outside.
  Switched on with `publish.modbus_tcp.enabled: true` in `config\site.plant.yaml`; the filled-in tag
  lists for Turkey are in `docs\turkey\` (regenerate with `python tools\turkey_register_map.py --host <vpn-ip>`).
- **Data** in `data\telemetry.sqlite`: every reading with timestamp and quality (good / stale / bad).
  A bad read is stored as bad, never as a zero.
- **Grafana** (optional, needs Docker Desktop): `grafana\docker-compose.yml` → `http://localhost:3000`.

## Two configs

| File | Use |
|---|---|
| `config\site.yaml` | **Demo.** Simulated grid + solar + 3 gensets + meter; government-grid outages included. No hardware, no network traffic. |
| `config\site.plant.yaml` | **Real plant.** Read-only Modbus polling of the InteliNeo (192.168.0.112, unit 1 - mapped from the controller's own Modbus export) and the ten inverters through the SAU (192.168.0.128 - registers pending its point table), **once an hour** (`poll_interval_s: 3600`). |

### To go live

1. **Plant PC on the ComAp network**: the port with the ComAp switch cable gets a fixed address
   `192.168.0.200`, mask `255.255.255.0`, no gateway (done on 14 Oct on the USB dongle; at
   installation move the cable to a built-in LAN port and set the same address there).
2. `CHECK-PLANT.bat`: the InteliNeo block must come back `good` with `bus_frequency_hz` ≈ 50 and
   `power_kw` equal to the kW on the controller screen (if the values look shifted by one register, set
   `address_offset: -1` on `neo-01`). The inverters show `registers pending` until step 3.
3. **SAU Modbus TCP point table** (SAU-WEB → Setting with the advanced login, or Sineng support) →
   add `address` / `kind` / `scale` next to each `sau_id` in `profiles\inverter_sineng.yaml`, run
   `CHECK-PLANT.bat` again, compare with the SAU's Device Monitor page.
4. `START-PLANT.bat`. Done. The Turkey platform reads the Modbus server (`docs\TURKEY_MODBUS.md`)
   over the VPN, or `http://<pc-ip>:8080/api/v1/tags` / the exported file.

Changing the poll rate: the dashboard's **Settings** page (30 min / 1 h / 2 h, never more often, saved in `data\settings.json`), or `poll_interval_s` in `config\site.plant.yaml`. "Read devices now" in the top bar reads everything once, at any time.

## The measuring session, one click (passive: no load on the SAU)

`SESSION.bat` is for us, to catch the SAU's stalls whenever they happen, for as long as it runs, without adding
anything to the SAU's load. `CHECK-PLANT.bat` / `START-PLANT.bat` are the collector for the Turkey platform; they
have nothing to do with this.

What it does, logged to `data\session_<date>.log`:

- the master (InteliNeo) every second: its PV logger comm-fail bits give every fail to the second, with its length;
- a ping of the SAU and of the master every second, and the MAC behind each address every 10 s. A ping is
  answered by the SAU's network stack, not by its Modbus program, so it costs the SAU nothing that matters and
  tells "box alive, program late" apart from "box or network gone";
- **the SAU is never read and never connected to** by this program (the probe skips it, there is no check);
- when Wireshark is installed on the PC: Wireshark's own `dumpcap` writes the SAU's traffic (filter
  `host 192.168.0.128`) into a ring in `data\capture\`, one file per hour, two days kept, on the adapter that leads
  to the SAU (found by itself; `--capture-if "Ethernet 3"` to force one). Around every comm fail the files are
  copied to `data\evidence\<date>\` so the ring never overwrites them. Capturing only listens;
- after the first hour, then every day at 05:00 plant time: `data\trace\report_<date>.md` and a zip in `bundles\`
  with the files, the report and the kept capture files. The report has, per comm fail: whether the SAU answered
  pings, and from the capture whether the SAU's TCP acknowledged the master's requests at once and how late its
  Modbus answers came, or with which exception (06 Server Device Busy). That is the evidence for Sineng.

The capture sees the master's requests to the SAU only when the switch (or the MikroTik) **mirrors the SAU's port**
to the PC's port. Without a mirror it sees this PC's own traffic to the SAU and the report says so; the master's
fail times and the pings are still there. Do not run Wireshark's own window on the same capture at the same time
for days: it keeps everything in memory.

The older active session (it reads the SAU every 5 s with a guard) is still there for a short, supervised test:
`python -m plantcollector.cli --config config\site.plant.yaml --data data session --mode active --hours 1`.

## Event studies: the trace

The hourly collector is for the Turkey platform. For questions like "what happens in the ten seconds of a
PV logger comm fail", `TRACE-PLANT.bat` runs a separate, read-only trace in a console window: the master
every second, the SAU array every five seconds (the setpoint registers the master writes are read back word
by word), the response time of every read, a fresh-connection test every minute, and an event log of every
bit that changes. Files land in `data\trace\` (one set per day, CSV), nothing goes into the database.
Other intervals: `python -m plantcollector.cli --config config\site.plant.yaml --data data trace --every neo-01=1 --every pv-array=1`.
The SAU allows five Modbus TCP links in all and the master uses two, so one trace at a time.

## The PLC lab

`PLC-LAB.bat` (or `python tools\plc_lab.py`) runs the plant model one second at a time with the master's real
PLC program inside it and shows every sheet with live pin values, like the controller's own PLC monitor, on a
model you can poke: a comm fail on one or both sessions, a cloud, a load step, the operator's OFF toggle, a
grid transfer, a fault reset, two to four gensets, power management on or off, and the switch between today's
program and the change-01 variants. A strip chart shows the last 15 minutes. It never connects to the plant.

What is exact in it: the program at its 100 ms cycle, the setpoints, the session refresh and timeout figures from
the archive, the load anchors and grid hours from the history, the sun curve for the date. What is modelled, and
adjustable on the page: how fast the inverters follow a setpoint, the controller's curtailment regulator, whether a
write lands in the SAU while it gives no replies, load noise, cloud edges. A comm fail is injected as "the SAU stops
answering" and the flag rises the way the module declares it, after the timeout and the retries, so a two-second
hiccup never raises it. **Replay mode** removes the modelling: `python tools\plc_lab.py --replay data\trace\neo-01_<date>.csv
--replay-sau data\trace\pv-array_<date>.csv` runs the program on the values the trace recorded on the plant and draws
the recorded setpoint next to the computed one, with today's program or either change-01 variant.
Scenarios on the page (today by hand, change 01 v3, change 01 v2 with stage 2, power management AUTO, Gen P Min
20 %, AUTO with the PLC and UPS fixes, two gensets in the sun, AUTO with two sets minimum, a grid day) each start
from zero with a fresh program and plant; "reset" does the same for the current one. Counters since the start:
solar used, available and thrown away, genset kWh, fuel (model), comm fails, setpoints forced to 0, warnings,
UPS sheds, min and max gen load, seconds in reverse power, reverse power trips, minutes with the plant dark.

The previous integrator's example is a button: "link lost, load drops 800 kW" (and 1,200 kW, and the one-session
variant). The gensets' reverse power protection is in the model as an assumed 5 % for 5 s until the IG4 settings are
read; a trip opens the gensets and the plant goes dark for three minutes, so it counts. Random production steps
(chillers, lines) come and go on their own, size and rate adjustable, so the test is not hand-picked. Hovering a
line names it with its value at that second.

A ninth scenario tries the fuel idea directly: one genset by day with the UPS as the buffer and the next set on
reserve. The gensets got an assumed overload protection (110 % for 10 s), started sets need an assumed 30 s before
they take load, and each UPS feeds an assumed 300 kW; all three are fields on the page until the plant confirms
them. The SAU ramp is set to 5 kW/s, the climb measured on 14 October after breaker closes, and the model's
curtailment regulator no longer winds up while the PV is off or unseen (the history shows the request restarting
low after a breaker close).

## Folder map

```
START.bat, START-PLANT.bat, CHECK-PLANT.bat, STOP.bat, UNINSTALL.bat, OPEN-DASHBOARD.bat, RUN-IN-WINDOW.bat
scripts\run.ps1                     what the START scripts run (Python, packages, Windows task, browser)
scripts\stop.ps1, uninstall-service.ps1
config\site.yaml                    demo
config\site.plant.yaml              real plant (read-only Modbus)
profiles\*.yaml                     device point maps (TODO = placeholder register)
profiles\tag_catalog.yaml           the 305 DT Turkey tag names mapped to our device/point ids
plantcollector\                     the program: adapters (modbus_tcp, inteliscada, simulator),
                                    store (SQLite), views, export (xlsx/csv), api (FastAPI), web (dashboard),
                                    publish (the read-only Modbus TCP server for Turkey)
docs\turkey\*.xlsx                  the two tag-list workbooks (Rev. 1.2) from DT Turkey
docs\DATA_FLOW.md                   how the data moves and how Turkey gets it
docs\TURKEY_MODBUS.md               the Modbus register map served to the Turkey platform
docs\DEVICES.md                     what the manuals and the site sessions say, and what it means for us
docs\site\                          exports taken on site (InteliNeo Modbus table, archive extract, PLC sheets, history of 2026-10-14)
docs\NEO_CONFIGURATION.md           how the InteliNeo configuration works (PLC program, devices, history, settings)
docs\SIMULATION.md                  the behaviour model: what is exact, what is assumed, scenarios and results
docs\plant_overview.svg             one-line and communication drawing of the plant, with what is still to verify
tools\neo_archive.py                reads the readable parts of a controller archive (.aig4), read-only
tools\neo_plc.py                    lists and executes the controller's PLC program from the sheet exports
tools\neo_plc_change01.py           builds the modified sheets of change 01 (docs\changes) in InteliConfig's export format
tools\plant_model.py                runs a day of the plant under today's settings and proposed changes
tools\plant_diagram.py              draws docs\plant_overview.svg
tools\plc_lab.py                    the PLC lab: the plant and the program live in the browser
tools\win_inspect.py                lists InteliConfig's windows and controls (first step of driving its read-only chores)
docs\HANDOVER.md                    read this first: everything known, assumed, decided and open
plantcollector\trace.py             the trace and the probe (read-only, own files)
plantcollector\session.py           the one-click session: steps, the SAU guard, daily report and zip
plantcollector\report.py            turns a day of trace files into the numbers (fails, SAU latency, words, regulator, load steps)
bundles\                            the zips the session makes, one per day
docs\site\visit_checklist.md        the read-only export steps for a site or remote session
tools\neo_history.py                turns a History export into CSV tables
tools\turkey_register_map.py        regenerates that map and fills the Turkey workbooks
docs\SAFETY.md                      why it cannot touch the plant
docs\INFO_NEEDED.md                 the open questions for the site
grafana\                            optional Grafana (Docker) on the same database
tools\sau-diag-kit\                 read-only diagnostics for the SAU / InteliNeo link
tests\                              python -m pytest -q
```

## Command line (optional)

```
.venv\Scripts\python -m plantcollector.cli --config config\site.plant.yaml check
.venv\Scripts\python -m plantcollector.cli --config config\site.plant.yaml export --hours 168 --step 3600 --out week.xlsx
.venv\Scripts\python -m plantcollector.cli --config config\site.plant.yaml report --date 2026-10-12
```
