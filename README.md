# Plant Data Collector — Erbil Coca-Cola hybrid plant

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
tools\neo_archive.py                reads the readable parts of a controller archive (.aig4), read-only
tools\neo_plc.py                    lists and executes the controller's PLC program from the sheet exports
tools\neo_plc_change01.py           builds the modified sheets of change 01 (docs\changes) in InteliConfig's export format
tools\plant_model.py                runs a day of the plant under today's settings and proposed changes
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
