import json

import pytest

from tests.research.test_ai_factory_provider import LOCAL, Transport


def test_model_proposes_window_design_with_bounded_evidence():
    from tools.gold_capture_design import propose_design, verify_design
    transport=Transport({'window_ms':5000,'max_age_ms':1000,'window_count':3})
    receipt=propose_design(seconds=30,base_rule={'window_ms':2000,'max_age_ms':1500},
                           environ=LOCAL,transport=transport)
    assert verify_design(receipt,seconds=30)=={'window_ms':5000,'max_age_ms':1000,'window_count':3}
    prompt=json.loads(transport.request['messages'][1]['content'])
    assert prompt['purpose']=='DESIGN_NEXT_PUBLIC_CAPTURE_BEFORE_OBSERVING_IT'
    assert 'prices' not in prompt
    receipt['design']['window_ms']=1000
    with pytest.raises(ValueError):verify_design(receipt,seconds=30)


@pytest.mark.parametrize('design',[
    {'window_ms':True,'max_age_ms':1000,'window_count':3},
    {'window_ms':15000,'max_age_ms':5000,'window_count':6},
    {'window_ms':5000,'max_age_ms':1000,'window_count':3,'code':'execute'},
])
def test_invalid_generated_design_is_not_accepted(design):
    from tools.gold_capture_design import propose_design
    with pytest.raises(ValueError):
        propose_design(seconds=30,base_rule={'window_ms':2000,'max_age_ms':1500},
                       environ=LOCAL,transport=Transport(design))


@pytest.mark.parametrize('price_design', [False, True])
def test_generated_plan_precedes_capture_and_is_not_regenerated_on_replay(tmp_path,monkeypatch,price_design):
    from quantos.local_artifacts import with_result_hash
    from tools.gold_capture_design import propose_design
    from tests.research.test_gold_input_collection import setup
    tool,base,captures=setup(tmp_path,monkeypatch)
    model_calls=[]
    def design(**kwargs):
        assert captures==[]
        model_calls.append(1)
        output={'window_ms':1000,'max_age_ms':100,'window_count':2}
        if price_design:output.update(zone_lower_bps=-50,zone_upper_bps=0,stop_bps=-100,target_bps=200)
        receipt=propose_design(**kwargs,environ=LOCAL,transport=Transport(output))
        receipt['received_at_ms']=1
        return with_result_hash({k:v for k,v in receipt.items() if k!='result_hash'})
    monkeypatch.setattr(tool,'propose_design',design)
    params={'attempt':2,'template_id':1,'seconds':30,'decision_policy':'AI_DESIGNED_ZONE_V1' if price_design else 'AI_DESIGNED_WATERMARK_V1'}
    first=tool.run_collect(params)
    assert tool.run_collect(params)==first
    assert captures==[30] and model_calls==[1]
    if price_design:
        from tools.gold_cost_inputs import load_inputs
        args=load_inputs((base/'gold-inputs/2.json').read_bytes())
        assert args['document']['zone']['lower']!=load_inputs((base/'gold-inputs/1.json').read_bytes())['document']['zone']['lower']
        assert args['plan']['stop']<args['plan']['target']
    path=base/'gold-collections/2/design.json'
    receipt=json.loads(path.read_bytes());receipt['design']['window_ms']=2000
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError,match='INVALID_GOLD_CAPTURE_DESIGN_RECEIPT'):
        tool.run_collect(params)
    assert captures==[30] and model_calls==[1]
