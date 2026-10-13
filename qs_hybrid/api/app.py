"""
Application API (FastAPI). The browser HMI, the AI and the optimizer all use this
same command path. Auth here is a minimal header-based RBAC stub — replace with
your identity provider before any live deployment (see docs/SAFETY.md).

Run:  uvicorn qs_hybrid.api.app:app --reload
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from ..analytics.report import build_daily_report
from ..control.autopilot import Autopilot
from ..control.ems import Goal
from ..control.policy import Caller
from ..registry.models import OperatingMode
from ..site import Site

app = FastAPI(title="QS Hybrid Site Platform", version="0.1.0")

_site: Site | None = None
_autopilot: Autopilot | None = None


def site() -> Site:
    global _site, _autopilot
    if _site is None:
        _site = Site(os.environ.get("QS_SITE_CONFIG", "config/site.yaml"), os.environ.get("QS_DATA_DIR", "./data"))
        _autopilot = Autopilot(_site)
    return _site


def autopilot() -> Autopilot:
    site()
    assert _autopilot is not None
    return _autopilot


# --- auth stub -------------------------------------------------------------------
# X-User: "name", X-Role: viewer|operator|engineer. Replace with OIDC/JWT.

def caller(x_user: str = Header(default="anonymous"), x_role: str = Header(default="viewer")) -> Caller:
    if x_role not in ("viewer", "operator", "engineer"):
        raise HTTPException(403, "invalid role")
    return Caller(f"user:{x_user}", x_role)


# --- read ----------------------------------------------------------------------------

@app.get("/api/v1/site")
def get_site(s: Site = Depends(site)):
    return s.snapshot()


@app.get("/api/v1/devices")
def devices(s: Site = Depends(site)):
    return [{"id": d.id, "kind": d.kind, "model": f"{d.vendor} {d.model}", "adapter": d.adapter,
             "profile_status": d.profile_status} for d in s.registry.devices.values()]


@app.get("/api/v1/devices/{device_id}/capabilities")
def capabilities(device_id: str, s: Site = Depends(site)):
    inv = [c for c in s.registry.capability_inventory() if c["device_id"] == device_id]
    if not inv:
        raise HTTPException(404, "unknown device")
    return inv[0]


@app.get("/api/v1/devices/{device_id}/points")
def points(device_id: str, s: Site = Depends(site)):
    s.poll_device(device_id)
    return {pid: {"value": r.value, "unit": r.unit, "quality": r.quality.value, "ts": r.source_ts.isoformat(), "error": r.error}
            for pid, r in s.store.latest(device_id).items()}


@app.get("/api/v1/devices/{device_id}/history/{point_id}")
def history(device_id: str, point_id: str, minutes: int = 120, s: Site = Depends(site)):
    since = s.clock() - timedelta(minutes=minutes)
    return s.store.history(device_id, point_id, since, s.clock())


@app.get("/api/v1/alarms")
def alarms(minutes: int = 240, s: Site = Depends(site)):
    return {"active": s.faults.active(), "events": s.store.events(s.clock() - timedelta(minutes=minutes), kind="fault")}


@app.get("/api/v1/audit-events")
def audit_events(limit: int = 100, c: Caller = Depends(caller), s: Site = Depends(site)):
    if c.role == "viewer":
        raise HTTPException(403, "audit requires operator/engineer")
    ok, n = s.audit.verify()
    return {"chain_ok": ok, "records": n, "tail": s.audit.tail(limit)}


@app.get("/api/v1/reports/daily")
def daily(date: str | None = None, s: Site = Depends(site)):
    day = s.clock()
    if date:
        from datetime import datetime, timezone
        day = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
    return build_daily_report(s.registry, s.store, day)


# --- plan ---------------------------------------------------------------------------

class PlanReq(BaseModel):
    objective: str = "min_cost"
    pv_share_pct: float | None = None
    bess_reserve_pct: float | None = None
    reserve_margin_kw: float = 60.0
    load_kw: float | None = None


@app.post("/api/v1/plan")
def plan(req: PlanReq, s: Site = Depends(site)):
    g = Goal(objective=req.objective, pv_share_pct=req.pv_share_pct, bess_reserve_pct=req.bess_reserve_pct,
             reserve_margin_kw=req.reserve_margin_kw)
    return s.ems.plan(g, load_kw=req.load_kw).to_dict()


# --- commands -------------------------------------------------------------------------

class CommandReq(BaseModel):
    device_id: str
    command_id: str
    args: dict[str, Any] = {}
    reason: str = ""
    idempotency_key: str | None = None


@app.post("/api/v1/commands", status_code=202)
def submit(req: CommandReq, c: Caller = Depends(caller), s: Site = Depends(site)):
    cmd = s.gateway.submit(c, req.device_id, req.command_id, req.args, reason=req.reason, idempotency_key=req.idempotency_key)
    return cmd.to_dict()


@app.get("/api/v1/commands")
def list_commands(s: Site = Depends(site)):
    return [c.to_dict() for c in list(s.gateway.commands.values())[-200:]]


@app.get("/api/v1/commands/{command_id}")
def get_command(command_id: str, s: Site = Depends(site)):
    cmd = s.gateway.commands.get(command_id)
    if not cmd:
        raise HTTPException(404, "unknown command")
    return cmd.to_dict()


@app.post("/api/v1/commands/{command_id}/approve")
def approve(command_id: str, c: Caller = Depends(caller), s: Site = Depends(site)):
    try:
        return s.gateway.approve(command_id, c).to_dict()
    except (KeyError, ValueError) as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/v1/commands/{command_id}/cancel")
def cancel(command_id: str, c: Caller = Depends(caller), s: Site = Depends(site)):
    try:
        return s.gateway.cancel(command_id, c).to_dict()
    except (KeyError, ValueError) as exc:
        raise HTTPException(409, str(exc))


# --- mode + autopilot ----------------------------------------------------------------------

class ModeReq(BaseModel):
    mode: OperatingMode


@app.post("/api/v1/site/mode")
def set_mode(req: ModeReq, c: Caller = Depends(caller), s: Site = Depends(site)):
    if c.role != "engineer":
        raise HTTPException(403, "only engineers change site mode")
    s.registry.policy.mode = req.mode
    s.audit.record("site_mode_changed", mode=req.mode.value, by=c.principal)
    return {"mode": req.mode.value}


class AutopilotReq(BaseModel):
    enabled: bool
    objective: str = "min_cost"
    pv_share_pct: float | None = None
    bess_reserve_pct: float | None = None
    interval_s: float = 300


@app.post("/api/v1/automations/autopilot")
def set_autopilot(req: AutopilotReq, c: Caller = Depends(caller), ap: Autopilot = Depends(autopilot)):
    if c.role not in ("operator", "engineer"):
        raise HTTPException(403, "operator/engineer only")
    ap.set_goal(Goal(objective=req.objective, pv_share_pct=req.pv_share_pct, bess_reserve_pct=req.bess_reserve_pct))
    ap.state.interval_s = req.interval_s
    ap.enable() if req.enabled else ap.disable(c.principal)
    ap.site.audit.record("autopilot_set", enabled=req.enabled, goal=str(ap.state.goal), by=c.principal)
    return {"enabled": ap.state.enabled, "goal": ap.state.goal.__dict__, "tripped": ap.state.tripped}


@app.post("/api/v1/automations/autopilot/tick")
def tick(ap: Autopilot = Depends(autopilot)):
    p = ap.tick()
    return {"ran": p is not None, "plan": p.to_dict() if p else None, "tripped": ap.state.tripped, "trip_reason": ap.state.trip_reason}


# --- AI chat --------------------------------------------------------------------------------

class ChatReq(BaseModel):
    message: str


_agent = None


@app.post("/api/v1/chat")
def chat(req: ChatReq, c: Caller = Depends(caller), s: Site = Depends(site)):
    global _agent
    if _agent is None:
        from ..ai.agent import SiteAgent
        _agent = SiteAgent(s)
    return {"reply": _agent.ask(f"[{c.principal}, role {c.role}] {req.message}")}


# --- data flow: site collector -> ingest -> tags API ------------------------------------
from .tags import router as tags_router  # noqa: E402

app.include_router(tags_router)

# --- local dashboard (plant PC, no cloud) ---------------------------------------------------
from fastapi.staticfiles import StaticFiles  # noqa: E402

from .local import WEB_DIR, router as local_router  # noqa: E402

app.include_router(local_router)
app.mount("/static", StaticFiles(directory=os.path.join(WEB_DIR, "static")), name="static")
