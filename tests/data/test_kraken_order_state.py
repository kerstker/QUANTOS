from decimal import Decimal as D

import pytest

from services.data.kraken_order_state import state, assert_progress
from quantos.contracts.order import OrderState


@pytest.mark.parametrize('status,filled,expected',[
    ('pending','0',OrderState.SUBMITTED),('open','0',OrderState.SUBMITTED),
    ('open','0.4',OrderState.PARTIALLY_FILLED),('closed','1',OrderState.FILLED),
    ('canceled','0.4',OrderState.CANCELLED),('expired','0.4',OrderState.EXPIRED)])
def test_quantity_aware_state_mapping(status,filled,expected):
    assert state(status,D('1'),D(filled))==expected


@pytest.mark.parametrize('status,filled',[('closed','0.4'),('open','1'),('pending','0.1'),('unknown','0'),('open','1.1')])
def test_unknown_or_inconsistent_response_is_not_a_success(status,filled):
    with pytest.raises(ValueError):state(status,D('1'),D(filled))


def test_replay_partial_cancel_and_terminal_rewrite_rules():
    assert_progress('open','canceled',D('1'),D('0.2'),D('0.4'))
    assert_progress('canceled','canceled',D('1'),D('0.4'),D('0.4'))
    for prior,new,before,after in [('open','open','0.4','0.2'),('canceled','closed','0.4','1'),
                                    ('canceled','canceled','0.4','0.5')]:
        with pytest.raises(ValueError):assert_progress(prior,new,D('1'),D(before),D(after))
