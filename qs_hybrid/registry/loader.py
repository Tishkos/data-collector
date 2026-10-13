"""Load the site config + device profiles from YAML into validated models."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

from .models import Device, SitePolicy

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROFILES_DIR = os.path.join(ROOT, "profiles")


@dataclass
class Registry:
    policy: SitePolicy
    devices: dict[str, Device] = field(default_factory=dict)

    def get(self, device_id: str) -> Device:
        if device_id not in self.devices:
            raise KeyError(f"unknown device '{device_id}'")
        return self.devices[device_id]

    def by_kind(self, kind: str) -> list[Device]:
        return [d for d in self.devices.values() if d.kind == kind]

    def capability_inventory(self) -> list[dict]:
        """Everything the AI and HMI are allowed to know about."""
        out = []
        for d in self.devices.values():
            out.append(
                {
                    "device_id": d.id,
                    "kind": d.kind,
                    "model": f"{d.vendor} {d.model}".strip(),
                    "profile_status": d.profile_status,
                    "points": [
                        {"id": p.id, "unit": p.unit, "type": p.type, "access": p.access}
                        for p in d.points
                    ],
                    "commands": [
                        {
                            "id": c.id,
                            "risk_class": c.risk_class.value,
                            "args": {k: v.model_dump(exclude_none=True) for k, v in c.args.items()},
                            "requires_approval": c.requires_approval,
                            "allowed_modes": [m.value for m in c.allowed_modes],
                            "description": c.description,
                        }
                        for c in d.commands
                    ],
                    "limits": d.limits.model_dump(exclude_none=True),
                }
            )
        return out


def _load_yaml(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_registry(site_config_path: str) -> Registry:
    cfg = _load_yaml(site_config_path)
    policy = SitePolicy(**cfg["site"])
    reg = Registry(policy=policy)

    for entry in cfg.get("devices", []):
        prof_path = entry["profile"]
        if not os.path.isabs(prof_path):
            prof_path = os.path.join(PROFILES_DIR, prof_path)
        prof = _load_yaml(prof_path)
        # site.yaml overrides identity + connection; profile supplies points/commands/limits
        merged = {**prof, **{k: v for k, v in entry.items() if k != "profile"}}
        dev = Device(**merged)
        reg.devices[dev.id] = dev
    return reg
