"""
Autopilot: the periodic dispatch loop.

Every `interval_s` it runs the EMS and submits the plan's steps:
  - C2 setpoints  -> as the `automation` identity. They execute only if the site is
                     in AUTONOMOUS mode and the (device, command) is on the allowlist.
  - C3 transitions (genset start/stop) -> as `ai` identity: always queued for a human.
Any failure state (bad telemetry, rejected writes, ambiguous readback) trips the
loop into RESTRICTED behaviour: it stops submitting and raises an alert. Local
control (InteliNeo/PLC) keeps the plant running regardless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..registry.models import OperatingMode, RiskClass
from ..site import Site
from .ems import Goal, Plan
from .gateway import Status
from .policy import Caller

AUTOMATION = Caller("automation:ems", "automation")
AI = Caller("ai:autopilot", "ai")


@dataclass
class AutopilotState:
    enabled: bool = False
    goal: Goal = field(default_factory=Goal)
    interval_s: float = 300.0
    last_plan: Plan | None = None
    consecutive_failures: int = 0
    tripped: bool = False
    trip_reason: str = ""
    log: list[str] = field(default_factory=list)


class Autopilot:
    def __init__(self, site: Site, notify: Callable[[str], None] | None = None):
        self.site = site
        self.state = AutopilotState()
        self.notify = notify or (lambda m: None)

    def set_goal(self, goal: Goal) -> None:
        self.state.goal = goal
        self.state.log.append(f"{self.site.clock().isoformat()} goal {goal}")

    def enable(self) -> None:
        self.state.enabled, self.state.tripped, self.state.consecutive_failures = True, False, 0

    def disable(self, why: str = "operator") -> None:
        self.state.enabled = False
        self.state.log.append(f"{self.site.clock().isoformat()} disabled: {why}")

    def trip(self, why: str) -> None:
        self.state.tripped, self.state.trip_reason, self.state.enabled = True, why, False
        self.site.audit.record("autopilot_tripped", reason=why)
        self.site.store.event("autopilot_tripped", why, severity="error", ts=self.site.clock())
        self.notify(f"AUTOPILOT TRIPPED: {why}. Plant continues on local control. Operator action required.")

    def tick(self) -> Plan | None:
        if not self.state.enabled or self.state.tripped:
            return None
        site = self.site
        plan = site.ems.plan(self.state.goal)
        self.state.last_plan = plan
        if not plan.feasible:
            self.trip("planner found no feasible dispatch: " + "; ".join(plan.warnings))
            return plan

        failures = 0
        pending = {(c.device_id, c.command_id) for c in site.gateway.pending()}
        transitions_this_tick: set[str] = set()
        for st in plan.steps:
            dev = site.registry.get(st.device_id)
            cdef = dev.command(st.command_id)
            if cdef is None:
                continue
            is_c2 = cdef.risk_class == RiskClass.C2_SETPOINT
            # A setpoint that follows a start/stop on the same device waits until that
            # transition is verified (next tick) — otherwise it's rejected as "not running".
            if is_c2 and (st.device_id in transitions_this_tick or any(d == st.device_id for d, _ in pending)):
                self.state.log.append(f"{site.clock().isoformat()} defer {st.device_id}.{st.command_id} until transition verified")
                continue
            if is_c2 and site.registry.policy.mode == OperatingMode.AUTONOMOUS:
                c = site.gateway.submit(AUTOMATION, st.device_id, st.command_id, st.args, reason=f"autopilot: {st.why}")
                if c.status in (Status.FAILED, Status.AMBIGUOUS):
                    failures += 1          # a real write problem — these trip the loop
                    self.notify(f"autopilot: {st.device_id}.{st.command_id} -> {c.status.value}: {c.error}")
                elif c.status == Status.REJECTED:
                    # the policy engine doing its job; logged, not a trip condition
                    self.state.log.append(f"{site.clock().isoformat()} rejected {st.device_id}.{st.command_id}: {c.error}")
            else:
                # C3 or site not autonomous: queue for a human, never execute silently
                if (st.device_id, st.command_id) in pending:
                    continue               # already waiting for approval; don't spam
                c = site.gateway.submit(AI, st.device_id, st.command_id, st.args, reason=f"autopilot proposes: {st.why}")
                if c.status == Status.AWAITING_APPROVAL:
                    transitions_this_tick.add(st.device_id)
                    self.notify(f"approval needed: {st.device_id}.{st.command_id} {st.args} — {st.why} (id {c.id})")
        if failures:
            self.state.consecutive_failures += 1
            if self.state.consecutive_failures >= 3:
                self.trip("3 consecutive ticks with failed/ambiguous writes")
        else:
            self.state.consecutive_failures = 0
        return plan
