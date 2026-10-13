# Data flow: site collector → VPS → API

One collector on site reads the field devices gently and pushes to the VPS.
Everyone who wants data (DT Turkey monitoring, dashboards, the AI, Excel users)
reads it from the VPS API. **Nobody polls the SAU or the InteliNeo except the
collector.** That is the whole point: the SAU is already overloaded.

```
 plant LAN (192.168.0.x)                          internet                 VPS
 ───────────────────────                          ────────                 ───
 Sineng SAU-100 ──┐                                                   ┌─ TimescaleDB / SQLite
 (10 inverters,   │  Modbus TCP, one block read                       │
  meter, sensors) │  per device every 30 s      HTTPS POST /ingest    │
                  ├──► site collector ──────────────────────────────► │  FastAPI
 InteliNeo 5500 ──┘  (SCADA PC or mini PC)      outbound only,        │   ├─ GET /tags        ← DT Turkey
 (gensets, mains,    store-and-forward spool    X-API-Key             │   ├─ GET /tags/{x}/history
  PV prod/req,       in local SQLite                                  │   ├─ GET /export.csv
  PVCB, comm bits)                                                    │   ├─ Grafana, AI reports, HMI
                                                                      └─ └─ command gateway (separate; see ARCHITECTURE)
```

## Site side: `qs_hybrid.collector.push`

- Reads every device in `config/site.yaml` through the normal adapters (read-only).
- Interval ≥ 10 s (default 30 s). One block read per device, not tag-by-tag.
- Every reading carries value, quality (good/stale/bad) and timestamp. Bad is sent as bad, never as 0.
- Spools batches in `data/spool.sqlite`; pushes oldest-first; keeps going for days without internet.
- Runs as a Windows service / systemd unit. Needs only outbound HTTPS to the VPS.

```
python -m qs_hybrid.collector.push --config config/site.yaml \
    --vps https://<vps>/api/v1/ingest --key <COLLECTOR_KEY> --interval 30
```

## VPS side: `qs_hybrid.api.tags`

| Endpoint | Who | What |
|---|---|---|
| `POST /api/v1/ingest` | collector (`X-API-Key: COLLECTOR_KEY`) | accepts batches, stores readings |
| `GET /api/v1/tags` | readers (`X-API-Key: READ_KEY`) | all external tags, latest value + quality + timestamp |
| `GET /api/v1/tags/{name}` | readers | one tag |
| `GET /api/v1/tags/{name}/history?minutes=1440` | readers | time series, downsampled |
| `GET /api/v1/export.csv` | readers | all tags as CSV |

Keys come from env: `QS_COLLECTOR_KEY`, `QS_READ_KEY`. Put the API behind HTTPS (Caddy/nginx).

## The tag catalog: `profiles/tag_catalog.yaml`

The contract between external tag names and our internal `device/point` names.
Generated from DT Turkey's Rev.1.2 workbooks by `scripts/build_tag_catalog.py`:
305 tags = 10 inverters × 29 signals + 15 plant-level signals.

```yaml
- name: INVERTER_3.INV3_AC_POWER      # what Turkey asked for
  device: inv-03                      # our device id (site.yaml)
  point: ac_power_kw                  # our point id (profiles/inverter_sineng.yaml)
  type: float
  unit: kW
```

Register addresses are NOT in the catalog; they live in the device profiles and are
filled from the SAU point table and the InteliConfig register export. Changing a
register never changes the external API.

## Quality rules (what consumers see)

- `good`: fresh reading from the device.
- `stale`: last good reading older than 5 minutes (collector or link down).
- `bad`: device did not answer / returned an error. `value` is `null`, never a stale number.
- A tag with no data yet returns `value: null, quality: bad, note: "no data yet"`.

## Still needed to go live

1. SAU point table (unit IDs + registers for inverters, meter, sensors) → `profiles/inverter_sineng.yaml`, `meter`, `env`.
2. InteliConfig Modbus register export → InteliNeo points (`pv_logger_comm_fail`, `pvcb_closed`, PV prod/req …).
3. A PC on the plant LAN with outbound HTTPS, to run the collector.
4. VPS with HTTPS, the two API keys, and the plant's written approval.
