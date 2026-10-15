"""
Fast, read-only trace of a few devices to plain files - the instrument for event studies: how long the
SAU takes to answer, what the master's comm-fail bits do, what the setpoint registers hold second by second.

Separate from the hourly collector: own process, own files, nothing goes into the database. Connections
stay open for the whole run (one per device host), so the trace costs the SAU one Modbus TCP link.

Files in <out>/ (one set per day, UTC date in the name):
  <device>_<date>.csv   one row per read: ts, ms (response time), ok_blocks, failed_blocks, then every point
                        (booleans 0/1, a value that could not be read is left empty)
  events_<date>.csv     every change of a boolean point, of a point marked 'edge: true' in its profile, and of
                        a device's reachability: ts, device, point, old, new
  connect_<date>.csv    the connection test: a fresh TCP connection to a host every N seconds, ts, host, ok, ms

Everything is read-only: the trace uses the same adapter as the collector, which has no write function.
"""

from __future__ import annotations

import csv
import os
import socket
import time
from datetime import datetime, timezone

from .adapters import build_adapter
from .registry import Quality, Registry


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _cell(reading) -> str:
    if reading is None or reading.quality != Quality.GOOD or reading.value is None:
        return ""
    v = reading.value
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return f"{v:.3f}".rstrip("0").rstrip(".")
    return str(v)


class _DailyCsv:
    """A CSV file that starts a new file (with header) when the UTC date changes."""

    def __init__(self, out_dir: str, stem: str, header: list[str]):
        self.out_dir, self.stem, self.header = out_dir, stem, header
        self._date = None
        self._f = None
        self._w = None

    def write(self, ts: datetime, row: list) -> None:
        d = ts.astimezone(timezone.utc).strftime("%Y-%m-%d")
        if d != self._date:
            self.close()
            path = os.path.join(self.out_dir, f"{self.stem}_{d}.csv")
            new = not os.path.exists(path) or os.path.getsize(path) == 0
            self._f = open(path, "a", newline="", encoding="utf-8")
            self._w = csv.writer(self._f)
            if new:
                self._w.writerow(self.header)
            self._date = d
        self._w.writerow(row)
        self._f.flush()

    def close(self) -> None:
        if self._f:
            self._f.close()
            self._f = self._w = None


class Trace:
    def __init__(self, registry: Registry, out_dir: str, every: dict[str, float],
                 connect_test_s: float | None = 60.0, connect_hosts: list[tuple[str, int]] | None = None,
                 log=print):
        os.makedirs(out_dir, exist_ok=True)
        self.registry, self.out_dir, self.log = registry, out_dir, log
        self.every = {k: float(v) for k, v in every.items()}
        for dev_id in self.every:
            if dev_id not in registry.devices:
                raise ValueError(f"unknown device '{dev_id}' (config has: {', '.join(registry.devices)})")
        self.adapters = {d: build_adapter(registry.devices[d]) for d in self.every}
        self.files = {d: _DailyCsv(out_dir, d, ["ts", "ms", "ok_blocks", "failed_blocks"] + [p.id for p in registry.devices[d].points])
                      for d in self.every}
        self.events = _DailyCsv(out_dir, "events", ["ts", "device", "point", "old", "new"])
        self.connect_file = _DailyCsv(out_dir, "connect", ["ts", "host", "ok", "ms", "error"])
        self.connect_test_s = connect_test_s
        if connect_hosts is None:
            connect_hosts = []
            for d in self.every:
                c = registry.devices[d].connection
                if "host" in c and (c["host"], int(c.get("port", 502))) not in connect_hosts:
                    connect_hosts.append((c["host"], int(c.get("port", 502))))
        self.connect_hosts = connect_hosts
        self._last: dict[tuple[str, str], object] = {}      # (device, point) -> last value, for the event log
        self.rows = {d: 0 for d in self.every}
        self.failed = {d: 0 for d in self.every}
        self.events_logged = 0

    # ---- one read of one device ----------------------------------------------------------------
    def read_once(self, dev_id: str, ts: datetime | None = None) -> None:
        ts = ts or datetime.now(timezone.utc)
        ad = self.adapters[dev_id]
        dev = self.registry.devices[dev_id]
        try:
            out = ad.read()
        except Exception as exc:                            # an adapter never raises on a bad device, but be safe
            out, err = {}, str(exc)
            self.log(f"[trace] {dev_id}: {err}")
        ok = getattr(ad, "last_ok_blocks", 0)
        failed = getattr(ad, "last_failed_blocks", 0)
        ms = getattr(ad, "last_ms", 0.0)
        self.files[dev_id].write(ts, [_iso(ts), ms, ok, failed] + [_cell(out.get(p.id)) for p in dev.points])
        self.rows[dev_id] += 1
        if ok == 0:
            self.failed[dev_id] += 1
        self._edge(ts, dev_id, "reachable", ok > 0)
        for p in dev.points:
            r = out.get(p.id)
            if r is None or r.quality != Quality.GOOD:
                continue
            if p.type == "boolean" or p.raw.get("edge"):
                self._edge(ts, dev_id, p.id, r.value)

    def _edge(self, ts: datetime, dev_id: str, point: str, value) -> None:
        key = (dev_id, point)
        old = self._last.get(key, None)
        if key in self._last and old == value:
            return
        if key in self._last:                               # the first value is a baseline, not a change
            self.events.write(ts, [_iso(ts), dev_id, point, _plain(old), _plain(value)])
            self.events_logged += 1
        self._last[key] = value

    # ---- connection test ------------------------------------------------------------------------
    def connect_test(self, ts: datetime | None = None) -> None:
        ts = ts or datetime.now(timezone.utc)
        for host, port in self.connect_hosts:
            t0 = time.perf_counter()
            try:
                s = socket.create_connection((host, port), timeout=3)
                s.close()
                ok, err = 1, ""
            except OSError as exc:
                ok, err = 0, str(exc)
            ms = round((time.perf_counter() - t0) * 1000, 1)
            self.connect_file.write(ts, [_iso(ts), f"{host}:{port}", ok, ms, err])

    # ---- the loop --------------------------------------------------------------------------------
    def run(self, duration_s: float | None = None, stop=None, status_every_s: float = 60.0) -> None:
        start = time.monotonic()
        due = {d: start for d in self.every}
        next_connect = start if self.connect_test_s else None
        next_status = start + status_every_s
        self.log(f"[trace] {', '.join(f'{d} every {s:g} s' for d, s in self.every.items())}"
                 + (f"; connection test every {self.connect_test_s:g} s" if self.connect_test_s else "")
                 + f"; files in {self.out_dir}")
        try:
            while True:
                now = time.monotonic()
                if duration_s is not None and now - start >= duration_s:
                    break
                if stop is not None and stop.is_set():
                    break
                for d, t in due.items():
                    if now >= t:
                        self.read_once(d)
                        due[d] = max(t + self.every[d], time.monotonic())    # no catching up after a slow read
                if next_connect is not None and now >= next_connect:
                    self.connect_test()
                    next_connect += self.connect_test_s
                if now >= next_status:
                    self.log("[trace] " + ", ".join(f"{d}: {self.rows[d]} rows, {self.failed[d]} failed"
                                                    for d in self.every) + f", {self.events_logged} events")
                    next_status += status_every_s
                pending = [t for t in due.values()] + ([next_connect] if next_connect is not None else [])
                time.sleep(max(0.02, min(pending) - time.monotonic()))
        finally:
            self.close()

    def close(self) -> None:
        for f in self.files.values():
            f.close()
        self.events.close()
        self.connect_file.close()
        for ad in self.adapters.values():
            try:
                ad.close()
            except Exception:
                pass


def _plain(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    return "" if v is None else str(v)


# ---- probe: which hosts answer on Modbus at all ---------------------------------------------------
def probe(targets: list[tuple[str, int, int]], address: int = 0, timeout: float = 3.0) -> list[dict]:
    """For each (host, port, unit): open a TCP connection, send one read of one holding register, report.
    An exception reply (illegal address, illegal function) still proves a Modbus server answers."""
    from pymodbus.client import ModbusTcpClient
    rows = []
    for host, port, unit in targets:
        row = {"host": host, "port": port, "unit": unit, "tcp": "", "modbus": "", "ms": None}
        t0 = time.perf_counter()
        try:
            s = socket.create_connection((host, port), timeout=timeout)
            s.close()
            row["tcp"] = "open"
        except OSError as exc:
            row["tcp"] = "timeout" if isinstance(exc, socket.timeout) else "closed"
            row["modbus"] = f"no connection ({exc})"
            row["ms"] = round((time.perf_counter() - t0) * 1000, 1)
            rows.append(row)
            continue
        c = ModbusTcpClient(host=host, port=port, timeout=timeout)
        try:
            c.connect()
            t1 = time.perf_counter()
            rr = c.read_holding_registers(address=address, count=1, device_id=unit)
            row["ms"] = round((time.perf_counter() - t1) * 1000, 1)
            if rr is None:
                row["modbus"] = "no reply"
            elif rr.isError():
                row["modbus"] = f"answers (exception: {rr})"
            else:
                row["modbus"] = f"answers, register {address} = {rr.registers[0]}"
        except Exception as exc:
            row["modbus"] = f"no reply ({exc})"
            row["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        finally:
            try:
                c.close()
            except Exception:
                pass
        rows.append(row)
    return rows
