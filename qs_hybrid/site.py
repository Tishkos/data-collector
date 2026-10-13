"""
Site runtime: wires registry + adapters + telemetry + policy + gateway + EMS.

In simulator mode the clock is the plant's clock and `advance()` steps physics,
so tests and demos are deterministic. In live mode the clock is wall time and
`poll()` is driven by a scheduler.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import yaml

from .adapters import build_adapter
from .adapters.simulator import SimPlant
from .analytics.faults import FaultDetector
from .control.audit import AuditLog
from .control.ems import EMS
from .control.gateway import CommandGateway
from .control.policy import PolicyEngine
from .registry.loader import load_registry
from .telemetry.store import TelemetryStore


class Site:
    def __init__(self, config_path: str = "config/site.yaml", data_dir: str = "./data"):
        self.config_path = config_path
        self.registry = load_registry(config_path)
        cfg = yaml.safe_load(open(config_path))
        self.simulated = all(d.adapter == "simulator" for d in self.registry.devices.values())
        self.plant: SimPlant | None = None
        if self.simulated:
            prof = cfg.get("site_load", {}).get("profile_kw") or [300] * 24
            self.plant = SimPlant(self.registry.devices, prof)
            self.clock = lambda: self.plant.now
            self.sleep = lambda s: self.plant.step(s)
        else:
            self.clock = lambda: datetime.now(timezone.utc)
            self.sleep = time.sleep

        os.makedirs(data_dir, exist_ok=True)
        self.store = TelemetryStore(os.path.join(data_dir, "telemetry.sqlite"))
        self.audit = AuditLog(os.path.join(data_dir, "audit.jsonl"))
        self.adapters = {d.id: build_adapter(d, plant=self.plant) for d in self.registry.devices.values()}
        for a in self.adapters.values():
            a.connect()
        self.policy = PolicyEngine(self.registry, self.store, clock=self.clock)
        self.gateway = CommandGateway(self.registry, self.store, self.adapters, self.policy, self.audit,
                                      clock=self.clock, refresh=self.poll_device, sleep=self.sleep)
        self.ems = EMS(self.registry, self.store, clock=self.clock)
        self.faults = FaultDetector(self.registry, self.store, clock=self.clock)
        self.poll()

    # --- telemetry ------------------------------------------------------------

    def poll_device(self, device_id: str) -> None:
        self.store.ingest(device_id, self.adapters[device_id].read())

    def poll(self) -> None:
        for did in self.adapters:
            self.poll_device(did)
        if self.plant is not None:
            self.store.event("sim_tick", f"load={self.plant.load_kw} unserved={self.plant.unserved_kw}", ts=self.clock(),
                             data=str({"load_kw": self.plant.load_kw, "unserved_kw": self.plant.unserved_kw}))
        for f in self.faults.scan():
            self.store.event("fault", f.message, device_id=f.device_id, severity=f.severity, code=f.code, ts=self.clock())

    def advance(self, seconds: float, step: float = 60.0) -> None:
        """Simulator only: move time forward, polling every `step` seconds."""
        assert self.plant is not None, "advance() is for simulator mode"
        remaining = seconds
        while remaining > 0:
            s = min(step, remaining)
            self.plant.step(s)
            remaining -= s
            self.poll()

    # --- convenience -------------------------------------------------------------

    def snapshot(self) -> dict:
        out = {"time": self.clock().isoformat(), "mode": self.registry.policy.mode.value, "devices": {}}
        for did in self.adapters:
            out["devices"][did] = {pid: (r.value if r.quality.value == "good" else f"<{r.quality.value}>")
                                   for pid, r in self.store.latest(did).items()}
        if self.plant is not None:
            out["site"] = {"load_kw": self.plant.load_kw, "unserved_kw": self.plant.unserved_kw}
        return out
