"""Strict pure plan contract keeps producer identities and authority separate."""
import copy
import pytest
from tests.tools.test_registered_research_run import registered_case as registered_case


def test_registered_plan_has_pure_bounded_contract():
    from quantos.contracts import registered_research_run as contract
    assert contract.SCHEMA=='QUANTOS_REGISTERED_RESEARCH_RUN_V1'
    for value in ({}, {'schema':contract.SCHEMA,'economic_authority':True}, None):
        with pytest.raises(ValueError): contract.validate_plan(value)


@pytest.mark.parametrize('change',('authority','bool_time','source_missing','candidate_preimage','id','extra','schema','duplicate','kind'))
def test_valid_native_plan_rejects_typed_alias_and_unbound_edits(registered_case,change):
    from quantos.contracts import registered_research_run as contract
    plan=copy.deepcopy(registered_case['prepare']())
    assert contract.validate_plan(plan)==plan
    if change=='authority':plan['submission_enabled']=0
    if change=='bool_time':plan['search']['recorded_at_ms']=False
    if change=='source_missing':plan['source_identity'].pop('architecture')
    if change=='candidate_preimage':plan['trials'][0]['definition_preimage']['candidate']='trend'
    if change=='id':plan['trials'][0]['native_trial_id']='0'*64
    if change=='extra':plan['human_approval']=True
    if change=='schema':plan['schema']='QUANTOS_REGISTERED_RESEARCH_RUN_V0'
    if change=='duplicate':plan['trials'][1]=copy.deepcopy(plan['trials'][0])
    if change=='kind':plan['search']['search_kind']='BACKTEST'
    with pytest.raises(ValueError):contract.validate_plan(plan)


def test_shared_large_values_are_bounded_before_json_expansion(monkeypatch):
    from quantos.contracts import registered_research_run as contract
    shared='x'*(4*1024*1024)
    monkeypatch.setattr(contract.json,'dumps',lambda *a,**kw:pytest.fail('unbounded JSON expansion'))
    with pytest.raises(ValueError,match='BYTES'):contract.canonical([shared]*100)


def test_registered_pure_module_has_no_producer_or_service_imports():
    import ast,inspect
    from quantos.contracts import registered_research_run as contract
    tree=ast.parse(inspect.getsource(contract))
    for node in ast.walk(tree):
        if isinstance(node,ast.ImportFrom):
            assert not (node.module or '').startswith(('services.','tools.','live.'))


@pytest.mark.parametrize('values', [[None] * 10000, [False, True] * 5000],
                         ids=['null-native-values', 'bool-native-values'])
def test_canonical_native_flags_respect_original_json_cpu_budget(values):
    import json
    import time
    from statistics import median
    from quantos.contracts.registered_research_run import canonical
    arguments = dict(sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    expected = json.dumps(values, **arguments).encode()
    ratios = []
    for _ in range(5):
        started = time.process_time_ns()
        assert json.dumps(values, **arguments).encode() == expected
        json_cpu = time.process_time_ns() - started
        started = time.process_time_ns()
        assert canonical(values) == expected
        bounded_cpu = time.process_time_ns() - started
        ratios.append(bounded_cpu / json_cpu)
    assert median(ratios) <= 15, f'canonical native CPU ratios: {ratios}'


def test_canonical_retains_original_node_depth_and_integer_limits():
    from quantos.contracts.registered_research_run import canonical
    assert canonical({'x': None}, max_nodes=3) == b'{"x":null}'
    with pytest.raises(ValueError, match='COMPLEXITY'):
        canonical({'x': None}, max_nodes=2)
    tree = None
    for _ in range(64):
        tree = [tree]
    assert canonical(tree)
    with pytest.raises(ValueError, match='COMPLEXITY'):
        canonical([tree])
    assert canonical(2**4095) == str(2**4095).encode()
    with pytest.raises(ValueError, match='INTEGER'):
        canonical(2**4096)
    with pytest.raises(ValueError, match='COMPLEXITY_LIMIT'):
        canonical(None, max_nodes=True)


@pytest.mark.parametrize('value', [None, 'x', {'x': False}],
                         ids=['null', 'string', 'mapping'])
def test_nonfinite_byte_limit_rejects_before_json_allocation(monkeypatch, value):
    from quantos.contracts import registered_research_run as contract
    monkeypatch.setattr(contract.json, 'dumps', lambda *a, **kw: pytest.fail('unbounded JSON expansion'))
    with pytest.raises(ValueError, match='BYTES'):
        contract.canonical(value, maximum=float('nan'))


def test_canonical_retains_original_conservative_byte_budget():
    from quantos.contracts.registered_research_run import canonical
    assert canonical([None], maximum=7) == b'[null]'
    with pytest.raises(ValueError, match='BYTES'):
        canonical([None], maximum=6)
