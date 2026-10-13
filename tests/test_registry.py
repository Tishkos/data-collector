import pytest

from qs_hybrid.registry.models import CommandDefinition, RiskClass


def test_c4_protective_commands_cannot_be_loaded():
    with pytest.raises(ValueError):
        CommandDefinition(id="open_gcb", risk_class=RiskClass.C4_PROTECTED)
    with pytest.raises(ValueError):
        CommandDefinition(id="emergency_stop", risk_class="C4")


def test_registry_loads_all_devices(site):
    ids = set(site.registry.devices)
    assert {"genset-01", "genset-02", "pv-01", "bess-01", "meter-01"} <= ids
    g = site.registry.get("genset-01")
    assert {c.id for c in g.commands} == {"set_power_target", "start", "stop", "fault_reset", "set_mode"}
    assert all(c.risk_class != RiskClass.C4_PROTECTED for c in g.commands)


def test_capability_inventory_has_no_raw_mapping(site):
    inv = site.registry.capability_inventory()
    for dev in inv:
        for c in dev["commands"]:
            assert "raw" not in c            # the AI never sees register addresses
        for p in dev["points"]:
            assert "raw" not in p
