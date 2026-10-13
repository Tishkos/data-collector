"""
VPS side of the data flow.

  POST /api/v1/ingest              <- site collector pushes batches (X-API-Key: collector key)
  GET  /api/v1/tags                -> catalog: every external tag with latest value+quality
  GET  /api/v1/tags/{name}         -> one tag, latest
  GET  /api/v1/tags/{name}/history -> time series (minutes back, downsampled)
  GET  /api/v1/export.csv          -> all tags latest, CSV (for Excel people)

External consumers (DT Turkey etc.) use the GET endpoints with a read-only key.
They never touch the field devices.
"""

from __future__ import annotations

import csv
import io
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import yaml
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel

from ..adapters.base import Reading
from ..registry.models import Quality
from .app import site

router = APIRouter(prefix="/api/v1")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CATALOG_PATH = os.path.join(ROOT, "profiles", "tag_catalog.yaml")

COLLECTOR_KEY = os.environ.get("QS_COLLECTOR_KEY", "change-me-collector")
READ_KEY = os.environ.get("QS_READ_KEY", "change-me-reader")


def _catalog() -> list[dict[str, Any]]:
    with open(CATALOG_PATH) as f:
        return yaml.safe_load(f)["tags"]


def collector_auth(x_api_key: str = Header(default="")):
    if x_api_key != COLLECTOR_KEY:
        raise HTTPException(401, "bad collector key")


def reader_auth(x_api_key: str = Header(default="")):
    if x_api_key not in (READ_KEY, COLLECTOR_KEY):
        raise HTTPException(401, "bad api key")


# --- ingest ---------------------------------------------------------------------

class Batch(BaseModel):
    ts: str
    devices: dict[str, dict[str, dict[str, Any]]]


class IngestReq(BaseModel):
    batches: list[Batch]


@router.post("/ingest", dependencies=[Depends(collector_auth)])
def ingest(req: IngestReq):
    s = site()
    n = 0
    for b in req.batches:
        for did, points in b.devices.items():
            readings: dict[str, Reading] = {}
            for pid, p in points.items():
                try:
                    ts = datetime.fromisoformat(p.get("ts", b.ts))
                except ValueError:
                    ts = datetime.now(timezone.utc)
                readings[pid] = Reading(point_id=pid, value=p.get("v"), unit="",
                                        source_ts=ts, quality=Quality(p.get("q", "good")), error=p.get("e"))
            s.store.ingest(did, readings)
            n += len(readings)
    return {"accepted_batches": len(req.batches), "points": n}


# --- tags -----------------------------------------------------------------------

def _latest(tag: dict[str, Any]) -> dict[str, Any]:
    s = site()
    r = s.store.latest(tag["device"]).get(tag["point"])
    if r is None:
        return {"name": tag["name"], "value": None, "quality": "bad", "unit": tag["unit"], "ts": None,
                "source": f"{tag['device']}/{tag['point']}", "note": "no data yet"}
    q = r.quality.value
    if q == "good" and r.age_ms() > 5 * 60 * 1000:
        q = "stale"
    return {"name": tag["name"], "value": r.value, "quality": q, "unit": tag["unit"],
            "ts": r.source_ts.isoformat(), "source": f"{tag['device']}/{tag['point']}"}


@router.get("/tags", dependencies=[Depends(reader_auth)])
def tags():
    return [_latest(t) for t in _catalog()]


@router.get("/tags/{name}", dependencies=[Depends(reader_auth)])
def tag(name: str):
    for t in _catalog():
        if t["name"] == name:
            return _latest(t)
    raise HTTPException(404, "unknown tag")


@router.get("/tags/{name}/history", dependencies=[Depends(reader_auth)])
def tag_history(name: str, minutes: int = 1440, max_points: int = 500):
    for t in _catalog():
        if t["name"] == name:
            s = site()
            now = datetime.now(timezone.utc)          # ingested data carries real time, not sim time
            since = now - timedelta(minutes=minutes)
            rows = s.store.history(t["device"], t["point"], since, now)
            step = max(1, len(rows) // max_points)
            return {"name": name, "unit": t["unit"], "rows": rows[::step]}
    raise HTTPException(404, "unknown tag")


@router.get("/export.csv", dependencies=[Depends(reader_auth)])
def export_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["tag", "value", "unit", "quality", "timestamp", "source"])
    for t in _catalog():
        r = _latest(t)
        w.writerow([r["name"], r["value"], r["unit"], r["quality"], r["ts"], r["source"]])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=tags.csv"})
