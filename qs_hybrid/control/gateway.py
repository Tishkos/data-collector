"""
Command gateway — the ONLY path from anything (HMI, AI, optimizer) to a device write.

Lifecycle:  submitted -> validated -> awaiting_approval -> approved -> rechecked
            -> executing -> verifying -> verified | failed | ambiguous
Every transition is audited. Acceptance is never reported as execution.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable

from ..adapters.base import Adapter
from ..registry.loader import Registry
from ..registry.models import CommandDefinition, Device, Quality
from ..telemetry.store import TelemetryStore
from .audit import AuditLog
from .policy import Caller, Decision, PolicyEngine


class Status(str, Enum):
    SUBMITTED = "submitted"
    REJECTED = "rejected"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"      # write sent, readback didn't confirm within timeout
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass
class Command:
    id: str
    device_id: str
    command_id: str
    args: dict[str, Any]
    caller: Caller
    reason: str
    status: Status = Status.SUBMITTED
    risk_class: str = ""
    decision: Decision | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    executed_at: datetime | None = None
    verified_at: datetime | None = None
    readback: Any = None
    error: str | None = None
    idempotency_key: str | None = None

    def to_dict(self) -> dict:
        return {
            "command_id": self.id, "device_id": self.device_id, "command": self.command_id,
            "args": self.args, "status": self.status.value, "risk_class": self.risk_class,
            "caller": self.caller.principal, "reason": self.reason,
            "reasons": self.decision.reasons if self.decision else [],
            "requires_approval": self.decision.requires_approval if self.decision else None,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "approved_by": self.approved_by, "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
            "readback": self.readback, "error": self.error,
        }


class CommandGateway:
    def __init__(
        self,
        registry: Registry,
        store: TelemetryStore,
        adapters: dict[str, Adapter],
        policy: PolicyEngine,
        audit: AuditLog,
        *,
        clock: Callable[[], datetime] | None = None,
        refresh: Callable[[str], None] | None = None,   # re-read one device's telemetry now
        sleep: Callable[[float], None] | None = None,
    ):
        self.reg = registry
        self.store = store
        self.adapters = adapters
        self.policy = policy
        self.audit = audit
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.refresh = refresh or (lambda did: None)
        self.sleep = sleep or (lambda s: None)
        self.commands: dict[str, Command] = {}
        self._idem: dict[str, str] = {}

    # --- submit ---------------------------------------------------------------

    def submit(self, caller: Caller, device_id: str, command_id: str, args: dict[str, Any] | None = None,
               *, reason: str = "", idempotency_key: str | None = None, ttl_s: float | None = None) -> Command:
        args = dict(args or {})
        if idempotency_key and idempotency_key in self._idem:
            return self.commands[self._idem[idempotency_key]]

        cmd = Command(id=f"cmd-{uuid.uuid4().hex[:8]}", device_id=device_id, command_id=command_id,
                      args=args, caller=caller, reason=reason, idempotency_key=idempotency_key)
        cmd.created_at = self.clock()
        cmd.expires_at = cmd.created_at + timedelta(seconds=ttl_s or self.reg.policy.approval_ttl_s)
        self.commands[cmd.id] = cmd
        if idempotency_key:
            self._idem[idempotency_key] = cmd.id
        self.audit.record("command_submitted", command_id=cmd.id, device=device_id, command=command_id,
                          args=args, caller=caller.principal, role=caller.role, reason=reason)

        # resolve registry objects
        try:
            device = self.reg.get(device_id)
        except KeyError as exc:
            return self._reject(cmd, str(exc))
        cdef = device.command(command_id)
        if cdef is None:
            return self._reject(cmd, f"'{command_id}' is not a registered command on {device_id}")
        cmd.risk_class = cdef.risk_class.value

        # validate
        self.refresh(device_id)
        dec = self.policy.evaluate(caller, device, cdef, args)
        cmd.decision = dec
        cmd.args = dec.clamped_args
        if not dec.allowed:
            return self._reject(cmd, "; ".join(dec.reasons))
        self.audit.record("command_validated", command_id=cmd.id, risk_class=cmd.risk_class,
                          requires_approval=dec.requires_approval, notes=dec.reasons)

        if dec.requires_approval:
            cmd.status = Status.AWAITING_APPROVAL
            return cmd
        return self._run(cmd, device, cdef)

    def _reject(self, cmd: Command, why: str) -> Command:
        cmd.status, cmd.error = Status.REJECTED, why
        self.audit.record("command_rejected", command_id=cmd.id, error=why)
        self.store.event("command_rejected", why, device_id=cmd.device_id, severity="warning", code=cmd.command_id, ts=self.clock())
        return cmd

    # --- approve / cancel -----------------------------------------------------------

    def approve(self, command_id: str, approver: Caller) -> Command:
        cmd = self.commands[command_id]
        if cmd.status != Status.AWAITING_APPROVAL:
            raise ValueError(f"command is {cmd.status.value}, not awaiting approval")
        if cmd.expires_at and self.clock() > cmd.expires_at:
            cmd.status = Status.EXPIRED
            self.audit.record("command_expired", command_id=cmd.id)
            raise ValueError("approval window expired; resubmit")
        if approver.principal == cmd.caller.principal and approver.role != "engineer":
            raise ValueError("a request cannot be approved by its own submitter")
        if approver.role not in ("operator", "engineer"):
            raise ValueError(f"role '{approver.role}' cannot approve")
        device = self.reg.get(cmd.device_id)
        cdef = device.command(cmd.command_id)
        assert cdef is not None
        if cdef.risk_class.value not in self.reg.policy.roles.get(approver.role, []):
            raise ValueError(f"role '{approver.role}' cannot approve {cdef.risk_class.value}")

        cmd.approved_by, cmd.approved_at, cmd.status = approver.principal, self.clock(), Status.APPROVED
        self.audit.record("command_approved", command_id=cmd.id, approver=approver.principal)

        # RECHECK: preconditions again, right before execution; state may have changed
        self.refresh(cmd.device_id)
        dec = self.policy.evaluate(cmd.caller, device, cdef, cmd.args)
        if not dec.allowed:
            return self._reject(cmd, "recheck failed after approval: " + "; ".join(dec.reasons))
        return self._run(cmd, device, cdef)

    def cancel(self, command_id: str, who: Caller) -> Command:
        cmd = self.commands[command_id]
        if cmd.status in (Status.AWAITING_APPROVAL, Status.SUBMITTED):
            cmd.status = Status.CANCELLED
            self.audit.record("command_cancelled", command_id=cmd.id, by=who.principal)
        else:
            raise ValueError(f"cannot cancel a command in state {cmd.status.value}")
        return cmd

    # --- execute + verify ---------------------------------------------------------

    def _run(self, cmd: Command, device: Device, cdef: CommandDefinition) -> Command:
        adapter = self.adapters[device.id]
        cmd.status, cmd.executed_at = Status.EXECUTING, self.clock()
        self.audit.record("command_executing", command_id=cmd.id, args=cmd.args)
        try:
            adapter.write(cdef, cmd.args)
        except Exception as exc:
            cmd.status, cmd.error = Status.FAILED, f"write error: {exc}"
            self.audit.record("command_failed", command_id=cmd.id, error=cmd.error)
            self.store.event("command_failed", cmd.error, device_id=device.id, severity="error", code=cdef.id, ts=self.clock())
            return cmd
        self.policy.note_executed(device, cdef, cmd.caller)

        if cdef.verification is None:
            cmd.status, cmd.verified_at = Status.VERIFIED, self.clock()
            self.audit.record("command_verified", command_id=cmd.id, note="no verification defined")
            return cmd

        cmd.status = Status.VERIFYING
        ok, readback = self._verify(device, cdef, cmd.args)
        cmd.readback = readback
        if ok:
            cmd.status, cmd.verified_at = Status.VERIFIED, self.clock()
            self.audit.record("command_verified", command_id=cmd.id, readback=readback)
            self.store.event("command_verified", f"{cdef.id} {cmd.args} readback={readback}",
                             device_id=device.id, code=cdef.id, ts=self.clock())
        else:
            # Do NOT retry blindly and do NOT auto-revert: a human must reconcile.
            cmd.status = Status.AMBIGUOUS
            cmd.error = f"readback did not confirm within {cdef.verification.timeout_s}s (got {readback})"
            self.audit.record("command_ambiguous", command_id=cmd.id, readback=readback)
            self.store.event("command_ambiguous", cmd.error, device_id=device.id, severity="error", code=cdef.id, ts=self.clock())
        return cmd

    def _verify(self, device: Device, cdef: CommandDefinition, args: dict[str, Any]) -> tuple[bool, Any]:
        v = cdef.verification
        assert v is not None
        deadline = self.clock() + timedelta(seconds=v.timeout_s)
        expected = v.expected if v.strategy == "state_equals" else next(iter(args.values()), None)
        last = None
        while True:
            self.refresh(device.id)
            val, q = self.store.value(device.id, v.point, now=self.clock())
            last = val
            if q == Quality.GOOD and val is not None:
                if v.strategy == "state_equals" and bool(val) == bool(expected):
                    return True, val
                if v.strategy == "readback_equals" and str(val) == str(expected):
                    return True, val
                if v.strategy == "readback_within":
                    try:
                        if abs(float(val) - float(expected)) <= v.tolerance:
                            return True, val
                    except (TypeError, ValueError):
                        pass
            if self.clock() >= deadline:
                return False, last
            self.sleep(1.0)

    # --- queries ---------------------------------------------------------------

    def pending(self) -> list[Command]:
        return [c for c in self.commands.values() if c.status == Status.AWAITING_APPROVAL]
