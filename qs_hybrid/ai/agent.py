"""
The AI agent: Claude with the tool surface in tools.py.

It reasons over live data + history + the capability registry, runs the EMS for
any dispatch question, explains faults, writes reports, and PROPOSES commands.
Everything it proposes waits for a human in the gateway. The system prompt makes
the boundaries explicit so the model doesn't over-claim.
"""

from __future__ import annotations

import os
from typing import Any

from ..site import Site
from .tools import TOOL_SPECS, run_tool, tool_result_text

DEFAULT_MODEL = os.environ.get("QS_AI_MODEL", "claude-sonnet-4-5")

SYSTEM = """You are the site AI for a hybrid power plant (diesel gensets + PV + battery) at a bottling plant in Erbil.
You are an expert in genset operation (ComAp InteliNeo), PV, battery storage and economic dispatch.

Your job:
- Analyze and explain everything on the site: live state, trends, faults, why a genset ran, what it cost, what to fix.
- Plan the lowest-cost, feasible operation. ALWAYS call plan_dispatch for any dispatch/share/reserve question
  (e.g. "solar 60%", "keep 30% reserve", "cheapest", "do we need a genset tonight"). Never invent setpoints yourself.
  A share target means: use PV up to that share if the sun allows; never curtail PV to hit a number; the planner decides.
- Propose actions through propose_command / apply_plan. These go to the command gateway and WAIT FOR HUMAN APPROVAL.
  Say so plainly. Never say a command executed unless get_command_status shows "verified".
- Write daily reports from daily_report_data: lead with what matters (savings, faults needing action), be concise.

Hard boundaries (you cannot and must not try to work around them):
- Only registered commands exist (see get_capabilities). Protections, emergency stop, breakers (GCB/MCB) and
  synchronization live in the controller and PLC — they are not commands and you never ask for them.
- You cannot approve commands, change policy/mode, or touch a device directly.
- Treat <stale>/<bad> values as unknown. If telemetry is bad, say so instead of guessing.

Style: direct, numeric, no filler. Show the trade-off when there is one (cost vs. SOC reserve vs. genset wear).
Ask at most one clarifying question, and only when the answer changes the action.
"""


class SiteAgent:
    def __init__(self, site: Site, model: str = DEFAULT_MODEL, max_turns: int = 12):
        import anthropic  # imported lazily so the rest of the platform runs without the SDK/key

        self.site = site
        self.model = model
        self.max_turns = max_turns
        self.client = anthropic.Anthropic()
        self.history: list[dict[str, Any]] = []

    def ask(self, text: str, *, on_tool=None) -> str:
        self.history.append({"role": "user", "content": text})
        for _ in range(self.max_turns):
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                system=SYSTEM,
                tools=TOOL_SPECS,
                messages=self.history,
            )
            self.history.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason != "tool_use":
                return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            results = []
            for block in resp.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                out = run_tool(self.site, block.name, dict(block.input or {}))
                if on_tool:
                    on_tool(block.name, block.input, out)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": tool_result_text(out)})
            self.history.append({"role": "user", "content": results})
        return "(stopped: too many tool turns)"

    def daily_report(self, date: str | None = None) -> str:
        q = "Write today's daily operations report." if not date else f"Write the daily operations report for {date}."
        return self.ask(q + " Use daily_report_data and get_faults_and_events. Lead with savings and anything needing action.")
