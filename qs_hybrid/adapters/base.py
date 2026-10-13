"""
Adapter interface. An adapter talks one protocol to one device.

Rule: `write()` is only ever called by the CommandGateway, after the policy engine
has approved a registered CommandDefinition. Adapters never expose raw register
writes to anything else, and no module outside qs_hybrid.control imports write().
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..registry.models import CommandDefinition, Device, PointDefinition, Quality


@dataclass
class Reading:
    point_id: str
    value: Any
    unit: str
    source_ts: datetime
    quality: Quality = Quality.GOOD
    error: str | None = None

    def age_ms(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (now - self.source_ts).total_seconds() * 1000.0


class Adapter:
    name = "base"

    def __init__(self, device: Device):
        self.device = device

    def connect(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def read(self, points: list[PointDefinition] | None = None) -> dict[str, Reading]:
        raise NotImplementedError

    def write(self, command: CommandDefinition, args: dict[str, Any]) -> None:
        """Issue the device-level operation for a registered command. Gateway-only."""
        raise NotImplementedError


def make_reading(p: PointDefinition, value: Any, quality: Quality = Quality.GOOD, error: str | None = None) -> Reading:
    return Reading(point_id=p.id, value=value, unit=p.unit, source_ts=datetime.now(timezone.utc), quality=quality, error=error)
