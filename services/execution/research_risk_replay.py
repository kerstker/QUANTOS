"""Reconsume retained technical inputs through the original ledger and risk gate.

This is an offline diagnostic adapter, not independent mathematical validation.
No source trust, strategy attribution, regime/model provenance, qualification,
order permissions, files, or services are supplied by this module.
"""
import copy
from collections import defaultdict
from decimal import Decimal

from quantos.contracts.backtest_identity import decode_identity_payload, exact_identity_hash
from quantos.contracts import registered_technical_input as transport
from quantos.contracts.registered_research_run import canonical, digest
from quantos.contracts.research_evidence_json import decode
from quantos.contracts import research_risk_replay as contract
from services.execution import authoritative_perpetual_backtest as native
from services.execution import perpetual_ledger_v2 as ledger_native
from services.risk.research_risk.domain.models import RiskConfig
from services.risk.research_risk.dry_run_guard import DryRunResearchRiskInputs
# Original Gold research math is composed at this existing offline execution
# boundary. Research report leases consume this port without importing risk.
from services.risk.research_risk.gold_research_replay import review_gold_account as review_gold_account


# Explicit static imports only. A tagged research object can never select a module.
_CLASSES = (
    native.CausalDataRecord, native.HistoricalContractRule, native.FundingPolicySnapshot,
    native.ObservedLedgerEvent, native.CapacitySnapshot, native.CostModelSnapshot,
    native.ExecutableVenueEvent, native.ExecutionOpportunitySnapshot, native.ExecutionRequest,
    native.ResearchRiskEvidence, native.DecisionRequest, ledger_native.MarginBracket,
    ledger_native.ContractSpec, ledger_native.CapacityPolicy, ledger_native.LiquidationBackstop,
    ledger_native.FundingEvent, ledger_native.MarkEvent, RiskConfig, DryRunResearchRiskInputs,
    ledger_native.MarginMode, ledger_native.CapacityAction,
)
_NATIVE = {cls.__module__ + '.' + cls.__name__: cls for cls in _CLASSES}


def decode_technical_input(document, *, manifest=None):
    """Bounded static reconstruction plus all original native manifest material."""
    document = decode(canonical(document))
    transport.project_manifest_inputs(document, manifest=manifest)
    description = transport._decode(document['arguments'])

    def materialize(value):
        if isinstance(value, transport.NativeDescription):
            cls = _NATIVE.get(value.type_name)
            contract.require(cls is not None, 'NATIVE_TYPE')
            fields = {key: materialize(child) for key, child in value.fields.items()}
            if value.type_name in transport.ENUM_VALUES:
                return cls(fields['value'])
            return cls(**fields)
        if type(value) is dict:
            return {key: materialize(child) for key, child in value.items()}
        if type(value) in (list, tuple):
            return type(value)(materialize(child) for child in value)
        if type(value) is frozenset:
            return frozenset(materialize(child) for child in value)
        return value

    arguments = materialize(description)
    original = native.research_manifest_inputs(**{key: arguments[key] for key in (
        'release_identity', 'dataset_identity', 'universe_snapshot', 'decisions',
        'market_events', 'contract_rules')})
    for key, value in original.items():
        claimed = decode_identity_payload(document['manifest_inputs'][key])
        contract.require(exact_identity_hash(value) == exact_identity_hash(claimed), 'MANIFEST_INPUT_' + key.upper())
    return arguments


def validate_risk_keys(weights, alpha_returns):
    """Key agreement is necessary; it does not prove strategy ownership."""
    contract.require(type(weights) is dict and type(alpha_returns) is dict
                     and weights and set(weights) == set(alpha_returns)
                     and all(type(key) is str and key for key in weights), 'RISK_KEY_BINDING')


@ledger_native.ledger_decimal_context()
def _ledger_points(arguments, result):
    """Replay whole timestamp batches after the native runner proved every fill.

    The first supplied event is the finite source cut. No state is extended into
    earlier caller history. Prefix hashes bind original complete observations,
    including availability, rule, capacity and cost, rather than report summaries.
    """
    specs = {rule.spec.instrument_id: rule.spec for rule in arguments['contract_rules']}
    payload = result['detail']['result_hash_payload']
    accepted = payload['execution_results']
    contract.require(len(accepted) == len(arguments['decisions']), 'ACCEPTED_EXECUTIONS')
    batches = defaultdict(list)
    inputs = defaultdict(list)
    for observed in arguments['market_events']:
        batches[observed.event.timestamp].append(observed.event)
        inputs[observed.event.timestamp].append(observed.as_canonical_dict())
    for decision, actual in zip(arguments['decisions'], accepted, strict=True):
        execution = decision.execution
        contract.require(actual['execution_id'] == execution.execution_id and actual['status'] == 'FILLED'
                         and Decimal(actual['filled_quantity']) == execution.quantity_delta, 'ACCEPTED_EXECUTION')
        batches[execution.timestamp].append(execution.build_trade_event(specs[execution.instrument_id]))
        inputs[execution.timestamp].append(execution.as_canonical_dict(specs[execution.instrument_id]))
    source_cut = min(batches)
    sample_times = {time for decision in arguments['decisions'] for time in decision.research_risk.timestamps}
    book = ledger_native.LinearPerpetualLedger(tuple(specs.values()), initial_collateral=arguments['initial_collateral'])
    prefix = digest({'schema': 'QUANTOS_RISK_INPUT_PREFIX_V1', 'input_events': 0})
    count = 0
    points = {}
    for timestamp in sorted(set(batches) | {t for t in sample_times if t >= source_cut}):
        if timestamp in batches:
            book.process_events(tuple(batches[timestamp]))
            count += len(batches[timestamp])
            prefix = digest({'previous_sha256': prefix, 'timestamp': timestamp,
                             'batch_sha256': exact_identity_hash(inputs[timestamp]), 'input_events': count})
        if timestamp in sample_times:
            points[timestamp] = {
                'actual_equity': ledger_native.canonical_decimal(book.account_equity()),
                'state_sha256': exact_identity_hash(book.state_as_canonical_dict()),
                'input_prefix_sha256': prefix, 'input_events': count,
            }
    for key, actual in (
        ('ending_state', book.state_as_canonical_dict()),
        ('ledger_events', [dict(row) for row in book.event_records]),
        ('reconciliation', book.reconciliation_snapshot().as_canonical_dict()),
        ('liquidations', [row.as_canonical_dict() for row in book.liquidations]),
    ):
        contract.require(exact_identity_hash(payload[key]) == exact_identity_hash(actual), 'LEDGER_' + key.upper())
    return source_cut, points


def _coverage(evidence, *, initial, source_cut, points):
    rows = []
    reasons = []
    for timestamp, claimed in zip(evidence.timestamps, evidence.equity_curve, strict=True):
        claim = ledger_native.canonical_decimal(Decimal(str(claimed)))
        actual = points.get(timestamp)
        if actual is None:
            rows.append(dict(timestamp=timestamp, status='UNCOVERED', claimed_equity=claim,
                             actual_equity=None, state_sha256=None, input_prefix_sha256=None, input_events=None))
        else:
            rows.append(dict(timestamp=timestamp, claimed_equity=claim, **actual,
                             status='MATCH' if Decimal(claim) == Decimal(actual['actual_equity']) else 'MISMATCH'))
    claimed_initial = Decimal(str(evidence.initial_equity))
    # Initial balance can only be compared when the whole history is covered.
    uncovered = any(row['status'] == 'UNCOVERED' for row in rows)
    mismatch = any(row['status'] == 'MISMATCH' for row in rows)
    if uncovered:
        reasons.append('HISTORY_BEFORE_SOURCE_CUT')
    elif claimed_initial != initial:
        reasons.append('INITIAL_EQUITY_MISMATCH')
        mismatch = True
    if any(row['status'] == 'MISMATCH' for row in rows):
        reasons.append('HISTORY_LEDGER_MISMATCH')
    status = 'MISMATCH' if mismatch else ('INSUFFICIENT' if uncovered else 'MATCH')
    return dict(status=status, source_cut_ms=source_cut,
                initial_equity=ledger_native.canonical_decimal(initial),
                claimed_initial_equity=ledger_native.canonical_decimal(claimed_initial), points=rows), reasons


@ledger_native.ledger_decimal_context()
def replay_technical_risk(*, document, original_result):
    """Return engineering evidence for one successful original native run.

    Failed/unknown native attempts have no successful record and cannot enter
    this adapter. The project reader must keep them in its full population.
    Original risk claims and hashes are never modified. Reexecuting the same
    implementation establishes reproducibility, not independent mathematics.
    """
    document = decode(canonical(document))
    original_result = decode(canonical(original_result))
    arguments = decode_technical_input(document, manifest=original_result['detail']['run_manifest_v2'])
    rerun = native.run_authoritative_linear_perpetual_v2(**arguments)
    contract.require(canonical(rerun) == canonical(original_result), 'ORIGINAL_RESULT')
    source_cut, points = _ledger_points(arguments, rerun)
    payload = rerun['detail']['result_hash_payload']
    decisions = []
    for item, context in zip(arguments['decisions'], payload['decision_contexts'], strict=True):
        coverage, reasons = _coverage(item.research_risk, initial=arguments['initial_collateral'],
                                      source_cut=source_cut, points=points)
        try:
            validate_risk_keys(context['target_weights'], dict(item.research_risk.full_inputs.alpha_returns))
            key_binding = 'MATCH'
        except ValueError:
            key_binding = 'UNMAPPED'
            reasons.append('RISK_KEY_UNMAPPED')
        # A single run's alpha_id is not a multi-strategy allocation ledger.
        reasons += ['POSITION_ATTRIBUTION_UNAVAILABLE', 'REGIME_PROVENANCE_UNAVAILABLE',
                    'MODEL_METADATA_PROVENANCE_UNAVAILABLE', 'INSUFFICIENT_CORRELATION_COHORT']
        checks = {}
        for engine in sorted(contract.ENGINES):
            dependencies = []
            if engine in ('market', 'drawdown', 'stress') and coverage['status'] != 'MATCH':
                dependencies += [r for r in reasons if r.startswith(('HISTORY_', 'INITIAL_'))]
            if engine in ('concentration', 'correlation'):
                dependencies += ['POSITION_ATTRIBUTION_UNAVAILABLE']
                if key_binding == 'UNMAPPED':
                    dependencies += ['RISK_KEY_UNMAPPED']
            if engine == 'regime':
                dependencies += ['REGIME_PROVENANCE_UNAVAILABLE']
            if engine == 'model_alpha':
                dependencies += ['MODEL_METADATA_PROVENANCE_UNAVAILABLE']
            status = 'REPLAYED_MATCH'
            if dependencies:
                status = ('REJECTED' if engine in ('market', 'drawdown', 'stress')
                          and coverage['status'] == 'MISMATCH' else 'INSUFFICIENT')
            if engine in ('concentration', 'correlation') and key_binding == 'UNMAPPED':
                status = 'REJECTED'
            checks[engine] = dict(status=status, reasons=dependencies,
                                  reproduction=copy.deepcopy(context['research_risk']['reports'][engine]))
        decisions.append(dict(decision_id=item.decision_id, timestamp=item.timestamp,
            risk_input_sha256=context['risk_input_hash'],
            config_sha256=exact_identity_hash(native._risk_config_dict(item.research_risk.config)),
            target_weights=copy.deepcopy(context['target_weights']), key_binding=key_binding,
            coverage=coverage, engine_checks=checks, reasons=reasons))
    report = dict(schema=contract.SCHEMA, adapter=contract.ADAPTER,
        input_sha256=digest(document), original_result_sha256=digest(original_result),
        run_id=rerun['run_id'], manifest_sha256=digest(rerun['detail']['run_manifest_v2']),
        status='REJECTED' if any(d['coverage']['status'] == 'MISMATCH' or d['key_binding'] == 'UNMAPPED'
                                 for d in decisions) else 'INSUFFICIENT',
        reproducibility=dict(original_result_match=True, ledger_match=True, mathematical_independence=False,
            original_attestations=copy.deepcopy(rerun['detail']['engine_attestations'])),
        decisions=decisions,
        execution_checks=[dict(execution_id=row['execution_id'], timestamp=row['execution_time_ms'],
            risk_equity=row['risk_equity'], target_weights=copy.deepcopy(row['target_weights']),
            research_risk=copy.deepcopy(row['research_risk'])) for row in payload['execution_risk_contexts']],
        limitations=list(contract.LIMITATIONS), submission_enabled=False, economic_authority=False)
    return contract.validate_report(report)
