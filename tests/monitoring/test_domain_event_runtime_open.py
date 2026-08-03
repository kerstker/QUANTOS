import pytest

from services.monitoring import domain_event_runtime
from services.monitoring.event_bus import EventBusIntegrityError


class _Bus:
    pass


def test_open_retries_only_concurrent_append_verification(monkeypatch):
    attempts = []
    sleeps = []

    def factory(*args, **kwargs):
        attempts.append((args, kwargs))
        if len(attempts) < 3:
            raise EventBusIntegrityError(
                "event authority changed during complete verification"
            )
        return _Bus()

    monkeypatch.setattr(domain_event_runtime, "DurableEventBus", factory)
    monkeypatch.setattr(domain_event_runtime.time, "sleep", sleeps.append)

    bus = domain_event_runtime.open_domain_event_bus("authority.db")

    assert isinstance(bus, _Bus)
    assert len(attempts) == 3
    assert sleeps == [0.05, 0.1]


def test_open_never_retries_other_integrity_failures(monkeypatch):
    attempts = []

    def factory(*args, **kwargs):
        attempts.append(1)
        raise EventBusIntegrityError("event chain hash mismatch")

    monkeypatch.setattr(domain_event_runtime, "DurableEventBus", factory)

    with pytest.raises(EventBusIntegrityError, match="hash mismatch"):
        domain_event_runtime.open_domain_event_bus("authority.db")

    assert attempts == [1]
