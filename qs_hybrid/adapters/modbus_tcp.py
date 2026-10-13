"""
Modbus TCP adapter. Reads are generic; writes only know how to execute the two
shapes a registered command can take in `raw`:

  raw: {address, kind, scale}                -> write a scaled setpoint register
  raw: {command_register, code | code_map}   -> write a command code (ComAp-style)

Anything else is refused. There is no "write arbitrary register" entry point.
"""

from __future__ import annotations

import struct
from typing import Any

from pymodbus.client import ModbusTcpClient

from ..registry.models import CommandDefinition, Device, PointDefinition, Quality
from .base import Adapter, Reading, make_reading


def _decode(regs: list[int], kind: str, scale: float, word_order: str = "big") -> float | int:
    if kind in ("uint16", "int16"):
        raw = regs[0]
        if kind == "int16" and raw >= 0x8000:
            raw -= 0x10000
        return raw * scale
    hi, lo = (regs[0], regs[1]) if word_order == "big" else (regs[1], regs[0])
    packed = struct.pack(">HH", hi, lo)
    fmt = {"uint32": ">I", "int32": ">i", "float32": ">f"}[kind]
    return struct.unpack(fmt, packed)[0] * scale


def _encode(value: float, kind: str, scale: float, word_order: str = "big") -> list[int]:
    raw = value / scale if scale else value
    if kind == "uint16":
        return [int(round(raw)) & 0xFFFF]
    if kind == "int16":
        return [int(round(raw)) & 0xFFFF]
    if kind in ("uint32", "int32"):
        packed = struct.pack(">i" if kind == "int32" else ">I", int(round(raw)))
    elif kind == "float32":
        packed = struct.pack(">f", float(raw))
    else:
        raise ValueError(kind)
    hi, lo = struct.unpack(">HH", packed)
    return [hi, lo] if word_order == "big" else [lo, hi]


class ModbusTcpAdapter(Adapter):
    name = "modbus_tcp"

    def __init__(self, device: Device):
        super().__init__(device)
        c = device.connection
        self.unit_id = int(c.get("unit_id", 1))
        self._client = ModbusTcpClient(host=c["host"], port=int(c.get("port", 502)), timeout=float(c.get("timeout", 3)))

    def connect(self) -> bool:
        return self._client.connect()

    def close(self) -> None:
        self._client.close()

    def read(self, points: list[PointDefinition] | None = None) -> dict[str, Reading]:
        out: dict[str, Reading] = {}
        for p in points or self.device.points:
            raw = p.raw
            if "address" not in raw:
                out[p.id] = make_reading(p, None, Quality.BAD, "no raw mapping")
                continue
            kind = raw.get("kind", "uint16")
            count = 2 if "32" in kind else 1
            fn = self._client.read_input_registers if raw.get("fn") == "input" else self._client.read_holding_registers
            try:
                rr = fn(address=int(raw["address"]), count=count, device_id=self.unit_id)
                if rr is None or rr.isError():
                    out[p.id] = make_reading(p, None, Quality.BAD, str(rr))
                    continue
                val = _decode(rr.registers, kind, float(raw.get("scale", 1.0)), raw.get("word_order", "big"))
                if "bit" in raw:                       # one bit of a status word
                    val = bool((int(rr.registers[0]) >> int(raw["bit"])) & 1)
                elif p.type == "boolean":
                    val = bool(val)
                out[p.id] = make_reading(p, val)
            except Exception as exc:
                out[p.id] = make_reading(p, None, Quality.BAD, str(exc))
        return out

    def write(self, command: CommandDefinition, args: dict[str, Any]) -> None:
        raw = command.raw
        if "command_register" in raw:
            # ComAp-style command code. Password/access-level handshake is controller
            # specific and must be added from the comms guide before live use.
            if "code" in raw:
                code = int(raw["code"])
            elif "code_map" in raw:
                code = int(raw["code_map"][str(next(iter(args.values())))])
            else:
                raise ValueError("command_register needs code or code_map")
            rr = self._client.write_register(address=int(raw["command_register"]), value=code, device_id=self.unit_id)
        elif "address" in raw:
            if len(args) != 1:
                raise ValueError("setpoint write expects exactly one argument")
            value = float(next(iter(args.values())))
            regs = _encode(value, raw.get("kind", "uint16"), float(raw.get("scale", 1.0)), raw.get("word_order", "big"))
            if len(regs) == 1:
                rr = self._client.write_register(address=int(raw["address"]), value=regs[0], device_id=self.unit_id)
            else:
                rr = self._client.write_registers(address=int(raw["address"]), values=regs, device_id=self.unit_id)
        else:
            raise ValueError(f"command {command.id} has no executable raw mapping")
        if rr is None or rr.isError():
            raise RuntimeError(f"modbus write failed: {rr}")
