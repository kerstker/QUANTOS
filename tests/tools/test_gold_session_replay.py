from decimal import Decimal as D

import pytest

from tests.tools.test_gold_session_prefix import inputs
from tools.gold_session_prefix import snapshot


def test_shared_prefix_replays_original_portfolio_account(tmp_path,monkeypatch):
    from tools.gold_session_replay import replay
    quotes,bars,capture,_=inputs(tmp_path,monkeypatch)
    snapshot(quotes,bars,tmp_path/'prefix')
    result=replay(tmp_path/'prefix',exchange_after=capture['exchange_after'],
        release_identity={'fixture':True},quantities={k:D('.1') for k in ('channel','trend','reversion')},
        initial_collateral=D('1000'),fee_rate=D('.001'),max_notional=D('100'))
    assert result['simulation']['reconciliation']['residual']=='0'
    assert result['simulation']['orders']
    assert result['economic_eligible'] is False and result['submission_enabled'] is False


def test_changed_prefix_cannot_be_replayed(tmp_path,monkeypatch):
    from tools.gold_session_replay import replay
    quotes,bars,capture,_=inputs(tmp_path,monkeypatch)
    snapshot(quotes,bars,tmp_path/'prefix')
    p=tmp_path/'prefix/inputs.json'
    p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises(ValueError,match='HASH'):
        replay(tmp_path/'prefix',exchange_after=capture['exchange_after'],
            release_identity={'fixture':True},quantities={k:D('.1') for k in ('channel','trend','reversion')},
            initial_collateral=D('1000'),fee_rate=D('.001'),max_notional=D('100'))


def test_new_prefix_schema_does_not_relax_original_sample_or_gap_limits(tmp_path,monkeypatch):
    from copy import deepcopy
    from services.execution.perpetual_public_replay import prepare_public_replay
    from tests.execution.test_perpetual_public_replay import sealed
    _,_,capture,_=inputs(tmp_path,monkeypatch)
    capture['schema_version']='GOLD_PUBLIC_SESSION_PREFIX_V1'
    assert len(prepare_public_replay(sealed(capture))['frames'])==64
    legacy=deepcopy(capture)
    legacy['schema_version']='GOLD_PUBLIC_REPLAY_CAPTURE_V1'
    with pytest.raises(ValueError,match='sample budget'):
        prepare_public_replay(sealed(legacy))
    capture['max_gap_ms']=30001
    with pytest.raises(ValueError,match='gap budget'):
        prepare_public_replay(sealed(capture))
