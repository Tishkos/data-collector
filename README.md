# Plant Data Collector — Erbil Coca-Cola hybrid plant

One folder, one click. Reads the plant (ComAp InteliNeo, Sineng inverters through the
SAU gateway, meter), stores every value locally, and shows a live dashboard with
Excel / CSV export. Runs on the plant PC. No cloud, no subscription, nothing
written to any device.

```
START.bat            -> installs (first time) and starts collector + dashboard, opens the browser
START.bat -Plant     -> same, with the REAL plant config (after the addresses are filled in)
STOP.bat             -> stops it
OPEN-DASHBOARD.bat   -> http://localhost:8080
```

The first start installs Python (if missing) and the packages; it needs internet once.
After that it runs offline.

## What you get

- **Dashboard** at `http://localhost:8080` (also from any PC on the plant LAN via `http://<pc-ip>:8080`):
  load / solar / gensets / battery tiles, 24 h power chart, battery SOC, every device
  with its live values and quality, active faults, event log, all points table.
- **Export**: pick a date range and step → **Excel** (Data sheet + Summary sheet) or **CSV**;
  one-click **daily report** Excel.
- **Data** kept in `data\telemetry.sqlite` (every value, with timestamp and quality) and
  `data\audit.jsonl`.
- **Grafana** (optional, needs Docker Desktop): `grafana\docker-compose.yml` → `http://localhost:3000`.

## Two configs

| File | Use |
|---|---|
| `config\site.yaml` | **Simulator.** Works immediately, no hardware. For demos and testing the dashboard. |
| `config\site.plant.yaml` | **Real plant.** Read-only polling of the InteliNeo (192.168.0.112) and the SAU (192.168.0.128). Register addresses are `TODO` until the two documents below arrive. |

### To go live (what is still needed from the site)

1. **InteliNeo Modbus register list**: InteliConfig → Modules → *Modbus Registers* tab (export or screenshots),
   plus the controller's Modbus unit id → fill `profiles\intelineo_master.yaml` and `connection.unit_id`.
2. **SAU point table** for the inverters (SAU web page → settings/communication, or the Sineng Modbus
   protocol for the inverter model), plus each inverter's unit id on the SAU → fill `profiles\inverter_sineng.yaml`
   and the `unit_id`s in `config\site.plant.yaml`.
3. Run `START.bat -Plant`, compare every value with the SAU web page / controller screen, fix scale or sign, done.
4. Install as a background task so it survives reboots: PowerShell as Administrator →
   `.\scripts\install-service.ps1 -Plant`

## Folder map

```
START.bat / STOP.bat / OPEN-DASHBOARD.bat   one-click scripts
scripts\run.ps1                             what START.bat runs (Python, venv, packages, start)
scripts\install-service.ps1                 run at boot as a Windows scheduled task
config\site.yaml, site.plant.yaml           simulator / real plant
profiles\*.yaml                             device register maps (TODO = placeholder)
profiles\tag_catalog.yaml                   the 305 DT-Turkey tag names mapped to our points
docs\turkey\*.xlsx                          the two tag-list workbooks (Rev.1.2) for the Turkey team
docs\DATA_FLOW.md, SAFETY.md, INFO_NEEDED.md
grafana\                                    optional Grafana (Docker)
tools\sau-diag-kit\                         read-only diagnostics for the SAU / InteliNeo link
qs_hybrid\                                  the engine (synced from the development repo qs-ai-hybird)
tests\                                      python -m pytest -q
```

## Safety

- The plant config runs in `restricted` mode: the software cannot write to any device.
- Polling is one block per device every 30 s. The SAU already struggles with the InteliNeo's
  polling; this adds a small, measured load and nothing else should poll the SAU directly.
- Stop at any time with `STOP.bat`; uninstall by deleting the folder. Nothing else on the PC is touched.

## Development

The engine lives in `Tishkos/qs-ai-hybird`. To pull its latest version into this repo:
`scripts/sync_from_dev.sh ../qs-ai-hybird`.
