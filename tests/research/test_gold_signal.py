from dataclasses import replace
from decimal import Decimal as D

import pytest

from services.research.gold_signal import (
    Rule, Zone, Window, GoldSignal, SignalError, ExitPlan, exit_reason,
)


def rule():
    return Rule(window_ms=1000, max_age_ms=100, sell_threshold=D('10'),
                confirm_windows=2)


def zone():
    return Zone(lower=D('99'), upper=D('100'), registered_at_ms=500, expires_at_ms=10000)


def window(start=1000, **kwargs):
    values=dict(symbol='XAUUSDT',start_ms=start,end_ms=start+1000,received_at_ms=start+1010,
        low=D('99.5'),high=D('101'),close=D('100.5'),buy_quantity=D('3'),
        sell_quantity=D('12'),source_hash='a'*64,complete=True)
    values.update(kwargs)
    return Window(**values)


def test_signal_waits_for_later_confirmation_and_never_invents_fill():
    s=GoldSignal(rule(),zone())
    first=s.observe(window(),decision_ms=2010)
    assert first['state']=='CONFIRMING' and first['intent'] is None
    second=s.observe(window(2000,high=D('102'),close=D('101.5')),decision_ms=3010)
    assert second['intent']=='LONG_RESEARCH_SIGNAL'
    assert second['earliest_execution_after_ms']==3010
    assert second['entry_price'] is None and second['authoritative'] is False
    assert s.observe(window(3000),decision_ms=4010)['intent'] is None


def test_future_and_stale_data_poison_epoch_instead_of_fabricating_signal():
    for frame,now in [(window(),2009),(window(),2200),
                      (window(complete=False),2010)]:
        s=GoldSignal(rule(),zone())
        assert s.observe(frame,decision_ms=now)['state']=='DATA_UNKNOWN'
        assert s.observe(window(2000),decision_ms=3010)['state']=='DATA_UNKNOWN'


def test_gap_is_not_silently_skipped():
    s=GoldSignal(rule(),zone()); s.observe(window(),decision_ms=2010)
    assert s.observe(window(3000),decision_ms=4010)['state']=='DATA_UNKNOWN'


def test_zone_must_exist_before_first_window_and_expires_before_signal():
    s=GoldSignal(rule(),replace(zone(),registered_at_ms=1001))
    assert s.observe(window(),decision_ms=2010)['state']=='DATA_UNKNOWN'
    s=GoldSignal(rule(),replace(zone(),expires_at_ms=3000))
    s.observe(window(),decision_ms=2010)
    assert s.observe(window(2000,high=D('102'),close=D('101.5')),decision_ms=3010)['state']=='EXPIRED'


def test_boundary_breach_cancels_and_confirmation_has_fixed_deadline():
    s=GoldSignal(rule(),zone())
    assert s.observe(window(low=D('98')),decision_ms=2010)['state']=='EXPIRED'
    s=GoldSignal(rule(),zone()); s.observe(window(),decision_ms=2010)
    s.observe(window(2000),decision_ms=3010)
    assert s.observe(window(3000),decision_ms=4010)['state']=='EXPIRED'


def test_below_threshold_is_not_absorption_and_future_windows_do_not_change_prefix():
    s=GoldSignal(rule(),zone())
    a=s.observe(window(sell_quantity=D('9')),decision_ms=2010)
    assert a['state']=='WATCHING'
    frozen=dict(a)
    s.observe(window(2000),decision_ms=3010)
    assert a==frozen
    assert GoldSignal(rule(),zone()).observe(window(sell_quantity=D('9')),decision_ms=2010)==a


@pytest.mark.parametrize('changes',[{'max_age_ms':True},{'sell_threshold':D('NaN')},
    {'confirm_windows':0},{'window_ms':0},{'sell_threshold':10.0}])
def test_bad_rule_rejected(changes):
    with pytest.raises(SignalError): GoldSignal(replace(rule(),**changes),zone())


def test_bad_frame_rejected_and_blocks_epoch():
    s=GoldSignal(rule(),zone())
    assert s.observe(window(low=D('102')),decision_ms=2010)['state']=='DATA_UNKNOWN'


def test_other_gold_contract_cannot_be_used_as_local_confirmation():
    s=GoldSignal(rule(),zone())
    assert s.observe(window(symbol='XAUTUSDT'),decision_ms=2010)['state']=='DATA_UNKNOWN'


def test_explicit_exit_precedence_and_no_signal_from_unknown_prices():
    plan=ExitPlan(filled_at_ms=3000,stop=D('99'),target=D('104'),deadline_ms=6000)
    assert exit_reason(plan,bid=D('98'),event_ms=6000,received_ms=6010,
                       decision_ms=6010,max_age_ms=100)=='STOP'
    assert exit_reason(plan,bid=D('NaN'),event_ms=6000,received_ms=6010,
                       decision_ms=6010,max_age_ms=100)=='DATA_UNKNOWN'
    assert exit_reason(plan,bid=D('102'),event_ms=4000,received_ms=4010,
                       decision_ms=4010,max_age_ms=100)=='NO_EXIT_TRIGGER'


def test_exit_rules_use_executable_bid_and_actual_fill_time():
    plan=ExitPlan(filled_at_ms=3000,stop=D('99'),target=D('104'),deadline_ms=6000)
    assert exit_reason(plan,bid=D('98'),event_ms=4000,received_ms=4010,
                       decision_ms=4010,max_age_ms=100)=='STOP'
    assert exit_reason(plan,bid=D('105'),event_ms=4000,received_ms=4010,
                       decision_ms=4010,max_age_ms=100)=='TARGET'
    assert exit_reason(plan,bid=D('102'),event_ms=6000,received_ms=6010,
                       decision_ms=6010,max_age_ms=100)=='TIMEOUT'
    assert exit_reason(plan,bid=D('98'),event_ms=2000,received_ms=2010,
                       decision_ms=4010,max_age_ms=100)=='DATA_UNKNOWN'
