from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from tests.research.test_ai_factory_provider import LOCAL, Transport
from tests.research.test_gold_input_collection import setup


def design():
    return {'window_ms': 1000, 'max_age_ms': 100, 'window_count': 2,
            'zone_lower_bps': -50, 'zone_upper_bps': 0,
            'stop_bps': -100, 'target_bps': 200}


def test_ai_price_recipe_compiles_into_original_public_cost_ledger(tmp_path, monkeypatch):
    from tools.gold_capture_design import propose_design, verify_design
    from services.research.gold_price_design import compile_prices
    from tools.gold_cost_inputs import load_inputs
    from tools.gold_cost_replay import replay_costs
    tool, base, calls = setup(tmp_path, monkeypatch)
    # Existing original capture/assembly supplies the complete public input.
    tool.run_collect({'attempt': 2, 'template_id': 1, 'seconds': 30})
    args = load_inputs((base/'gold-inputs/2.json').read_bytes())
    original = deepcopy(args)
    anchor = args['frames'][0]
    from types import SimpleNamespace
    from tools import gold_capture_design
    monkeypatch.setattr(gold_capture_design, 'time', SimpleNamespace(time_ns=lambda: (anchor.timestamp-1)*1000000))
    receipt = propose_design(seconds=30, base_rule={'window_ms': 1000, 'max_age_ms': 100},
        price_design=True, environ=LOCAL, transport=Transport(design()))
    recipe = verify_design(receipt, seconds=30)
    document, plan = compile_prices(recipe, document=args['document'], plan=args['plan'],
        anchor=anchor, designed_at_ms=receipt['received_at_ms'])
    quote = anchor.quotes[0]
    mid = (quote.bid_price+quote.ask_price)/2
    assert Decimal(document['zone']['lower']) == mid*Decimal('.995')
    assert Decimal(document['zone']['upper']) == mid
    assert plan['stop'] == mid*Decimal('.99')
    assert plan['target'] == mid*Decimal('1.02')
    assert plan['quantity'] == args['plan']['quantity']
    assert args == original
    from tools.gold_public_cost_inputs import assemble_inputs, verify_assembly
    compiled = assemble_inputs(capture=args['market_identity']['capture'], document=document,
        plan=plan, **{k:args[k] for k in ('release_identity','initial_collateral','costs','limits')})
    verify_assembly(compiled)
    result = replay_costs(**compiled)
    assert result['simulation']['reconciliation']['residual'] == '0'
    assert result['authoritative'] is False
    assert result['outcome'] in ('NO_SIGNAL', 'OPEN_AT_END', 'FLAT_TERMINAL')


@pytest.mark.parametrize('change', [{'zone_lower_bps': True}, {'stop_bps': -10},
    {'target_bps': -1}, {'zone_upper_bps': 3000}, {'quantity': 10}])
def test_invalid_price_recipe_never_becomes_a_design(change):
    from tools.gold_capture_design import propose_design
    with pytest.raises(ValueError):
        propose_design(seconds=30, base_rule={'window_ms': 1000, 'max_age_ms': 100},
            price_design=True, environ=LOCAL, transport=Transport({**design(), **change}))


def test_anchor_is_fixed_before_the_first_signal_window(tmp_path, monkeypatch):
    from services.research.gold_price_design import compile_prices
    from tools.gold_cost_inputs import load_inputs
    tool, base, _ = setup(tmp_path, monkeypatch)
    tool.run_collect({'attempt': 2, 'template_id': 1, 'seconds': 30})
    args = load_inputs((base/'gold-inputs/2.json').read_bytes())
    anchor = args['frames'][0]
    for changed, designed in ((anchor, anchor.timestamp+1),
        (replace(anchor, timestamp=args['document']['start_ms']+1, market_events=(), capacity_snapshots=()), anchor.timestamp-1),
        (replace(anchor, quotes=()), anchor.timestamp-1)):
        with pytest.raises(ValueError):
            compile_prices(design(), document=args['document'], plan=args['plan'],
                anchor=changed, designed_at_ms=designed)


def test_price_model_receives_original_technical_diagnostics():
    import json
    from tools.gold_capture_design import propose_design, verify_design
    from tests.research.test_gold_research_memory import memory
    transport=Transport(design())
    history=memory()
    receipt=propose_design(seconds=30,base_rule={'window_ms':1000,'max_age_ms':100},
        price_design=True,research_memory=history,environ=LOCAL,transport=transport)
    prompt=json.loads(transport.request['messages'][1]['content'])
    assert prompt['prior_technical_experiments']==history
    assert receipt['schema']=='GOLD_AI_CAPTURE_DESIGN_V4'
    assert receipt['research_memory']==history
    assert verify_design(receipt,seconds=30)==design()
    with pytest.raises(ValueError,match='STRATEGY_MEMORY_CONTEXT'):
        propose_design(seconds=30,base_rule={'window_ms':1000,'max_age_ms':100},
            research_memory=history,environ=LOCAL,transport=Transport({}))
