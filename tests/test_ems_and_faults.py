from qs_hybrid.control.autopilot import Autopilot
from qs_hybrid.control.ems import Goal
from qs_hybrid.registry.models import OperatingMode


def test_plan_balances_power_and_respects_min_load(site):
    site.advance(30 * 60)   # 06:30, little sun
    p = site.ems.plan(Goal(objective="share_target", pv_share_pct=60, bess_reserve_pct=30))
    assert p.feasible
    total = p.pv_dispatch_kw + p.bess_dispatch_kw + sum(p.genset_dispatch.values())
    assert abs(total - p.load_kw) < 1.0
    for gid, kw in p.genset_dispatch.items():
        if kw > 0:
            assert kw >= site.registry.get(gid).limits.min_load_kw
    assert any("only" in w and "sun" in w for w in p.warnings)      # honest about the 60% target


def test_plan_noon_no_genset_and_never_curtails_to_hit_share(site):
    site.advance(6 * 3600)  # 12:00
    p = site.ems.plan(Goal(objective="share_target", pv_share_pct=20))
    assert all(kw == 0 for kw in p.genset_dispatch.values())
    assert p.pv_dispatch_kw > 0.9 * p.pv_available_kw             # didn't throttle PV to 20%
    assert p.bess_dispatch_kw < 0                                   # surplus charges the battery


def test_plan_reserve_is_respected(site):
    site.advance(30 * 60)
    site.plant.bess["bess-01"].soc_pct = 25.0
    site.poll()
    p = site.ems.plan(Goal(bess_reserve_pct=30))
    assert p.bess_dispatch_kw <= 0          # at/below reserve -> no discharge


def test_unit_commitment_picks_two_gensets_for_peak(site):
    site.advance(30 * 60)
    p = site.ems.plan(Goal(), load_kw=800)
    on = [g for g, kw in p.genset_dispatch.items() if kw > 0]
    assert len(on) == 2
    assert p.alternatives and p.alternatives[0]["cost_per_h"] <= p.alternatives[-1]["cost_per_h"]


def test_fault_detector_catches_injected_scenarios(site):
    site.advance(3 * 3600)
    site.plant.inject("pv_soiling")
    site.plant.inject("genset_alarm", "genset-02")
    site.plant.inject("comms_loss", "bess-01")
    site.advance(40 * 60)
    active = site.faults.active()
    assert "GENSET_ALARM:genset-02" in active
    assert "COMMS_LOST:bess-01" in active
    assert "PV_UNDERPERFORMANCE:pv-01" in active


def test_autopilot_full_day_no_unserved_no_trip(site, operator):
    site.registry.policy.mode = OperatingMode.AUTONOMOUS
    ap = Autopilot(site)
    ap.set_goal(Goal(objective="share_target", pv_share_pct=60, bess_reserve_pct=30))
    ap.enable()
    worst = 0.0
    for tick in range(96):
        ap.tick()
        for c in site.gateway.pending():
            site.gateway.approve(c.id, operator)
        site.advance(15 * 60, step=60)
        if tick > 4:   # allow the first genset start to be approved
            worst = max(worst, site.plant.unserved_kw)
    assert not ap.state.tripped
    assert worst < 1.0
    ok, _ = site.audit.verify()
    assert ok
