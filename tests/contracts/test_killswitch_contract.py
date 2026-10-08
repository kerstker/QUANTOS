"""Phase 8 — KillSwitchState contract: frozen, defaults inactive, is_active reflects tripped."""
import pytest

from quantos.contracts.killswitch import KillSwitchScope, KillSwitchState


def test_defaults_inactive_global():
    s = KillSwitchState()
    assert s.is_active is False and s.scope == KillSwitchScope.GLOBAL and s.trip_count == 0


def test_tripped_is_active_and_frozen():
    s = KillSwitchState(tripped=True, incident_id="trip-1", trip_count=1)
    assert s.is_active is True
    with pytest.raises(Exception):
        s.tripped = False                       # frozen
