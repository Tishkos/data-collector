"""
Modbus TCP reader. Read-only: only read_holding_registers / read_input_registers exist here.

Points are grouped into contiguous blocks (max 100 registers) so one device costs a
handful of requests per poll, not one per point. That is what keeps the load on the
SAU and the InteliNeo small.

Connections are shared per host:port and closed after every poll (close_all): the SAU
gateway allows only 5 Modbus TCP links in total, and the InteliNeo already uses some of
them. Ten inverters behind one SAU therefore cost one connection, and none between polls.

point.raw: {address, kind: uint16|int16|uint32|int32|float32, scale, fn: holding|input|coil|discrete,
            word_order: big|little,
            bit: <n>        one bit of a status word (0 = least significant)
            mask: <int>     true when any of these bits is set
            invert: true    negate a bit/mask boolean
            derived: comm_error              true when the device answered nothing
            derived: sum, of: [point, ...]   sum of sibling points (all must be good)}
connection.address_offset: added to every address before the request (0 by default; -1 if a
            device's table turns out to be 1-based).
"""

from __future__ import annotations

import logging
import struct
import threading
import time
from datetime import datetime, timezone

from pymodbus.client import ModbusTcpClient

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)     # we report failures ourselves, per point

from ..registry import Device, PointDefinition, Quality
from ..store import Reading
from . import Adapter, reading

MAX_BLOCK = 100

_POOL: dict[tuple[str, int], ModbusTcpClient] = {}
_POOL_LOCK = threading.RLock()


def shared_client(host: str, port: int, timeout: float) -> ModbusTcpClient:
    with _POOL_LOCK:
        key = (host, port)
        if key not in _POOL:
            _POOL[key] = ModbusTcpClient(host=host, port=port, timeout=timeout)
        return _POOL[key]


def close_all() -> None:
    """Drop every connection; called by the collector after each poll."""
    with _POOL_LOCK:
        for c in _POOL.values():
            try:
                c.close()
            except Exception:
                pass


def _decode(regs: list[int], kind: str, scale: float, word_order: str) -> float | int:
    if kind in ("uint16", "int16"):
        raw = regs[0]
        if kind == "int16" and raw >= 0x8000:
            raw -= 0x10000
        return raw * scale
    hi, lo = (regs[0], regs[1]) if word_order == "big" else (regs[1], regs[0])
    packed = struct.pack(">HH", hi, lo)
    fmt = {"uint32": ">I", "int32": ">i", "float32": ">f"}[kind]
    return struct.unpack(fmt, packed)[0] * scale


def _size(kind: str) -> int:
    return 2 if "32" in kind else 1


class ModbusTcpAdapter(Adapter):
    name = "modbus_tcp"

    def __init__(self, device: Device):
        super().__init__(device)
        c = device.connection
        self.unit_id = int(c.get("unit_id", 1))
        self.address_offset = int(c.get("address_offset", 0))
        self._client = shared_client(c["host"], int(c.get("port", 502)), float(c.get("timeout", 3)))
        self._blocks = self._plan_blocks(device.points)
        self.last_ok_blocks = 0                 # statistics of the last read(), for the trace
        self.last_failed_blocks = 0
        self.last_ms = 0.0

    @staticmethod
    def _plan_blocks(points: list[PointDefinition]) -> list[tuple[str, int, int, list[PointDefinition]]]:
        """Group mapped points by function code into contiguous blocks."""
        mapped = [p for p in points if "address" in p.raw]
        blocks: list[tuple[str, int, int, list[PointDefinition]]] = []
        for fn in ("holding", "input", "coil", "discrete"):
            pts = sorted((p for p in mapped if p.raw.get("fn", "holding") == fn), key=lambda p: int(p.raw["address"]))
            cur: list[PointDefinition] = []
            start = end = None
            for p in pts:
                a = int(p.raw["address"])
                n = _size(p.raw.get("kind", "uint16"))
                if cur and (a + n - start > MAX_BLOCK):
                    blocks.append((fn, start, end - start, cur))
                    cur, start, end = [], None, None
                if start is None:
                    start = a
                cur.append(p)
                end = max(end or 0, a + n)
            if cur:
                blocks.append((fn, start, end - start, cur))
        return blocks

    def connect(self) -> bool:
        return self._client.connect()

    def close(self) -> None:
        self._client.close()

    def read(self) -> dict[str, Reading]:
        with _POOL_LOCK:                    # one request at a time on a shared connection
            return self._read()

    def _read(self) -> dict[str, Reading]:
        t0 = time.perf_counter()
        try:
            return self._read_blocks()
        finally:
            self.last_ms = round((time.perf_counter() - t0) * 1000, 1)

    def _read_blocks(self) -> dict[str, Reading]:
        out: dict[str, Reading] = {}
        now = datetime.now(timezone.utc)
        derived = []
        for p in self.device.points:
            if "address" not in p.raw:
                if p.raw.get("derived") in ("comm_error", "sum"):
                    derived.append(p)
                else:
                    out[p.id] = reading(p, None, Quality.BAD, "no register mapping", now)
        ok_blocks = failed_blocks = 0
        for fn, start, count, pts in self._blocks:
            call = {"input": self._client.read_input_registers, "coil": self._client.read_coils,
                    "discrete": self._client.read_discrete_inputs}.get(fn, self._client.read_holding_registers)
            try:
                if not self._client.connected:
                    self._client.connect()
                rr = call(address=start + self.address_offset, count=count, device_id=self.unit_id)
                if rr is None or rr.isError():
                    raise RuntimeError(str(rr))
                regs = [int(b) for b in rr.bits[:count]] if fn in ("coil", "discrete") else rr.registers   # a coil is a 0/1 "register"
                ok_blocks += 1
            except Exception as exc:
                failed_blocks += 1
                for p in pts:
                    out[p.id] = reading(p, None, Quality.BAD, f"modbus: {exc}", now)
                continue
            for p in pts:
                raw = p.raw
                off = int(raw["address"]) - start
                kind = raw.get("kind", "uint16")
                try:
                    chunk = regs[off:off + _size(kind)]
                    if "bit" in raw:
                        val = bool((int(chunk[0]) >> int(raw["bit"])) & 1)
                    elif "mask" in raw:
                        val = bool(int(chunk[0]) & int(raw["mask"]))
                    else:
                        val = _decode(chunk, kind, float(raw.get("scale", 1.0)), raw.get("word_order", "big"))
                        if p.type == "boolean":
                            val = bool(val)
                    if raw.get("invert") and isinstance(val, bool):
                        val = not val
                    out[p.id] = reading(p, val, ts=now)
                except Exception as exc:
                    out[p.id] = reading(p, None, Quality.BAD, f"decode: {exc}", now)
        self.last_ok_blocks, self.last_failed_blocks = ok_blocks, failed_blocks
        for p in derived:
            if p.raw.get("derived") == "comm_error":   # the device answered nothing this poll
                if not self._blocks:                    # nothing mapped yet: nothing was asked, so unknown
                    out[p.id] = reading(p, None, Quality.BAD, "no register mapping", now)
                else:
                    out[p.id] = reading(p, failed_blocks > 0 and ok_blocks == 0, ts=now)
            else:                                       # sum of sibling points, good only when all are good
                parts = [out.get(pid) for pid in p.raw.get("of", [])]
                if parts and all(r is not None and r.quality == Quality.GOOD and r.value is not None for r in parts):
                    out[p.id] = reading(p, round(sum(float(r.value) for r in parts), 3), ts=now)
                else:
                    missing = [pid for pid, r in zip(p.raw.get("of", []), parts) if r is None or r.quality != Quality.GOOD]
                    out[p.id] = reading(p, None, Quality.BAD, f"sum needs {', '.join(missing) or 'of: [...]'}", now)
        return out
