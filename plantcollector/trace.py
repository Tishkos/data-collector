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
  watch_<date>.csv      the stall catcher (watch={device: seconds}): every second one ping of the device's host
                        (is the box alive) and one read of one register on the device's existing connection
                        (does the application answer, and in how many ms): ts, device, kind, ok, ms, detail

Each device runs in its own thread with its own connection, so a stalled SAU never delays the master's rows -
the master's comm-fail bits are read on time while the SAU is being slow. Everything is read-only: the trace
uses the same adapter as the collector, which has no write function.
"""

from __future__ import annotations

import csv
import os
import platform
import re
import socket
import subprocess
import threading
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
                 log=print, watch: dict[str, float] | None = None):
        os.makedirs(out_dir, exist_ok=True)
        self.registry, self.out_dir, self.log = registry, out_dir, log
        self.every = {k: float(v) for k, v in every.items()}
        self.watch = {k: float(v) for k, v in (watch or {}).items()}
        for dev_id in list(self.every) + list(self.watch):
            if dev_id not in registry.devices:
                raise ValueError(f"unknown device '{dev_id}' (config has: {', '.join(registry.devices)})")
        for dev_id in self.watch:
            if dev_id not in self.every:
                raise ValueError(f"watch device '{dev_id}' must also be traced (every)")
        self.adapters = {d: build_adapter(registry.devices[d]) for d in self.every}
        self.files = {d: _DailyCsv(out_dir, d, ["ts", "ms", "ok_blocks", "failed_blocks"] + [p.id for p in registry.devices[d].points])
                      for d in self.every}
        self.events = _DailyCsv(out_dir, "events", ["ts", "device", "point", "old", "new"])
        self.connect_file = _DailyCsv(out_dir, "connect", ["ts", "host", "ok", "ms", "error"])
        self.watch_file = _DailyCsv(out_dir, "watch", ["ts", "device", "kind", "ok", "ms", "detail"])
        self._lock = threading.RLock()                      # events, _last and the watch file are shared by threads
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
        self.watch_rows = 0
        self.stalls = 0                                      # watch reads slower than 1 s or unanswered
        self._ping_off = False

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
        with self._lock:
            old = self._last.get(key, None)
            if key in self._last and old == value:
                return
            if key in self._last:                           # the first value is a baseline, not a change
                self.events.write(ts, [_iso(ts), dev_id, point, _plain(old), _plain(value)])
                self.events_logged += 1
            self._last[key] = value

    # ---- the stall catcher ----------------------------------------------------------------------
    def watch_read(self, dev_id: str, ts: datetime | None = None) -> None:
        """One register on the device's own connection: did the application answer, in how many ms."""
        ts = ts or datetime.now(timezone.utc)
        ad = self.adapters[dev_id]
        fn = getattr(ad, "ping_read", None)
        if fn is None:
            return
        status, ms, value = fn()
        ok = 1 if status == "ok" else 0
        with self._lock:
            self.watch_file.write(ts, [_iso(ts), dev_id, "read", ok, ms, status if not ok else value])
            self.watch_rows += 1
            if not ok or ms >= 1000:
                self.stalls += 1
        self._edge(ts, dev_id, "answers_us", ok == 1)

    def watch_ping(self, dev_id: str, ts: datetime | None = None) -> None:
        ts = ts or datetime.now(timezone.utc)
        host = self.registry.devices[dev_id].connection.get("host")
        if not host:
            return
        if self._ping_off:
            return
        ok, ms, detail = ping(host)
        if detail.startswith("ping unavailable"):           # no ping on this machine: say so once, stop trying
            self._ping_off = True
            self.log(f"[trace] {detail}; the stall catcher keeps the reads only")
            return
        with self._lock:
            self.watch_file.write(ts, [_iso(ts), dev_id, "ping", ok, ms, detail])
            self.watch_rows += 1
        self._edge(ts, dev_id, "ping_ok", ok == 1)

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
    def _device_loop(self, dev_id: str, end: float | None, stop) -> None:
        period = self.every[dev_id]
        due = time.monotonic()
        while not stop.is_set() and (end is None or time.monotonic() < end):
            if time.monotonic() >= due:
                self.read_once(dev_id)
                due = max(due + period, time.monotonic())        # no catching up after a slow read
            self._nap(due, end, stop)

    def _watch_loop(self, dev_id: str, fn, end: float | None, stop) -> None:
        period = self.watch[dev_id]
        due = time.monotonic()
        while not stop.is_set() and (end is None or time.monotonic() < end):
            if time.monotonic() >= due:
                fn(dev_id)
                due = max(due + period, time.monotonic())
            self._nap(due, end, stop)

    @staticmethod
    def _nap(due: float, end: float | None, stop) -> None:
        wait = due - time.monotonic()
        if end is not None:
            wait = min(wait, end - time.monotonic())
        if wait > 0:
            stop.wait(min(wait, 0.5))

    def run(self, duration_s: float | None = None, stop=None, status_every_s: float = 60.0) -> None:
        start = time.monotonic()
        end = None if duration_s is None else start + duration_s
        stop = stop or threading.Event()
        next_connect = start if self.connect_test_s else None
        next_status = start + status_every_s
        self.log(f"[trace] {', '.join(f'{d} every {s:g} s' for d, s in self.every.items())}"
                 + (f"; watch {', '.join(f'{d} every {s:g} s' for d, s in self.watch.items())}" if self.watch else "")
                 + (f"; connection test every {self.connect_test_s:g} s" if self.connect_test_s else "")
                 + f"; files in {self.out_dir}")
        threads = [threading.Thread(target=self._device_loop, args=(d, end, stop), daemon=True, name=f"trace-{d}") for d in self.every]
        for d in self.watch:
            threads.append(threading.Thread(target=self._watch_loop, args=(d, self.watch_read, end, stop), daemon=True, name=f"watch-{d}"))
            threads.append(threading.Thread(target=self._watch_loop, args=(d, self.watch_ping, end, stop), daemon=True, name=f"ping-{d}"))
        for t in threads:
            t.start()
        try:
            while not stop.is_set() and (end is None or time.monotonic() < end):
                now = time.monotonic()
                if next_connect is not None and now >= next_connect:
                    self.connect_test()
                    next_connect += self.connect_test_s
                if now >= next_status:
                    self.log("[trace] " + ", ".join(f"{d}: {self.rows[d]} rows, {self.failed[d]} failed"
                                                    for d in self.every) + f", {self.events_logged} events"
                             + (f", watch {self.watch_rows} rows, {self.stalls} stalls" if self.watch else ""))
                    next_status += status_every_s
                pending = [next_status] + ([next_connect] if next_connect is not None else [])
                if end is not None:
                    pending.append(end)
                stop.wait(max(0.05, min(0.5, min(pending) - time.monotonic())))
        finally:
            for t in threads:                               # let each thread finish its current read, then close
                t.join(timeout=15)
            self.close()

    def close(self) -> None:
        for f in self.files.values():
            f.close()
        self.events.close()
        self.connect_file.close()
        self.watch_file.close()
        for ad in self.adapters.values():
            try:
                ad.close()
            except Exception:
                pass


def _plain(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    return "" if v is None else str(v)


def ping(host: str, timeout_s: float = 1.0) -> tuple[int, float, str]:
    """One ICMP echo with the system's ping (no privileges needed). Returns (ok, ms, detail)."""
    win = platform.system().lower().startswith("win")
    cmd = ["ping", "-n", "1", "-w", str(int(timeout_s * 1000)), host] if win else ["ping", "-c", "1", "-W", str(max(1, int(timeout_s))), host]
    t0 = time.perf_counter()
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s + 2).stdout
    except FileNotFoundError:
        return _icmp_echo(host, timeout_s)                  # no ping binary (a bare container): try a datagram ICMP socket
    except Exception as exc:
        return 0, round((time.perf_counter() - t0) * 1000, 1), f"ping failed: {exc}"
    low = out.lower()
    if "ttl=" not in low:                                   # Windows prints "unreachable" replies with exit code 0
        return 0, round((time.perf_counter() - t0) * 1000, 1), "no reply"
    m = re.search(r"time[=<]\s*([\d.]+)", low)
    return 1, float(m.group(1)) if m else round((time.perf_counter() - t0) * 1000, 1), ""


# ---- probe: which hosts answer on Modbus at all ---------------------------------------------------
def _icmp_echo(host: str, timeout_s: float) -> tuple[int, float, str]:
    """ICMP echo through an unprivileged datagram socket (Linux with ping_group_range open). No raw socket."""
    import struct as _struct
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    except (OSError, AttributeError) as exc:
        return 0, 0.0, f"ping unavailable: {exc}"
    try:
        sock.settimeout(timeout_s)
        payload = _struct.pack(">BBHHH", 8, 0, 0, 0, 1) + b"plantcollector"
        csum = 0
        data = payload if len(payload) % 2 == 0 else payload + b"\0"
        for i in range(0, len(data), 2):
            csum += (data[i] << 8) + data[i + 1]
        csum = (csum >> 16) + (csum & 0xFFFF)
        csum = (~(csum + (csum >> 16))) & 0xFFFF
        payload = payload[:2] + _struct.pack(">H", csum) + payload[4:]
        t0 = time.perf_counter()
        sock.sendto(payload, (host, 0))
        sock.recvfrom(1024)
        return 1, round((time.perf_counter() - t0) * 1000, 2), ""
    except socket.timeout:
        return 0, round(timeout_s * 1000, 1), "no reply"
    except OSError as exc:
        return 0, 0.0, f"ping unavailable: {exc}"
    finally:
        sock.close()


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
