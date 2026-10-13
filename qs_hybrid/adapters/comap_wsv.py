"""
ComAp WebSupervisor (cloud) adapter.

Route: controller --AirGate--> WebSupervisor --REST API--> here.
Needs a WSV Pro (or trial) account with API access. Credentials via env:
  COMAP_KEY, COMAP_CLIENT_ID, COMAP_SECRET  (ComAp Cloud Identity -> bearer token)
and the unit's GUID in the device connection: {unit_guid: "..."}.

Point `raw` for this adapter is {value_guid: "..."} (WSV value GUIDs).
Command `raw` is {wsv_command: "start"|"stop"|"faultReset"|"changeMode", mode?: "..."}.

This file wraps the official REST API with `requests`. The community `comap`
package (pypi.org/project/comap) does the same; we keep a thin direct client so
the gateway's verification + audit stay in our hands.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

from ..registry.models import CommandDefinition, Device, PointDefinition, Quality
from .base import Adapter, Reading, make_reading

IDENTITY_URL = "https://api.websupervisor.net/identity/v1/token"   # confirm on portal.websupervisor.net
WSV_URL = "https://api.websupervisor.net/wsv/v1.1"


class ComApWSVAdapter(Adapter):
    name = "comap_wsv"

    def __init__(self, device: Device):
        super().__init__(device)
        self.key = os.environ.get("COMAP_KEY", "")
        self.client_id = os.environ.get("COMAP_CLIENT_ID", "")
        self.secret = os.environ.get("COMAP_SECRET", "")
        self.unit_guid = device.connection.get("unit_guid", "")
        self._token: str | None = None
        self._token_exp = 0.0

    # --- auth ---------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        if not self._token or time.time() > self._token_exp - 60:
            r = requests.post(
                IDENTITY_URL,
                headers={"Comap-Key": self.key},
                data={"grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self.secret},
                timeout=15,
            )
            r.raise_for_status()
            j = r.json()
            self._token = j["access_token"]
            self._token_exp = time.time() + float(j.get("expires_in", 3600))
        return {"Comap-Key": self.key, "Authorization": f"Bearer {self._token}"}

    def connect(self) -> bool:
        try:
            self._headers()
            return True
        except Exception:
            return False

    # --- read ---------------------------------------------------------------

    def read(self, points: list[PointDefinition] | None = None) -> dict[str, Reading]:
        pts = points or self.device.points
        guids = ",".join(p.raw["value_guid"] for p in pts if "value_guid" in p.raw)
        try:
            r = requests.get(
                f"{WSV_URL}/units/{self.unit_guid}/values",
                params={"valueGuids": guids},
                headers=self._headers(),
                timeout=20,
            )
            r.raise_for_status()
            by_guid = {v["valueGuid"]: v for v in r.json().get("values", [])}
        except Exception as exc:
            return {p.id: make_reading(p, None, Quality.BAD, str(exc)) for p in pts}
        out: dict[str, Reading] = {}
        for p in pts:
            v = by_guid.get(p.raw.get("value_guid"))
            if v is None:
                out[p.id] = make_reading(p, None, Quality.BAD, "value not returned")
                continue
            val = v.get("value")
            if p.type == "boolean":
                val = bool(val)
            out[p.id] = make_reading(p, val)
        return out

    # --- write (gateway-only) ---------------------------------------------------

    def write(self, command: CommandDefinition, args: dict[str, Any]) -> None:
        cmd = command.raw.get("wsv_command")
        if not cmd:
            raise ValueError(f"{command.id}: no wsv_command mapping")
        body: dict[str, Any] = {"command": cmd}
        if cmd == "changeMode":
            body["mode"] = str(args.get("mode", command.raw.get("mode", "")))
        r = requests.post(
            f"{WSV_URL}/units/{self.unit_guid}/command",
            json=body,
            headers=self._headers(),
            timeout=20,
        )
        r.raise_for_status()
