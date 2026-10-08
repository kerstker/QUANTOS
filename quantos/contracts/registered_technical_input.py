"""Read finite native descriptions without importing or constructing producers.

The manifest_inputs trees are emitted using the runner's shared native material
builder. This parser validates their transport and binds them to the original
manifest; it does not replay venue, execution or risk algorithms.
"""
from dataclasses import dataclass
import copy

from quantos.contracts.backtest_identity import decode_identity_payload, exact_identity_hash, RunManifestV2
from quantos.contracts.registered_research_run import canonical, require

SCHEMA='QUANTOS_REGISTERED_TECHNICAL_INPUT_V1'
ARGUMENTS=frozenset('release_identity dataset_identity universe_snapshot alpha_definition initial_collateral contract_rules funding_policies market_events decisions parameters random_seed'.split())
NATIVE_FIELDS={
'services.execution.authoritative_perpetual_backtest.CausalDataRecord': 'record_id event_time available_time ingestion_time source_identity value',
'services.execution.authoritative_perpetual_backtest.HistoricalContractRule': 'rule_id spec effective_from effective_until observed_time available_time ingestion_time source_identity',
'services.execution.authoritative_perpetual_backtest.FundingPolicySnapshot': 'instrument_id venue_id policy_epoch_id coverage_start coverage_end settlement_interval_ms settlement_times observed_time available_time ingestion_time source_identity',
'services.execution.authoritative_perpetual_backtest.ObservedLedgerEvent': 'event available_time ingestion_time source_identity rule_id',
'services.execution.authoritative_perpetual_backtest.CapacitySnapshot': 'instrument_id policy adv_window_start adv_window_end available_time ingestion_time source_identity',
'services.execution.authoritative_perpetual_backtest.CostModelSnapshot': 'model_identity calibration_end available_time ingestion_time source_identity fee_rate spread_rate impact_rate',
'services.execution.authoritative_perpetual_backtest.ExecutableVenueEvent': 'timestamp execution_price',
'services.execution.authoritative_perpetual_backtest.ExecutionOpportunitySnapshot': 'instrument_id venue_id coverage_start coverage_end executable_events available_time ingestion_time source_identity',
'services.execution.authoritative_perpetual_backtest.ExecutionRequest': 'execution_id timestamp instrument_id quantity_delta execution_price margin_mode isolated_margin rule_id capacity cost_model opportunity available_time ingestion_time source_identity price_basis',
'services.execution.authoritative_perpetual_backtest.ResearchRiskEvidence': 'returns initial_equity equity_curve timestamps full_inputs config observed_time available_time ingestion_time source_identity source_records',
'services.execution.authoritative_perpetual_backtest.DecisionRequest': 'decision_id timestamp signal_inputs execution capacity_snapshots research_risk',
'services.execution.perpetual_ledger_v2.MarginBracket': 'notional_floor notional_cap initial_margin_rate maintenance_margin_rate maintenance_amount',
'services.execution.perpetual_ledger_v2.ContractSpec': 'instrument_id contract_type margin_asset contract_multiplier quantity_step min_quantity min_notional liquidation_fee_rate settlement_quantum margin_brackets new_risk_notional_cap margin_overflow_policy',
'services.execution.perpetual_ledger_v2.CapacityPolicy': 'venue_id window_id period_rule_id period_index adv_notional max_participation_rate action',
'services.execution.perpetual_ledger_v2.LiquidationBackstop': 'insurance_adjustment adl_adjustment',
'services.execution.perpetual_ledger_v2.FundingEvent': 'timestamp instrument_id funding_rate mark_price liquidation_backstop',
'services.execution.perpetual_ledger_v2.MarkEvent': 'timestamp instrument_id mark_price observed_trade_price liquidation_backstop',
'services.risk.research_risk.domain.models.RiskConfig': 'max_portfolio_volatility max_var_95 max_cvar_95 max_drawdown max_drawdown_duration max_single_alpha_weight max_cluster_weight max_pairwise_correlation max_average_correlation max_liquidity_usage max_participation_rate max_liquidation_days max_leverage max_turnover max_cost_drag min_effective_number_of_bets min_capacity max_capacity_usage max_worst_regime_loss max_stress_loss min_validation_confidence min_paper_confidence periods_per_year seed',
'services.risk.research_risk.dry_run_guard.DryRunResearchRiskInputs': 'capacity_cny daily_volume_cny total_aum alpha_returns regime_labels alpha_metadata timestamps',
}
ENUM_VALUES={
    'services.execution.perpetual_ledger_v2.MarginMode': ('CROSS','ISOLATED'),
    'services.execution.perpetual_ledger_v2.CapacityAction': ('REJECT','PARTIAL'),
}


@dataclass(frozen=True)
class NativeDescription:
    """Inert data only: never an executable native object."""
    type_name: str
    fields: dict


def _decode(node):
    require(type(node) is list and len(node) in (2,3) and type(node[0]) is str,'TECHNICAL_NODE')
    tag=node[0]
    if tag=='value':
        require(len(node)==2,'TECHNICAL_VALUE')
        return decode_identity_payload(node[1])
    if tag=='native':
        require(len(node)==3 and type(node[1]) is str and node[1] in NATIVE_FIELDS
                and type(node[2]) is list,'TECHNICAL_NATIVE_TYPE')
        pairs=node[2]
        require(all(type(p) is list and len(p)==2 and type(p[0]) is str for p in pairs),'TECHNICAL_FIELDS')
        require([p[0] for p in pairs]==NATIVE_FIELDS[node[1]].split(),'TECHNICAL_FIELDS')
        return NativeDescription(node[1],{k:_decode(v) for k,v in pairs})
    if tag=='native_enum':
        require(len(node)==3 and type(node[1]) is str and node[1] in ENUM_VALUES,'TECHNICAL_ENUM')
        value=decode_identity_payload(node[2])
        require(type(value) is str and value in ENUM_VALUES[node[1]],'TECHNICAL_ENUM_VALUE')
        return NativeDescription(node[1],{'value':value})
    require(len(node)==2 and type(node[1]) is list,'TECHNICAL_CONTAINER')
    if tag=='native_mapping':
        pairs=node[1]
        require(all(type(p) is list and len(p)==2 for p in pairs),'TECHNICAL_PAIRS')
        require([canonical(p[0]) for p in pairs]==sorted(canonical(p[0]) for p in pairs),'TECHNICAL_ORDER')
        result={}
        for key,value in pairs:
            key=_decode(key)
            try:
                require(key not in result,'TECHNICAL_DUPLICATE_KEY')
                result[key]=_decode(value)
            except TypeError as exc: raise ValueError('REGISTERED_RESEARCH_TECHNICAL_KEY') from exc
        return result
    if tag in ('tuple','list'):
        result=[_decode(v) for v in node[1]]
        return tuple(result) if tag=='tuple' else result
    if tag=='frozenset':
        require([canonical(v) for v in node[1]]==sorted(canonical(v) for v in node[1]),'TECHNICAL_ORDER')
        try:
            result=frozenset(_decode(v) for v in node[1])
        except TypeError as exc: raise ValueError('REGISTERED_RESEARCH_TECHNICAL_SET') from exc
        require(len(result)==len(node[1]),'TECHNICAL_DUPLICATE_SET')
        return result
    raise ValueError('REGISTERED_RESEARCH_TECHNICAL_UNKNOWN_TAG')


def project_manifest_inputs(document, *, manifest=None):
    """Derive the old three-tree transport from an explicitly bound full input.

This authenticates no caller and grants no research qualification. Consumers
must bind the full input bytes to their registered plan and real native record.
"""
    canonical(document)  # depth/node/expanded byte bounds before recursion
    require(type(document) is dict and set(document)=={'schema','arguments','manifest_inputs'}
            and document['schema']==SCHEMA,'TECHNICAL_DOCUMENT')
    arguments=_decode(document['arguments'])
    require(type(arguments) is dict and set(arguments)==ARGUMENTS,'TECHNICAL_ARGUMENTS')
    trees=document['manifest_inputs']
    require(type(trees) is dict and set(trees)=={'release_identity','data_identity','universe'},'MANIFEST_INPUT_FIELDS')
    values={k:decode_identity_payload(v) for k,v in trees.items()}
    require(exact_identity_hash(values['release_identity'])==exact_identity_hash(arguments['release_identity']),'MANIFEST_RELEASE')
    universe=arguments['universe_snapshot']
    require(isinstance(universe,NativeDescription)
            and universe.type_name=='services.execution.authoritative_perpetual_backtest.CausalDataRecord','MANIFEST_UNIVERSE_TYPE')
    require(exact_identity_hash(values['universe'])==exact_identity_hash(universe.fields),'MANIFEST_UNIVERSE')
    data=values['data_identity']
    require(type(data) is dict and set(data)=={'dataset_identity','universe_snapshot','signal_inputs',
        'research_risk_inputs','market_events','execution_observations'},'MANIFEST_DATA_FIELDS')
    require(exact_identity_hash(data['dataset_identity'])==exact_identity_hash(arguments['dataset_identity'])
            and exact_identity_hash(data['universe_snapshot'])==exact_identity_hash(values['universe']),'MANIFEST_DATA')
    # These direct CausalDataRecord dictionaries use exactly all original fields.
    decisions=arguments['decisions']
    require(type(decisions) in (list,tuple) and decisions and all(isinstance(d,NativeDescription)
        and d.type_name=='services.execution.authoritative_perpetual_backtest.DecisionRequest' for d in decisions),'MANIFEST_DECISIONS')
    signals=[]
    for decision in decisions:
        for value in decision.fields['signal_inputs']:
            require(isinstance(value,NativeDescription) and value.type_name==universe.type_name,'MANIFEST_SIGNAL')
            signals.append(value.fields)
    require(exact_identity_hash(data['signal_inputs'])==exact_identity_hash(signals),'MANIFEST_SIGNALS')
    require(type(data['research_risk_inputs']) in (list,tuple) and len(data['research_risk_inputs'])==len(decisions)
        and type(data['execution_observations']) in (list,tuple) and len(data['execution_observations'])==len(decisions)
        and type(data['market_events']) in (list,tuple) and len(data['market_events'])==len(arguments['market_events']),'MANIFEST_COUNTS')
    if manifest is not None:
        value=RunManifestV2.from_dict(manifest)
        require(value.release_hash==exact_identity_hash(values['release_identity'])
            and value.data_hash==exact_identity_hash(data)
            and value.universe_hash==exact_identity_hash(values['universe'])
            and value.alpha_hash==exact_identity_hash(arguments['alpha_definition']),'MANIFEST_HASH')
    return dict(schema='QUANTOS_TECHNICAL_PERPETUAL_INPUT_V1',values=copy.deepcopy(trees))
