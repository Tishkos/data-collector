"""Adapter factory. One place decides which protocol class serves a device."""

from __future__ import annotations

from ..registry.models import Device
from .base import Adapter, Reading


def build_adapter(device: Device, *, plant=None) -> Adapter:
    if device.adapter == "simulator":
        from .simulator import SimulatorAdapter

        if plant is None:
            raise ValueError("simulator adapter needs a SimPlant")
        return SimulatorAdapter(device, plant)
    if device.adapter == "modbus_tcp":
        from .modbus_tcp import ModbusTcpAdapter

        return ModbusTcpAdapter(device)
    if device.adapter == "comap_wsv":
        from .comap_wsv import ComApWSVAdapter

        return ComApWSVAdapter(device)
    if device.adapter == "inteliscada":
        from .inteliscada import InteliScadaAdapter

        return InteliScadaAdapter(device)
    raise ValueError(f"unknown adapter '{device.adapter}' for {device.id}")


__all__ = ["Adapter", "Reading", "build_adapter"]
