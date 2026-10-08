"""Trial identity may name an execution strategy, never approve it."""
import json
import pytest
from tests.research.test_gold_ai_research import setup


def test_strategy_definition_retains_actual_rules_and_exit_plan(tmp_path, monkeypatch):
    from tools.gold_execution_identity import retain_strategy_definition
    from tools.gold_cost_inputs import load_inputs
    tool, calls, dest = setup(tmp_path, monkeypatch)
    tool.run_research({'attempt': 1})
    original = {name: (dest/'trial'/name).read_bytes()
                for name in ('input.json', 'report.json', 'manifest.json', 'trials.db')}
    definition = retain_strategy_definition(dest/'trial')
    arguments = load_inputs(original['input.json'])
    assert definition['entry_rule'] == arguments['document']['rule']
    assert definition['historical_zone'] == arguments['document']['zone']
    assert definition['exit_and_size_plan'] == {
        k: str(v) if k in ('quantity', 'stop', 'target') else v
        for k, v in arguments['plan'].items()}
    assert definition['submission_enabled'] is False
    assert definition['economic_eligible'] is False
    assert definition['prospective_registration_required'] is True
    saved = (dest/'trial/strategy-definition.json').read_bytes()
    assert retain_strategy_definition(dest/'trial') == definition
    assert (dest/'trial/strategy-definition.json').read_bytes() == saved
    assert len(calls) == 1
    assert all((dest/'trial'/name).read_bytes() == body for name, body in original.items())


def test_changed_strategy_definition_is_not_silently_replaced(tmp_path, monkeypatch):
    from tools.gold_execution_identity import retain_strategy_definition
    tool, _, dest = setup(tmp_path, monkeypatch)
    tool.run_research({'attempt': 1})
    definition = retain_strategy_definition(dest/'trial')
    definition['entry_rule']['sell_threshold'] = '999'
    path = dest/'trial/strategy-definition.json'
    path.write_text(json.dumps(definition))
    changed = path.read_bytes()
    with pytest.raises(ValueError, match='GOLD_STRATEGY_DEFINITION_CHANGED'):
        retain_strategy_definition(dest/'trial')
    assert path.read_bytes() == changed


def test_retained_trial_names_stable_strategy_without_changing_approval(tmp_path,monkeypatch):
    from tools.gold_execution_identity import from_archive
    from services.research.gold_execution_identity import validate_identity
    tool,_,dest=setup(tmp_path,monkeypatch)
    result=tool.run_research({'attempt':1})
    identity=from_archive(dest/'trial')
    assert identity['trial_id']==result['trial_id'] and identity['candidate_id']==result['candidate_id']
    assert identity['strategy_id'].startswith('gold-research-')
    assert identity['economic_eligible'] is False and identity['submission_enabled'] is False
    assert from_archive(dest/'trial')==identity
    validate_identity(identity,as_of_ms=identity['available_at_ms'],strategy_id=identity['strategy_id'])
    for at,sid in ((identity['available_at_ms']-1,identity['strategy_id']),
                   (identity['available_at_ms'],'unrelated-strategy')):
        with pytest.raises(ValueError):validate_identity(identity,as_of_ms=at,strategy_id=sid)
    altered=dict(identity,candidate_id=(identity['candidate_id']+1)%6)
    with pytest.raises(ValueError):validate_identity(altered,as_of_ms=identity['available_at_ms'],strategy_id=identity['strategy_id'])
    origin_path=dest/'trial/research-origin.json'
    origin=json.loads(origin_path.read_bytes());origin['proposal']['candidate_id']=5
    origin_path.write_text(json.dumps(origin))
    with pytest.raises(ValueError):from_archive(dest/'trial')
