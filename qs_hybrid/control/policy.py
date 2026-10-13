"""
Policy engine: decides whether a command MAY run. Deterministic, no AI involved.

Checks, in order:
  1. command exists on the device and is not a forbidden class  (registry already guarantees C4 is unloadable)
  2. caller's role is allowed that risk class
  3. site mode permits the command; autonomous callers must be on the allowlist
  4. argument schema + commissioned limits
  5. every named precondition on the command (functions below)
  6. rate limit per device+command
Returns a Decision with every reason, so the audit trail and the AI can explain it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..registry.loader import Registry
from ..registry.models import CommandDefinition, Device, OperatingMode, Quality, RiskClass
from ..telemetry.store import TelemetryStore


@dataclass
class Decision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    requires_approval: bool = False
    risk_class: str = ""
    clamped_args: dict[str, Any] = field(default_factory=dict)


@dataclass
class Caller:
    principal: str          # "operator:tishko", "automation:ems", "ai:claude"
    role: str               # viewer | operator | engineer | automation


class PolicyEngine:
    def __init__(self, registry: Registry, store: TelemetryStore, clock: Callable[[], datetime] | None = None):
        self.reg = registry
        self.store = store
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._last_exec: dict[tuple[str, str], datetime] = {}
        self._auto_times: list[datetime] = []

    # --- public ---------------------------------------------------------------

    def evaluate(self, caller: Caller, device: Device, cmd: CommandDefinition, args: dict[str, Any]) -> Decision:
        d = Decision(allowed=True, risk_class=cmd.risk_class.value, clamped_args=dict(args))
        pol = self.reg.policy

        # 2. role
        if cmd.risk_class.value not in pol.roles.get(caller.role, []):
            d.allowed = False
            d.reasons.append(f"role '{caller.role}' may not issue {cmd.risk_class.value} commands")

        # 3. mode
        mode = pol.mode
        if mode == OperatingMode.RESTRICTED:
            d.allowed = False
            d.reasons.append("site is in RESTRICTED mode: no new writes")
        elif mode not in cmd.allowed_modes:
            d.allowed = False
            d.reasons.append(f"command not allowed in site mode '{mode.value}'")
        if caller.role == "automation":
            on_list = any(a.get("device") == device.id and a.get("command") == cmd.id for a in pol.autonomous_allowlist)
            if mode != OperatingMode.AUTONOMOUS:
                d.allowed = False
                d.reasons.append("automation may only execute when site mode is 'autonomous'")
            elif not on_list:
                d.allowed = False
                d.reasons.append(f"{device.id}.{cmd.id} is not on the autonomous allowlist")
            else:
                now = self.clock()
                self._auto_times = [t for t in self._auto_times if now - t < timedelta(hours=1)]
                if len(self._auto_times) >= pol.max_automated_commands_per_hour:
                    d.allowed = False
                    d.reasons.append("automated command budget for this hour exhausted")

        # 4. args
        for name, spec in cmd.args.items():
            if name not in args:
                d.allowed = False
                d.reasons.append(f"missing argument '{name}'")
                continue
            v = args[name]
            if spec.type == "number":
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    d.allowed = False
                    d.reasons.append(f"argument '{name}' must be a number")
                    continue
                lo, hi = spec.min, spec.max
                if lo is not None and v < lo:
                    d.reasons.append(f"{name}={v} below min {lo}; clamped")
                    v = lo
                if hi is not None and v > hi:
                    d.reasons.append(f"{name}={v} above max {hi}; clamped")
                    v = hi
                d.clamped_args[name] = v
            elif spec.type == "enum" and spec.choices and str(v) not in spec.choices:
                d.allowed = False
                d.reasons.append(f"argument '{name}'={v} not in {spec.choices}")
        extra = set(args) - set(cmd.args)
        if extra:
            d.allowed = False
            d.reasons.append(f"unexpected arguments {sorted(extra)}")

        # 5. preconditions
        for name in cmd.preconditions:
            fn = PRECONDITIONS.get(name)
            if fn is None:
                d.allowed = False
                d.reasons.append(f"unknown precondition '{name}' (refusing rather than guessing)")
                continue
            ok, why = fn(self, device, cmd, d.clamped_args)
            if not ok:
                d.allowed = False
                d.reasons.append(f"precondition '{name}' failed: {why}")

        # 6. rate limit
        last = self._last_exec.get((device.id, cmd.id))
        if last and (self.clock() - last).total_seconds() < cmd.min_interval_s:
            d.allowed = False
            d.reasons.append(f"rate limit: {cmd.id} on {device.id} ran {int((self.clock()-last).total_seconds())}s ago (min {int(cmd.min_interval_s)}s)")

        d.requires_approval = cmd.requires_approval and caller.role != "automation" or (
            caller.role == "automation" and cmd.requires_approval
        )
        if caller.role in ("ai",):
            d.requires_approval = True   # an AI proposal always needs a human unless running as automation on the allowlist
        return d

    def note_executed(self, device: Device, cmd: CommandDefinition, caller: Caller) -> None:
        now = self.clock()
        self._last_exec[(device.id, cmd.id)] = now
        if caller.role == "automation":
            self._auto_times.append(now)

    # --- helpers used by preconditions -----------------------------------------

    def _val(self, device: Device, pid: str):
        p = device.point(pid)
        return self.store.value(device.id, pid, max_age_ms=p.max_age_ms if p else None, now=self.clock())

    def _fresh(self, device: Device) -> tuple[bool, str]:
        latest = self.store.latest(device.id)
        if not latest:
            return False, "no telemetry at all"
        bad = [r.point_id for r in latest.values() if r.quality != Quality.GOOD]
        stale = [p.id for p in device.points if (r := latest.get(p.id)) and r.quality == Quality.GOOD and r.age_ms(self.clock()) > p.max_age_ms]
        if bad:
            return False, f"bad quality on {bad[:4]}"
        if stale:
            return False, f"stale: {stale[:4]}"
        return True, ""


# --- named preconditions ------------------------------------------------------
# signature: (engine, device, cmd, args) -> (ok, reason)

def _device_healthy(e: PolicyEngine, dev: Device, cmd, args):
    v, q = e._val(dev, "alarm_active")
    if dev.point("alarm_active") is None:
        return True, ""
    if q != Quality.GOOD:
        return False, "alarm status unknown"
    if v and cmd.id != "fault_reset":
        return False, "device has an active alarm"
    return True, ""


def _telemetry_fresh(e, dev, cmd, args):
    return e._fresh(dev)


def _genset_running(e, dev, cmd, args):
    v, q = e._val(dev, "running")
    return (bool(v) and q == Quality.GOOD), "genset is not running (or status unknown)"


def _genset_stopped(e, dev, cmd, args):
    v, q = e._val(dev, "running")
    return ((not v) and q == Quality.GOOD), "genset is already running (or status unknown)"


def _no_active_alarm(e, dev, cmd, args):
    v, q = e._val(dev, "alarm_active")
    return (not v and q == Quality.GOOD), "active alarm present"


def _mode_permits_write(e, dev, cmd, args):
    v, q = e._val(dev, "mode")
    if dev.point("mode") is None:
        return True, ""
    if q != Quality.GOOD:
        return False, "controller mode unknown"
    if str(v).upper() == "OFF":
        return False, "controller is in OFF — switch locally first"
    return True, ""


def _within_limits(e, dev, cmd, args):
    lim = dev.limits
    if cmd.id == "set_power_target" and lim.max_load_kw is not None:
        t = float(args.get("target_kw", 0))
        if t > lim.max_load_kw:
            return False, f"target {t} > commissioned max {lim.max_load_kw}"
        if lim.min_load_kw is not None and 0 < t < lim.min_load_kw:
            return False, f"target {t} < minimum stable load {lim.min_load_kw}"
    if cmd.id == "set_power_limit" and lim.curtail_min_pct is not None:
        if float(args.get("limit_pct", 100)) < lim.curtail_min_pct:
            return False, f"limit below commissioned curtail floor {lim.curtail_min_pct}%"
    if cmd.id == "set_active_power_target":
        t = float(args.get("target_kw", 0))
        if lim.discharge_kw_max is not None and t > lim.discharge_kw_max:
            return False, f"discharge {t} > {lim.discharge_kw_max}"
        if lim.charge_kw_max is not None and -t > lim.charge_kw_max:
            return False, f"charge {-t} > {lim.charge_kw_max}"
    return True, ""


def _min_stop_time(e, dev, cmd, args):
    # We don't have a last_stop point on every controller; use event history if present.
    last = e.store.events(e.clock() - timedelta(days=2), kind="command_verified")
    for ev in last:
        if ev["device_id"] == dev.id and ev["code"] == "stop":
            elapsed = (e.clock() - datetime.fromisoformat(ev["ts"])).total_seconds()
            if dev.limits.min_stop_s and elapsed < dev.limits.min_stop_s:
                return False, f"stopped {int(elapsed)}s ago; min off time {int(dev.limits.min_stop_s)}s"
            break
    return True, ""


def _min_run_time(e, dev, cmd, args):
    last = e.store.events(e.clock() - timedelta(days=2), kind="command_verified")
    for ev in last:
        if ev["device_id"] == dev.id and ev["code"] == "start":
            elapsed = (e.clock() - datetime.fromisoformat(ev["ts"])).total_seconds()
            if dev.limits.min_run_s and elapsed < dev.limits.min_run_s:
                return False, f"started {int(elapsed)}s ago; min run time {int(dev.limits.min_run_s)}s"
            break
    return True, ""


def _site_reserve_without(e, dev, cmd, args):
    """Stopping this genset must leave enough firm capacity (other gensets + BESS) to cover load."""
    load = 0.0
    for m in e.reg.by_kind("meter"):
        v, q = e._val(m, "load_kw")
        if q == Quality.GOOD and v is not None:
            load = float(v)
    if load == 0.0:
        # fall back: sum of what is currently supplied
        for d in e.reg.devices.values():
            if d.kind in ("genset", "pv", "bess"):
                v, q = e._val(d, "power_kw")
                if q == Quality.GOOD and v:
                    load += float(v)
    firm = 0.0
    for g in e.reg.by_kind("genset"):
        if g.id == dev.id:
            continue
        v, q = e._val(g, "running")
        if q == Quality.GOOD and v:
            firm += g.limits.max_load_kw or 0.0
    for b in e.reg.by_kind("bess"):
        soc, q = e._val(b, "soc_pct")
        if q == Quality.GOOD and soc is not None and soc > (b.limits.soc_min_pct or 0) + 5:
            firm += b.limits.discharge_kw_max or 0.0
    for p in e.reg.by_kind("pv"):
        v, q = e._val(p, "power_kw")
        if q == Quality.GOOD and v:
            firm += 0.5 * float(v)   # count only half of PV as firm (clouds)
    if firm < load:
        return False, f"firm capacity without {dev.id} is {firm:.0f} kW < load {load:.0f} kW"
    return True, ""


def _bess_soc_window(e, dev, cmd, args):
    soc, q = e._val(dev, "soc_pct")
    if q != Quality.GOOD or soc is None:
        return False, "SOC unknown"
    t = float(args.get("target_kw", 0))
    lim = dev.limits
    if t > 0 and soc <= (lim.soc_min_pct or 0):
        return False, f"SOC {soc}% at/below min {lim.soc_min_pct}% — cannot discharge"
    if t < 0 and soc >= (lim.soc_max_pct or 100):
        return False, f"SOC {soc}% at/above max {lim.soc_max_pct}% — cannot charge"
    return True, ""


def _bess_bms(e, dev, cmd, args):
    t = float(args.get("target_kw", 0))
    if t > 0:
        mx, q = e._val(dev, "max_discharge_kw")
        if q == Quality.GOOD and mx is not None and t > float(mx):
            return False, f"BMS discharge limit {mx} kW < {t}"
    elif t < 0:
        mx, q = e._val(dev, "max_charge_kw")
        if q == Quality.GOOD and mx is not None and -t > float(mx):
            return False, f"BMS charge limit {mx} kW < {-t}"
    return True, ""


PRECONDITIONS: dict[str, Callable] = {
    "device_healthy": _device_healthy,
    "telemetry_fresh": _telemetry_fresh,
    "genset_running": _genset_running,
    "genset_stopped": _genset_stopped,
    "no_active_alarm": _no_active_alarm,
    "mode_permits_write": _mode_permits_write,
    "within_commissioned_limits": _within_limits,
    "min_stop_time_elapsed": _min_stop_time,
    "min_run_time_elapsed": _min_run_time,
    "site_has_reserve_without_genset": _site_reserve_without,
    "bess_soc_window": _bess_soc_window,
    "bess_within_bms_limits": _bess_bms,
}
