# Still needed from the site

The program is complete; these are the numbers that turn the placeholder register maps
into real ones. Each item names the file it goes into.

## 1. ComAp InteliNeo 5500 (site master)
- [ ] InteliConfig → Modules → **Modbus Registers** tab: export (or screenshots) of the register
      list → `profiles\intelineo_master.yaml`
- [ ] The controller's Modbus **unit id** (device address, default 1) → `config\site.plant.yaml` `neo-01.connection.unit_id`
- [ ] Word order of 32-bit values (checked on the first live read; `word_order: little` if values look wild)
- [ ] Names of the per-genset values exposed by the master (G1…G5 power, running, fuel) — if the master does
      not expose them, each genset controller is added as its own device with `profiles\genset_comap.yaml`

## 2. Sineng inverters through the SAU-300
- [ ] Inverter **model** (nameplate photo) and number of string inputs
- [ ] SAU **point table** (SAU web page → communication / Modbus settings, or the Sineng Modbus
      protocol document for the model) → `profiles\inverter_sineng.yaml`
- [ ] Each inverter's **unit id** on the SAU → the ten `unit_id`s in `config\site.plant.yaml`
- [ ] Is a pyranometer / temperature sensor connected to the SAU? Its address → `profiles\pv_sunspec.yaml`
      (the Turkey list has IRRADIANCE, MODULE/AMBIENT TEMPERATURE)

## 3. Mains and meter
- [ ] Where does the plant read mains power and "mains available"? (InteliNeo master values, an
      InteliMains 210, or a separate meter) → `mains_power_kw` / `mains_available` in the InteliNeo
      profile, or a device with `profiles\grid_mains.yaml`
- [ ] LV panel meter: brand/model, address, is it on the SAU or on its own IP → `profiles\meter_site.yaml`

## 4. Plant PC
- [ ] Which PC runs it (the SCADA PC at 192.168.0.x is fine), and that it stays on 24/7
- [ ] A short written OK from Mr Baban / plant management for read-only data collection
- [ ] Whether Turkey may reach `http://<pc-ip>:8080` (VPN) or receives exported files instead

## 5. Nice to have
- [ ] Local diesel price → `fuel_price_per_l` (only used for the cost figures)
- [ ] Genset nameplate ratings → `limits.rated_kw` (only used for the demo and for `L per kWh` sanity)
