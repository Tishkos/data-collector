"""
ComAp InteliSCADA 3.x adapter (read-only).

Reads values from the InteliSCADA server on the SCADA PC instead of polling the
InteliNeo: zero extra load on the controller. Flow proven on site (Oct 2026):

  POST /api/auth/login               {}                      -> anonymous access/refresh token
  POST /api/auth/sites/{site}/unlock {password}              -> site-scoped token (expires after
                                                                autoLogoutTimeoutMin idle; we re-unlock on 401)
  GET  /api/sites                                            -> site id + name
  GET  /api/sites/{site}/devices                             -> devices (InteliNeo, gensets, mains)
  GET  /api/sites/{site}/devices/{dev}/datapoints            -> datapoint DEFINITIONS (no values)
  GET  /api/trends/{site}                                    -> trend sources (historised values)
  POST /api/trends/query  {siteDefinitionId, from, to, requestedTrends[], maxDataPoints}
                                                             -> samples with timestamps  (rate-limited: 429)

Live values come from the trend history (latest sample per trend). That is the
historian path; it gives us ComAp's own timestamps and lets us backfill weeks.

Device profile `raw` for this adapter:  {trend: "<trend id or name>"}  per point.
Device connection: {site_id?, device_name?}  (site auto-detected if only one).
Shared client per base URL so ten devices do one login, not ten.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from ..registry.models import CommandDefinition, Device, PointDefinition, Quality
from .base import Adapter, Reading, make_reading

_CLIENTS: dict[str, "InteliScadaClient"] = {}
_LOCK = threading.Lock()


def _ts(s: str) -> datetime:
    s = s.replace("Z", "+00:00")
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


class InteliScadaClient:
    def __init__(self, base_url: str, site_password: str, site_id: str | None = None, timeout: float = 20.0):
        self.base = base_url.rstrip("/")
        self.password = site_password
        self.site_id = site_id
        self.timeout = timeout
        self.s = requests.Session()
        self._site_token: str | None = None
        self._anon_token: str | None = None
        self._trend_cache: dict[str, Any] = {}
        self._trend_cache_at = 0.0
        self.last_query_at = 0.0
        self.min_query_interval = 10.0     # server has PermitLimit=1 on trend reads

    # --- auth ---------------------------------------------------------------

    def _login_anonymous(self) -> str:
        r = self.s.post(f"{self.base}/api/auth/login", json={}, timeout=self.timeout)
        r.raise_for_status()
        self._anon_token = r.json()["accessToken"]
        return self._anon_token

    def _unlock(self) -> str:
        if not self._anon_token:
            self._login_anonymous()
        if not self.site_id:
            sites = self.s.get(f"{self.base}/api/sites", headers=self._h(self._anon_token), timeout=self.timeout).json()
            if isinstance(sites, dict):
                sites = [sites]
            if len(sites) != 1:
                raise RuntimeError(f"{len(sites)} sites on this InteliSCADA; set connection.site_id")
            self.site_id = sites[0]["id"]
        r = self.s.post(f"{self.base}/api/auth/sites/{self.site_id}/unlock",
                        headers=self._h(self._anon_token), json={"password": self.password}, timeout=self.timeout)
        if r.status_code == 401:
            # anonymous token may have expired
            self._login_anonymous()
            r = self.s.post(f"{self.base}/api/auth/sites/{self.site_id}/unlock",
                            headers=self._h(self._anon_token), json={"password": self.password}, timeout=self.timeout)
        r.raise_for_status()
        self._site_token = r.json()["accessToken"]
        return self._site_token

    @staticmethod
    def _h(token: str | None) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"} if token else {}

    def ensure(self) -> None:
        if not self._site_token:
            self._unlock()

    def _req(self, method: str, path: str, **kw) -> Any:
        """Request with the site token; re-unlock once on 401 (5-min idle logout)."""
        self.ensure()
        for attempt in (1, 2):
            r = self.s.request(method, f"{self.base}{path}", headers=self._h(self._site_token), timeout=self.timeout, **kw)
            if r.status_code == 401 and attempt == 1:
                self._unlock()
                continue
            if r.status_code == 429:
                time.sleep(5)
                continue
            r.raise_for_status()
            return r.json() if r.content else None
        raise RuntimeError(f"{method} {path}: unauthorized after re-unlock")

    # --- discovery ---------------------------------------------------------------

    def devices(self) -> list[dict]:
        self.ensure()
        return self._req("GET", f"/api/sites/{self.site_id}/devices")

    def datapoints(self, device_id: str) -> list[dict]:
        self.ensure()
        return self._req("GET", f"/api/sites/{self.site_id}/devices/{device_id}/datapoints")

    def trends(self, max_age_s: float = 600) -> list[dict]:
        self.ensure()
        if time.time() - self._trend_cache_at > max_age_s:
            self._trend_cache = {"list": self._req("GET", f"/api/trends/{self.site_id}")}
            self._trend_cache_at = time.time()
        return self._trend_cache["list"]

    def trend_id(self, key: str) -> str | None:
        """Accept a trend id or a (case-insensitive) trend name."""
        for t in self.trends():
            if t.get("id") == key or str(t.get("name", "")).lower() == key.lower():
                return t.get("id")
        return None

    # --- data --------------------------------------------------------------------

    def query(self, trend_ids: list[str], since: datetime, until: datetime | None = None, max_points: int = 1000) -> list[dict]:
        # respect the server's single-permit trend reader
        wait = self.min_query_interval - (time.time() - self.last_query_at)
        if wait > 0:
            time.sleep(wait)
        until = until or datetime.now(timezone.utc)
        body = {"siteDefinitionId": self.site_id, "from": since.isoformat(), "to": until.isoformat(),
                "requestedTrends": trend_ids, "maxDataPoints": max_points}
        out = self._req("POST", "/api/trends/query", json=body)
        self.last_query_at = time.time()
        return out or []

    @staticmethod
    def latest_samples(trend_data: list[dict]) -> dict[str, tuple[datetime, Any]]:
        """{trend_id: (ts, value)} of the newest sample per trend."""
        out: dict[str, tuple[datetime, Any]] = {}
        for t in trend_data:
            samples = t.get("samples") or []
            if not samples:
                continue
            last = samples[-1]
            ts = last.get("timestamp") or last.get("time") or last.get("t")
            val = last.get("value") if "value" in last else last.get("v")
            if ts is None:
                continue
            out[t["id"]] = (_ts(ts), val)
        return out


def get_client(base_url: str, password: str, site_id: str | None) -> InteliScadaClient:
    with _LOCK:
        c = _CLIENTS.get(base_url)
        if c is None:
            c = InteliScadaClient(base_url, password, site_id)
            _CLIENTS[base_url] = c
        return c


class InteliScadaAdapter(Adapter):
    """One registry device whose points map to InteliSCADA trends."""

    name = "inteliscada"

    def __init__(self, device: Device):
        super().__init__(device)
        c = device.connection
        self.client = get_client(
            c.get("url", os.environ.get("QS_ISCADA_URL", "http://localhost:8801")),
            c.get("password", os.environ.get("QS_ISCADA_PASSWORD", "")),
            c.get("site_id") or os.environ.get("QS_ISCADA_SITE_ID") or None,
        )
        self.lookback_min = float(c.get("lookback_min", 10))
        self.stale_after_s = float(c.get("stale_after_s", 300))

    def connect(self) -> bool:
        try:
            self.client._unlock()
            return True
        except Exception:
            return False

    def read(self, points: list[PointDefinition] | None = None) -> dict[str, Reading]:
        pts = points or self.device.points
        keyed: dict[str, PointDefinition] = {}
        unmapped: list[PointDefinition] = []
        try:
            for p in pts:
                key = p.raw.get("trend")
                tid = self.client.trend_id(str(key)) if key else None
                if tid:
                    keyed[tid] = p
                else:
                    unmapped.append(p)
        except Exception as exc:  # login/unlock/trend-list failure: everything is BAD, nothing crashes
            return {p.id: make_reading(p, None, Quality.BAD, f"inteliscada: {exc}") for p in pts}
        out: dict[str, Reading] = {p.id: make_reading(p, None, Quality.BAD, "no trend mapping") for p in unmapped}
        if not keyed:
            return out
        try:
            since = datetime.now(timezone.utc) - timedelta(minutes=self.lookback_min)
            data = self.client.query(list(keyed), since)
            latest = self.client.latest_samples(data)
        except Exception as exc:
            for p in keyed.values():
                out[p.id] = make_reading(p, None, Quality.BAD, f"inteliscada: {exc}")
            return out
        now = datetime.now(timezone.utc)
        for tid, p in keyed.items():
            if tid not in latest:
                out[p.id] = make_reading(p, None, Quality.BAD, "no samples in window")
                continue
            ts, val = latest[tid]
            if p.type == "boolean" and val is not None:
                val = bool(val)
            r = make_reading(p, val)
            r.source_ts = ts
            if (now - ts).total_seconds() > self.stale_after_s:
                r.quality = Quality.STALE
            out[p.id] = r
        return out

    def write(self, command: CommandDefinition, args: dict[str, Any]) -> None:
        raise PermissionError("InteliSCADA adapter is read-only by design")
