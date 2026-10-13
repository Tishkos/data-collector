import pytest

from qs_hybrid.control.gateway import Status
from qs_hybrid.registry.models import OperatingMode


def test_ai_proposal_waits_for_human_then_verifies(site, ai, operator):
    site.advance(30 * 60)
    c = site.gateway.submit(ai, "genset-01", "start", reason="test")
    assert c.status == Status.AWAITING_APPROVAL
    assert site.store.latest("genset-01")["running"].value is False     # nothing happened yet
    c = site.gateway.approve(c.id, operator)
    assert c.status == Status.VERIFIED
    assert c.readback is True
    assert site.store.latest("genset-01")["running"].value is True


def test_submitter_cannot_approve_own_command(site, operator):
    c = site.gateway.submit(operator, "genset-01", "start", reason="t")
    assert c.status == Status.AWAITING_APPROVAL
    with pytest.raises(ValueError):
        site.gateway.approve(c.id, operator)


def test_viewer_cannot_write(site, viewer):
    c = site.gateway.submit(viewer, "genset-01", "set_power_target", {"target_kw": 200})
    assert c.status == Status.REJECTED and "role" in c.error


def test_unregistered_command_rejected(site, engineer):
    c = site.gateway.submit(engineer, "genset-01", "open_gcb")
    assert c.status == Status.REJECTED
    c = site.gateway.submit(engineer, "genset-01", "write_register", {"address": 100, "value": 1})
    assert c.status == Status.REJECTED


def test_setpoint_clamped_to_limits(site, operator):
    c = site.gateway.submit(operator, "bess-01", "set_active_power_target", {"target_kw": 5000})
    assert c.status == Status.VERIFIED
    assert c.args["target_kw"] == 300.0
    assert any("clamped" in r for r in c.decision.reasons)


def test_min_run_time_and_reserve_block_immediate_stop(site, operator, engineer):
    site.advance(30 * 60)
    c = site.gateway.submit(operator, "genset-01", "start")
    site.gateway.approve(c.id, engineer)
    c = site.gateway.submit(operator, "genset-01", "stop")
    assert c.status == Status.REJECTED
    assert "min_run_time_elapsed" in c.error


def test_rate_limit(site, operator):
    site.advance(30 * 60)
    c = site.gateway.submit(operator, "genset-01", "start")
    site.gateway.approve(c.id, site_eng := __import__("qs_hybrid.control.policy", fromlist=["Caller"]).Caller("e", "engineer"))
    a = site.gateway.submit(operator, "genset-01", "set_power_target", {"target_kw": 200})
    b = site.gateway.submit(operator, "genset-01", "set_power_target", {"target_kw": 210})
    assert a.status == Status.VERIFIED and b.status == Status.REJECTED and "rate limit" in b.error


def test_restricted_mode_blocks_everything(site, operator):
    site.registry.policy.mode = OperatingMode.RESTRICTED
    c = site.gateway.submit(operator, "pv-01", "set_power_limit", {"limit_pct": 90})
    assert c.status == Status.REJECTED and "RESTRICTED" in c.error


def test_automation_needs_autonomous_mode_and_allowlist(site, automation):
    c = site.gateway.submit(automation, "pv-01", "set_power_limit", {"limit_pct": 90})
    assert c.status == Status.REJECTED
    site.registry.policy.mode = OperatingMode.AUTONOMOUS
    c = site.gateway.submit(automation, "pv-01", "set_power_limit", {"limit_pct": 90})
    assert c.status == Status.VERIFIED
    # genset start is deliberately not on the allowlist, and automation may not issue C3 anyway
    c = site.gateway.submit(automation, "genset-01", "start")
    assert c.status == Status.REJECTED


def test_bad_telemetry_blocks_writes(site, operator):
    site.plant.inject("comms_loss", "bess-01")
    site.poll()
    c = site.gateway.submit(operator, "bess-01", "set_active_power_target", {"target_kw": 50})
    assert c.status == Status.REJECTED and "telemetry_fresh" in c.error


def test_approval_expires(site, operator, engineer):
    c = site.gateway.submit(operator, "genset-01", "start")
    site.advance(10 * 60)        # ttl is 300 s
    with pytest.raises(ValueError):
        site.gateway.approve(c.id, engineer)
    assert c.status == Status.EXPIRED


def test_idempotency(site, operator):
    a = site.gateway.submit(operator, "pv-01", "set_power_limit", {"limit_pct": 80}, idempotency_key="k1")
    b = site.gateway.submit(operator, "pv-01", "set_power_limit", {"limit_pct": 80}, idempotency_key="k1")
    assert a.id == b.id


def test_audit_chain_detects_tampering(site, operator, tmp_path):
    site.gateway.submit(operator, "pv-01", "set_power_limit", {"limit_pct": 95})
    ok, n = site.audit.verify()
    assert ok and n >= 3
    with open(site.audit.path) as f:
        lines = f.readlines()
    i = next(k for k, l in enumerate(lines) if "set_power_limit" in l)
    lines[i] = lines[i].replace("set_power_limit", "set_power_limxt")
    with open(site.audit.path, "w") as f:
        f.writelines(lines)
    ok, _ = site.audit.verify()
    assert not ok
