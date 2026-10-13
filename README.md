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
- **Data** in `data\telemetry.sqlite`: every reading with timestamp and quality (good / stale / bad).
  A bad read is stored as bad, never as a zero.
- **Grafana** (optional, needs Docker Desktop): `grafana\docker-compose.yml` → `http://localhost:3000`.

## Two configs

| File | Use |
|---|---|
| `config\site.yaml` | **Demo.** Simulated grid + solar + 3 gensets + meter; government-grid outages included. No hardware, no network traffic. |
| `config\site.plant.yaml` | **Real plant.** Read-only Modbus polling of the InteliNeo (192.168.0.112) and the SAU (192.168.0.128), **once an hour** (`poll_interval_s: 3600`). Register addresses are `TODO` until the two documents below arrive. |

### To go live (what is still needed from the site)

1. **InteliNeo Modbus register list**: InteliConfig → Modules → *Modbus Registers* tab (export or
   screenshots), plus the controller's Modbus unit id → fill `profiles\intelineo_master.yaml` and `connection.unit_id`.
2. **SAU point table** for the inverters (SAU web page → settings/communication, or the Sineng Modbus
   protocol for the inverter model), plus each inverter's unit id on the SAU → fill
   `profiles\inverter_sineng.yaml` and the `unit_id`s in `config\site.plant.yaml`.
3. `CHECK-PLANT.bat`: compare every printed value with the SAU web page / controller screen, fix scale
   or sign in the profile, repeat until everything is `good`.
4. `START-PLANT.bat`. Done. The Turkey side reads `http://<pc-ip>:8080/api/v1/tags` or the exported file.

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
                                    store (SQLite), views, export (xlsx/csv), api (FastAPI), web (dashboard)
docs\turkey\*.xlsx                  the two tag-list workbooks (Rev. 1.2) from DT Turkey
docs\DATA_FLOW.md                   how the data moves and how Turkey gets it
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
