"""
Modbus TCP reader. Read-only: only read_holding_registers / read_input_registers exist here.

Points are grouped into contiguous blocks (max 100 registers) so one device costs a
handful of requests per poll, not one per point. That is what keeps the load on the
SAU and the InteliNeo small.

point.raw: {address, kind: uint16|int16|uint32|int32|float32, scale, fn: holding|input,
            word_order: big|little, bit: <n> (one bit of a status word)}
"""

from __future__ import annotations

import logging
import struct
from datetime import datetime, timezone

from pymodbus.client import ModbusTcpClient

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)     # we report failures ourselves, per point

from ..registry import Device, PointDefinition, Quality
from ..store import Reading
from . import Adapter, reading

MAX_BLOCK = 100


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
        self._client = ModbusTcpClient(host=c["host"], port=int(c.get("port", 502)), timeout=float(c.get("timeout", 3)))
        self._blocks = self._plan_blocks(device.points)

    @staticmethod
    def _plan_blocks(points: list[PointDefinition]) -> list[tuple[str, int, int, list[PointDefinition]]]:
        """Group mapped points by function code into contiguous blocks."""
        mapped = [p for p in points if "address" in p.raw]
        blocks: list[tuple[str, int, int, list[PointDefinition]]] = []
        for fn in ("holding", "input"):
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
        out: dict[str, Reading] = {}
        now = datetime.now(timezone.utc)
        derived = []
        for p in self.device.points:
            if "address" not in p.raw:
                if p.raw.get("derived") == "comm_error":
                    derived.append(p)
                else:
                    out[p.id] = reading(p, None, Quality.BAD, "no register mapping", now)
        ok_blocks = failed_blocks = 0
        for fn, start, count, pts in self._blocks:
            call = self._client.read_input_registers if fn == "input" else self._client.read_holding_registers
            try:
                if not self._client.connected:
                    self._client.connect()
                rr = call(address=start, count=count, device_id=self.unit_id)
                if rr is None or rr.isError():
                    raise RuntimeError(str(rr))
                regs = rr.registers
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
                    else:
                        val = _decode(chunk, kind, float(raw.get("scale", 1.0)), raw.get("word_order", "big"))
                        if p.type == "boolean":
                            val = bool(val)
                    out[p.id] = reading(p, val, ts=now)
                except Exception as exc:
                    out[p.id] = reading(p, None, Quality.BAD, f"decode: {exc}", now)
        for p in derived:   # "comm_error" = the device answered nothing this poll
            out[p.id] = reading(p, failed_blocks > 0 and ok_blocks == 0, ts=now)
        return out
