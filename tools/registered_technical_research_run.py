"""Finite native technical producer wrapper; transport never dynamically imports.

Restart accepts original native kwargs and compares their complete transport.
It cannot reconstruct executable Python objects from research JSON.
"""
import copy
import dataclasses
from decimal import Decimal
from enum import Enum
import time

from quantos.contracts.backtest_identity import encode_identity_payload, exact_identity_hash
from quantos.contracts import registered_research_run as contract
from quantos.contracts.registered_technical_input import project_manifest_inputs
from services.execution import authoritative_perpetual_backtest as native
from tools.perpetual_replay_research import source_identity
from tools import registered_research_run as common

_NATIVE_MODULES = frozenset(('services.execution.authoritative_perpetual_backtest',
    'services.execution.perpetual_ledger_v2', 'services.risk.research_risk.domain.models',
    'services.risk.research_risk.dry_run_guard'))
_ARGUMENTS = frozenset('release_identity dataset_identity universe_snapshot alpha_definition initial_collateral contract_rules funding_policies market_events decisions parameters random_seed'.split())


def technical_input(arguments):
    """Lossless, finite allowlisted *description* of actual native arguments."""
    contract.require(type(arguments) is dict and set(arguments)==_ARGUMENTS,'TECHNICAL_ARGUMENTS')
    nodes=0; budget=0
    def walk(value,depth=0):
        nonlocal nodes,budget
        nodes+=1
        contract.require(nodes<=100_000 and depth<=64,'TECHNICAL_COMPLEXITY')
        cls=type(value)
        budget+=16
        if cls in (str,bytes):
            contract.require(len(value)<=contract.MAX_INPUT_BYTES,'INPUT_BYTES')
            budget+=len(value.encode('utf-8')) if cls is str else (len(value)*4+2)//3
        contract.require(budget<=contract.MAX_INPUT_BYTES,'INPUT_BYTES')
        if dataclasses.is_dataclass(value) and not isinstance(value,type):
            contract.require(cls.__module__ in _NATIVE_MODULES,'TECHNICAL_DATACLASS')
            return ['native',cls.__module__+'.'+cls.__name__,
                    [[field.name,walk(getattr(value,field.name),depth+1)] for field in dataclasses.fields(value)]]
        if isinstance(value,Enum):
            contract.require(cls.__module__ in _NATIVE_MODULES,'TECHNICAL_ENUM')
            return ['native_enum',cls.__module__+'.'+cls.__name__,encode_identity_payload(value.value)]
        if cls is dict:
            pairs=[[walk(k,depth+1),walk(v,depth+1)] for k,v in value.items()]
            return ['native_mapping',sorted(pairs,key=lambda pair:contract.canonical(pair[0]))]
        if cls in (tuple,list):
            return [cls.__name__,[walk(v,depth+1) for v in value]]
        if cls is frozenset:
            return ['frozenset',sorted([walk(v,depth+1) for v in value],key=contract.canonical)]
        return ['value',encode_identity_payload(value)]
    description=walk(arguments)
    original=native.research_manifest_inputs(**{k:arguments[k] for k in
        ('release_identity','dataset_identity','universe_snapshot','decisions','market_events','contract_rules')})
    value=dict(schema='QUANTOS_REGISTERED_TECHNICAL_INPUT_V1',arguments=description,
               manifest_inputs={k:encode_identity_payload(v) for k,v in original.items()})
    contract.canonical(value)
    project_manifest_inputs(value)
    return value


def _candidate(arguments):
    alpha=arguments['alpha_definition']
    contract.require(type(alpha) is dict and type(alpha.get('alpha_id')) is str,'TECHNICAL_CANDIDATE')
    definition=copy.deepcopy(alpha)
    mapping=definition.get('position_mapping')
    contract.require(type(mapping) is dict and mapping.get('schema')=='DIRECT_LINEAR_TARGET_QUANTITY_V1','TECHNICAL_MAPPING')
    for field in ('quantity_scale','max_abs_quantity'):
        contract.require(type(mapping.get(field)) is Decimal,'TECHNICAL_DECIMAL')
        mapping[field]=str(mapping[field])
    preimage=dict(candidate_definition=definition,alpha_identity=encode_identity_payload(alpha),
                  alpha_hash=exact_identity_hash(alpha))
    return alpha['alpha_id'],contract.digest(definition),preimage


def prepare_technical(arguments, *, project_root, search_id, producer_id, actor_id,
                      dataset_version, registered_at_ms, invocation_id=None, evaluation_core=None, risk_review_policy=None):
    source=source_identity()
    contract.require(contract.canonical(source)==contract.canonical(arguments['release_identity']),'SOURCE')
    if evaluation_core is not None:
        from quantos.contracts.research_evaluation_plan import validate_core
        validate_core(evaluation_core)
        contract.require(evaluation_core['dataset_manifest_sha256'] == contract.digest(arguments['dataset_identity']),
                         'EVALUATION_DATASET')
    return common._prepare(contract.canonical(technical_input(arguments)),project_root=project_root,
        adapter=contract.TECHNICAL,source=source,candidates=[_candidate(arguments)],search_id=search_id,
        producer_id=producer_id,actor_id=actor_id,dataset_version=dataset_version,
        registered_at_ms=registered_at_ms,invocation_id=invocation_id,evaluation_core=evaluation_core,risk_review_policy=risk_review_policy)


def run_technical(arguments, *, destination, registry, clock_ms=None):
    arguments=copy.deepcopy(arguments)
    raw=contract.canonical(technical_input(arguments))
    def verify(plan):
        contract.require(contract.canonical(plan['source_identity'])==contract.canonical(source_identity())
            and contract.canonical(plan['source_identity'])==contract.canonical(arguments['release_identity']),'SOURCE_CHANGED')
        contract.require(contract.canonical(technical_input(arguments))==raw,'TECHNICAL_INPUT_CHANGED')
        if plan['schema'] in contract.EVALUATED_SCHEMAS:
            contract.require(plan['evaluation_core']['dataset_manifest_sha256'] == contract.digest(arguments['dataset_identity']),
                             'EVALUATION_DATASET')
        name,definition,preimage=_candidate(arguments)
        contract.require(len(plan['trials'])==1 and plan['trials'][0]['candidate_id']==name
            and plan['trials'][0]['config_hash']==definition
            and contract.canonical(plan['trials'][0]['definition_preimage'])==contract.canonical(preimage),'NATIVE_CANDIDATES')
    def execute():
        result=native.run_authoritative_linear_perpetual_v2(**arguments)
        project_manifest_inputs(technical_input(arguments),manifest=result['detail']['run_manifest_v2'])
        return result
    saved=contract.validate_plan(common._load(common.Path(destination)/'plan.json',maximum=contract.MAX_PLAN_BYTES))
    def replay(plan,report):
        from services.execution.research_risk_replay import replay_technical_risk
        return replay_technical_risk(document=technical_input(arguments),original_result=report)
    result=common._native_run(destination=destination,input_raw=raw,registry=registry,
        clock_ms=clock_ms or (lambda:time.time_ns()//1_000_000),adapter=contract.TECHNICAL,
        execute=execute,verify_source=verify,
        replay_risk=replay if saved['schema']==contract.RISK_EVALUATED_SCHEMA else None)
    project_manifest_inputs(technical_input(arguments),manifest=result['detail']['run_manifest_v2'])
    return result
