"""
Telemetry store: live snapshot (authoritative for control decisions) + SQLite history.

The control plane reads `latest()` and checks age/quality itself — it never
trusts the historian as the live state.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone

from ..adapters.base import Reading
from ..registry.models import Quality


class TelemetryStore:
    def __init__(self, db_path: str = "./data/telemetry.sqlite"):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS readings (
                   ts TEXT NOT NULL, device_id TEXT NOT NULL, point_id TEXT NOT NULL,
                   value REAL, text_value TEXT, quality TEXT NOT NULL, error TEXT)"""
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS ix_readings ON readings(device_id, point_id, ts)")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS events (
                   ts TEXT NOT NULL, kind TEXT NOT NULL, device_id TEXT, severity TEXT,
                   code TEXT, message TEXT, data TEXT)"""
        )
        self._db.commit()
        self._latest: dict[str, dict[str, Reading]] = {}

    # --- write --------------------------------------------------------------

    def ingest(self, device_id: str, readings: dict[str, Reading]) -> None:
        with self._lock:
            self._latest.setdefault(device_id, {}).update(readings)
            rows = []
            for r in readings.values():
                num = r.value if isinstance(r.value, (int, float)) and not isinstance(r.value, bool) else (
                    1.0 if r.value is True else 0.0 if r.value is False else None
                )
                txt = None if num is not None else (None if r.value is None else str(r.value))
                rows.append((r.source_ts.isoformat(), device_id, r.point_id, num, txt, r.quality.value, r.error))
            self._db.executemany("INSERT INTO readings VALUES (?,?,?,?,?,?,?)", rows)
            self._db.commit()

    def event(self, kind: str, message: str, *, device_id: str | None = None, severity: str = "info",
              code: str | None = None, data: str | None = None, ts: datetime | None = None) -> None:
        ts = ts or datetime.now(timezone.utc)
        with self._lock:
            self._db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)",
                             (ts.isoformat(), kind, device_id, severity, code, message, data))
            self._db.commit()

    # --- read ---------------------------------------------------------------

    def latest(self, device_id: str) -> dict[str, Reading]:
        return dict(self._latest.get(device_id, {}))

    def value(self, device_id: str, point_id: str, *, max_age_ms: float | None = None, now: datetime | None = None):
        """Return (value, quality). Quality degrades to STALE when older than max_age_ms."""
        r = self._latest.get(device_id, {}).get(point_id)
        if r is None:
            return None, Quality.BAD
        q = r.quality
        if max_age_ms is not None and q == Quality.GOOD and r.age_ms(now) > max_age_ms:
            q = Quality.STALE
        return r.value, q

    def history(self, device_id: str, point_id: str, since: datetime, until: datetime | None = None) -> list[tuple[str, float | None]]:
        until = until or datetime.now(timezone.utc)
        cur = self._db.execute(
            "SELECT ts, value FROM readings WHERE device_id=? AND point_id=? AND ts>=? AND ts<=? ORDER BY ts",
            (device_id, point_id, since.isoformat(), until.isoformat()),
        )
        return cur.fetchall()

    def events(self, since: datetime, kind: str | None = None, limit: int = 500) -> list[dict]:
        q = "SELECT ts, kind, device_id, severity, code, message, data FROM events WHERE ts>=?"
        args: list = [since.isoformat()]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        cols = ["ts", "kind", "device_id", "severity", "code", "message", "data"]
        return [dict(zip(cols, row)) for row in self._db.execute(q, args).fetchall()]

    def daily_energy(self, device_id: str, point_id: str, day: datetime) -> float:
        """Integrate a kW series over a day -> kWh (trapezoid on timestamps)."""
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = self.history(device_id, point_id, start, start.replace(hour=23, minute=59, second=59))
        kwh = 0.0
        for (t0, v0), (t1, v1) in zip(rows, rows[1:]):
            if v0 is None or v1 is None:
                continue
            dt_h = (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() / 3600.0
            kwh += (v0 + v1) / 2.0 * dt_h
        return round(kwh, 1)
