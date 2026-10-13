"""
HTTP API + dashboard. Nothing here can write to a device; the one POST triggers a read.

  /                               dashboard
  /api/v1/health                  collector status
  /api/v1/local/summary           live values, KPIs, device comms, active faults
  POST /api/v1/local/poll         read every device now (a read, not a write)
  /api/v1/local/series            time series (hours= | from= to=, step=, keys=)
  /api/v1/local/alarms            event log (hours=)
  /api/v1/local/tags              every point with its latest value
  /api/v1/local/daily             daily totals (date=YYYY-MM-DD)
  /api/v1/local/export.csv|xlsx   wide table (from=, to=, step=)
  /api/v1/local/report.xlsx       daily report workbook (date=)
  /api/v1/tags                    the DT Turkey tag list with live values
  /api/v1/tags/{name}             one tag
  /api/v1/tags.csv|xlsx           tag list as a file
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import export, views
from .core import Collector

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
MAX_EXPORT_DAYS = 62


def _file(data: bytes, name: str, kind: str) -> Response:
    media = {"csv": "text/csv; charset=utf-8",
             "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}[kind]
    return Response(content=data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


def create_app(c: Collector) -> FastAPI:
    app = FastAPI(title="Plant data collector", version="1.0", docs_url="/api/docs", redoc_url=None)
    app.state.collector = c

    @app.exception_handler(Exception)
    async def _unhandled(request, exc):
        logging.getLogger("collector").exception("request failed: %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: {exc}",
                                                      "path": request.url.path, "hint": "details in data/collector.log"})
    catalog = views.load_tag_catalog()
    app.mount("/static", StaticFiles(directory=os.path.join(WEB_DIR, "static")), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(os.path.join(WEB_DIR, "index.html"))

    @app.get("/api/v1/health")
    def health():
        return {"ok": True, "simulated": c.simulated, "polls": c.polls,
                "last_poll": c.last_poll_at.isoformat() if c.last_poll_at else None,
                "devices": {d: views.device_comms(c, d) for d in c.registry.devices}}

    @app.get("/api/v1/local/summary")
    def summary():
        return views.summary(c)

    @app.post("/api/v1/local/poll")
    def poll_now():
        """Read every device right now (the only POST; it reads, it does not write)."""
        t0 = time.time()
        c.poll()
        devs = {d: views.device_comms(c, d) for d in c.registry.devices}
        return {"ok": True, "took_s": round(time.time() - t0, 2), "time": c.last_poll_at.isoformat(),
                "devices": devs, "answering": sum(1 for v in devs.values() if v == "ok"), "total": len(devs)}

    @app.get("/api/v1/local/series")
    def series(hours: float | None = Query(None, gt=0, le=24 * MAX_EXPORT_DAYS), frm: str | None = Query(None, alias="from"),
               to: str | None = None, step: int | None = Query(None, ge=10), keys: str | None = None):
        since, until = views.parse_range(frm, to, hours, c.now())
        if until - since > timedelta(days=MAX_EXPORT_DAYS):
            raise HTTPException(400, f"range longer than {MAX_EXPORT_DAYS} days")
        return views.series(c, since, until, step, keys.split(",") if keys else None)

    @app.get("/api/v1/local/alarms")
    def alarms(hours: float = Query(24, gt=0, le=24 * 366), limit: int = Query(300, ge=1, le=5000)):
        since = c.now() - timedelta(hours=hours)
        return {"active": c.active_issues(), "events": c.store.events(since, limit)}

    @app.get("/api/v1/local/tags")
    def tags():
        return views.tags(c)

    @app.get("/api/v1/local/daily")
    def daily(date: str | None = None):
        day = datetime.fromisoformat(date).replace(tzinfo=timezone.utc) if date else None
        return c.daily_totals(day)

    @app.get("/api/v1/local/export.{fmt}")
    def export_window(fmt: str, frm: str | None = Query(None, alias="from"), to: str | None = None,
                      step: int = Query(300, ge=10), hours: float | None = Query(None, gt=0)):
        if fmt not in ("csv", "xlsx"):
            raise HTTPException(404)
        since, until = views.parse_range(frm, to, hours, c.now())
        if until - since > timedelta(days=MAX_EXPORT_DAYS):
            raise HTTPException(400, f"range longer than {MAX_EXPORT_DAYS} days; export in pieces")
        headers, rows = export.wide_table(c, since, until, step)
        name = f"{c.registry.site.site_id}_{since.strftime('%Y%m%d-%H%M')}_{until.strftime('%Y%m%d-%H%M')}_{step}s.{fmt}"
        data = export.to_csv(headers, rows) if fmt == "csv" else export.to_xlsx(c, headers, rows, since, until, step)
        return _file(data, name, fmt)

    @app.get("/api/v1/local/report.xlsx")
    def report(date: str | None = None):
        day = datetime.fromisoformat(date).replace(tzinfo=timezone.utc) if date else c.now()
        return _file(export.daily_report_xlsx(c, day), f"{c.registry.site.site_id}_daily_{day.strftime('%Y-%m-%d')}.xlsx", "xlsx")

    # --- the tag list DT Turkey asked for --------------------------------------------------------

    @app.get("/api/v1/tags")
    def tag_values():
        return {"version": "1.2", "count": len(catalog), "time": c.now().isoformat(),
                "tags": views.catalog_values(c, catalog)}

    @app.get("/api/v1/tags.{fmt}")
    def tag_file(fmt: str):
        if fmt == "csv":
            return _file(export.tags_csv(c, catalog), "tags.csv", "csv")
        if fmt == "xlsx":
            return _file(export.tags_xlsx(c, catalog), "tags.xlsx", "xlsx")
        raise HTTPException(404)

    @app.get("/api/v1/tags/{name}")
    def one_tag(name: str):
        for t in views.catalog_values(c, catalog):
            if t["name"].lower() == name.lower():
                return t
        raise HTTPException(404, f"no tag named {name}")

    return app
