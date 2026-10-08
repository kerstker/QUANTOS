from services.research.profitability_timing import TimingGate


def test_adverse_short_signal_waits_only_until_fixed_deadline():
    gate=TimingGate(wait_ms=3600000)
    assert gate.decide(at=1000,current=0,target=.25,short_signal=-.001)['target'] is None
    row=gate.decide(at=3601000,current=0,target=.25,short_signal=-.001)
    assert row['target']==.25 and row['reason']=='WAIT_TIMEOUT'


def test_risk_reduction_and_reversal_close_are_never_delayed():
    gate=TimingGate(wait_ms=3600000)
    assert gate.decide(at=1000,current=.25,target=0,short_signal=.01)['target']==0
    assert gate.decide(at=2000,current=.25,target=-.25,short_signal=.01)['target']==0


def test_new_opposite_target_cancels_pending_and_restarts_bounded_wait():
    gate=TimingGate(wait_ms=3600000)
    gate.decide(at=1000,current=0,target=.25,short_signal=-.001)
    row=gate.decide(at=2000,current=0,target=-.25,short_signal=.001)
    assert row['target'] is None and row['cancelled_target']==.25
    assert row['deadline_ms']==3602000
