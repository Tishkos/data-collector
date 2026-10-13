"""
The AI's tool surface. These are the ONLY things the model can call.

Read tools return data. `plan_dispatch` runs the deterministic EMS. The two write
tools (`propose_command`, `apply_plan`) submit through the CommandGateway as role
"ai", which the policy engine always routes to human approval. The model cannot
approve, cannot change policy, cannot touch an adapter, and cannot name a register.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from ..analytics.report import build_daily_report
from ..control.ems import Goal
from ..control.policy import Caller
from ..site import Site

AI_CALLER = Caller("ai:claude", "ai")

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "get_site_snapshot",
        "description": "Current live values for every device (gensets, PV, battery, meter), site mode and time. Values shown as <stale>/<bad> are not trustworthy.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_capabilities",
        "description": "The device capability registry: every device, its points, its registered commands with risk classes/args/limits. Only these commands exist.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_history",
        "description": "Time series for one point over the last N minutes (downsampled).",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "point_id": {"type": "string"},
                "minutes": {"type": "integer", "default": 120},
            },
            "required": ["device_id", "point_id"],
        },
    },
    {
        "name": "get_faults_and_events",
        "description": "Active faults plus recent events (faults, cleared faults, commands verified/rejected/ambiguous) over the last N minutes.",
        "input_schema": {"type": "object", "properties": {"minutes": {"type": "integer", "default": 240}}},
    },
    {
        "name": "plan_dispatch",
        "description": (
            "Run the deterministic energy-management planner. Use for any request like 'solar 60%', 'cheapest', "
            "'keep 30% reserve', 'do we need a genset tonight'. Returns the feasible dispatch, the exact registered "
            "commands it implies, explanation, cost/h and alternatives. It never curtails PV to hit a share target."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "objective": {"type": "string", "enum": ["min_cost", "share_target"], "default": "min_cost"},
                "pv_share_pct": {"type": "number"},
                "bess_reserve_pct": {"type": "number"},
                "reserve_margin_kw": {"type": "number", "default": 60},
                "load_kw": {"type": "number", "description": "override load (what-if); omit for live"},
            },
        },
    },
    {
        "name": "propose_command",
        "description": (
            "Submit ONE registered command to the command gateway on behalf of the user. It is validated by the "
            "policy engine and then WAITS FOR HUMAN APPROVAL. Never claim it executed. Returns command_id + status + reasons."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string"},
                "command_id": {"type": "string"},
                "args": {"type": "object"},
                "reason": {"type": "string", "description": "why — shown to the approver and stored in the audit log"},
            },
            "required": ["device_id", "command_id", "reason"],
        },
    },
    {
        "name": "apply_plan",
        "description": "Submit every step of the last plan_dispatch result as commands (each awaits approval). Pass the plan's steps back.",
        "input_schema": {
            "type": "object",
            "properties": {
                "steps": {"type": "array", "items": {"type": "object"}},
                "reason": {"type": "string"},
            },
            "required": ["steps", "reason"],
        },
    },
    {
        "name": "get_command_status",
        "description": "Lifecycle status of a command (submitted/awaiting_approval/verified/rejected/ambiguous...) with readback and reasons.",
        "input_schema": {"type": "object", "properties": {"command_id": {"type": "string"}}, "required": ["command_id"]},
    },
    {
        "name": "daily_report_data",
        "description": "Computed numbers for a day: energy by source, PV share, fuel, cost, savings vs diesel-only, faults, commands. Use to write the daily report.",
        "input_schema": {"type": "object", "properties": {"date": {"type": "string", "description": "YYYY-MM-DD; default today"}}},
    },
]


def _ds(rows: list, n: int = 60) -> list:
    if len(rows) <= n:
        return rows
    k = max(1, len(rows) // n)
    return rows[::k]


def run_tool(site: Site, name: str, inp: dict[str, Any]) -> Any:
    if name == "get_site_snapshot":
        return site.snapshot()
    if name == "get_capabilities":
        return site.registry.capability_inventory()
    if name == "get_history":
        since = site.clock() - timedelta(minutes=int(inp.get("minutes", 120)))
        rows = site.store.history(inp["device_id"], inp["point_id"], since, site.clock())
        return {"device_id": inp["device_id"], "point_id": inp["point_id"], "rows": _ds([(t, v) for t, v in rows])}
    if name == "get_faults_and_events":
        since = site.clock() - timedelta(minutes=int(inp.get("minutes", 240)))
        return {"active_faults": site.faults.active(), "events": site.store.events(since, limit=100)}
    if name == "plan_dispatch":
        g = Goal(
            objective=inp.get("objective", "min_cost"),
            pv_share_pct=inp.get("pv_share_pct"),
            bess_reserve_pct=inp.get("bess_reserve_pct"),
            reserve_margin_kw=float(inp.get("reserve_margin_kw", 60)),
        )
        return site.ems.plan(g, load_kw=inp.get("load_kw")).to_dict()
    if name == "propose_command":
        c = site.gateway.submit(AI_CALLER, inp["device_id"], inp["command_id"], inp.get("args") or {}, reason=inp["reason"])
        return c.to_dict()
    if name == "apply_plan":
        out = []
        for st in inp["steps"]:
            c = site.gateway.submit(AI_CALLER, st["device_id"], st["command_id"], st.get("args") or {},
                                    reason=f"{inp['reason']} — {st.get('why','')}")
            out.append(c.to_dict())
        return out
    if name == "get_command_status":
        c = site.gateway.commands.get(inp["command_id"])
        return c.to_dict() if c else {"error": "unknown command_id"}
    if name == "daily_report_data":
        day = site.clock()
        if inp.get("date"):
            from datetime import datetime, timezone
            day = datetime.fromisoformat(inp["date"]).replace(tzinfo=timezone.utc)
        return build_daily_report(site.registry, site.store, day)
    return {"error": f"unknown tool {name}"}


def tool_result_text(obj: Any) -> str:
    return json.dumps(obj, default=str)[:20000]
