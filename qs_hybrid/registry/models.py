"""
Device capability registry — the source of truth for what exists and what is allowed.

Nothing in the platform may touch a device except through a Device + CommandDefinition
that is declared here. The AI, the HMI and the optimizer all go through the same
registry and the same gateway. There is no "raw register write" anywhere.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class RiskClass(str, Enum):
    """Site-approved command risk classes (policy model, see docs/SAFETY.md)."""

    C0_READ = "C0"            # read only
    C1_LOW = "C1"             # low-impact setting
    C2_SETPOINT = "C2"        # operational setpoint (power target, SOC reserve, PV limit)
    C3_TRANSITION = "C3"      # start/stop/mode change — major state transition
    C4_PROTECTED = "C4"       # protections, interlocks, e-stop, breakers, sync — NEVER via platform


# Risk classes that may never be executed by this platform, in any mode.
FORBIDDEN_CLASSES: frozenset[RiskClass] = frozenset({RiskClass.C4_PROTECTED})


class OperatingMode(str, Enum):
    MANUAL = "manual"                 # operator selects device + command; gateway still validates
    SUPERVISED = "supervised"         # AI proposes, human approves, gateway executes
    AUTONOMOUS = "autonomous"         # approved automations run inside their envelope
    RESTRICTED = "restricted"         # fallback: no new automated writes; local control only


class Quality(str, Enum):
    GOOD = "good"
    STALE = "stale"
    UNCERTAIN = "uncertain"
    BAD = "bad"


class PointDefinition(BaseModel):
    """One readable (or read/write) signal on a device, under its standard name."""

    id: str                                    # e.g. "power_kw", "soc_pct", "running"
    type: Literal["number", "boolean", "enum", "string"] = "number"
    unit: str = ""
    access: Literal["read", "read_write"] = "read"
    max_age_ms: int = 5000                     # older than this -> STALE
    quality_required: Quality = Quality.GOOD
    # adapter-specific raw mapping (modbus address etc). Opaque to the rest of the system.
    raw: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class ArgSpec(BaseModel):
    type: Literal["number", "boolean", "enum", "string"] = "number"
    unit: str = ""
    min: float | None = None
    max: float | None = None
    choices: list[str] | None = None


class Verification(BaseModel):
    point: str                                 # which point confirms the command
    strategy: Literal["readback_equals", "readback_within", "state_equals"] = "readback_equals"
    tolerance: float = 0.0                     # for readback_within
    expected: Any | None = None                # for state_equals (e.g. running == True)
    timeout_s: float = 30.0


class CommandDefinition(BaseModel):
    """An intentionally supported operational function — never an arbitrary register."""

    id: str                                    # e.g. "set_power_target", "start", "stop"
    category: str = "operational"
    risk_class: RiskClass
    args: dict[str, ArgSpec] = Field(default_factory=dict)
    preconditions: list[str] = Field(default_factory=list)   # names of checks in policy.py
    verification: Verification | None = None
    allowed_modes: list[OperatingMode] = Field(
        default_factory=lambda: [OperatingMode.MANUAL, OperatingMode.SUPERVISED]
    )
    requires_approval: bool = True
    min_interval_s: float = 60.0               # rate limit per device+command
    raw: dict[str, Any] = Field(default_factory=dict)   # adapter mapping (opaque)
    description: str = ""

    @field_validator("risk_class")
    @classmethod
    def _no_forbidden(cls, v: RiskClass) -> RiskClass:
        # A profile may document C4 functions for completeness, but they can't be loaded
        # as executable commands. Fail loudly at load time rather than at runtime.
        if v in FORBIDDEN_CLASSES:
            raise ValueError(
                f"risk class {v.value} is forbidden for platform commands "
                "(protections / e-stop / breakers / sync stay in the controller & PLC)"
            )
        return v


class DeviceLimits(BaseModel):
    """Commissioned physical envelope. Used by the EMS and the policy engine."""

    rated_kw: float | None = None
    min_load_kw: float | None = None           # gensets: minimum stable load
    max_load_kw: float | None = None
    ramp_kw_per_min: float | None = None
    min_run_s: float | None = None             # gensets: minimum run time once started
    min_stop_s: float | None = None            # gensets: minimum off time once stopped
    start_delay_s: float | None = None
    soc_min_pct: float | None = None           # BESS
    soc_max_pct: float | None = None
    soc_reserve_pct: float | None = None       # BESS: keep this much for the evening
    charge_kw_max: float | None = None
    discharge_kw_max: float | None = None
    capacity_kwh: float | None = None
    curtail_min_pct: float | None = None       # PV: lowest allowed power-limit setting
    fuel_l_per_kwh_curve: list[tuple[float, float]] | None = None  # (load_frac, L/kWh)


class Device(BaseModel):
    id: str                                    # "genset-01", "pv-01", "bess-01", "plc-01"
    kind: Literal["genset", "pv", "bess", "plc", "meter", "forecast"]
    vendor: str = ""
    model: str = ""
    firmware: str = ""
    adapter: str                               # "simulator" | "modbus_tcp" | "comap_wsv" | ...
    connection: dict[str, Any] = Field(default_factory=dict)  # host/port/unit, or secret ref
    points: list[PointDefinition] = Field(default_factory=list)
    commands: list[CommandDefinition] = Field(default_factory=list)
    limits: DeviceLimits = Field(default_factory=DeviceLimits)
    profile_revision: int = 1
    profile_status: Literal["draft", "commissioned"] = "draft"
    source_document: str = ""

    def point(self, pid: str) -> PointDefinition | None:
        return next((p for p in self.points if p.id == pid), None)

    def command(self, cid: str) -> CommandDefinition | None:
        return next((c for c in self.commands if c.id == cid), None)


class SitePolicy(BaseModel):
    """Site-wide operating policy. Lives in config/site.yaml, versioned."""

    site_id: str
    name: str = ""
    timezone: str = "Asia/Baghdad"
    mode: OperatingMode = OperatingMode.SUPERVISED
    policy_revision: int = 1
    # Autonomy envelope: only these device/command pairs may run without a human click.
    autonomous_allowlist: list[dict[str, str]] = Field(default_factory=list)   # [{device, command}]
    max_automated_commands_per_hour: int = 12
    telemetry_quality_min: Quality = Quality.GOOD
    approval_ttl_s: float = 300.0
    # Who may do what. Minimal RBAC; replace with your IdP later.
    roles: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "viewer": ["C0"],
            "operator": ["C0", "C1", "C2", "C3"],
            "engineer": ["C0", "C1", "C2", "C3"],
            "automation": ["C0", "C2"],     # the optimizer service identity: never C3 on its own
            "ai": ["C0", "C2", "C3"],       # the AI may PROPOSE C2/C3; the gateway forces approval
        }
    )
    # Economics (used by the EMS)
    fuel_price_per_l: float = 1.0
    bess_cycle_cost_per_kwh: float = 0.05
    genset_start_cost: float = 2.0            # currency, wear + fuel of a start
