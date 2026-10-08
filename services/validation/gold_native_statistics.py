"""Consume declared Gold originals without replaying execution or changing cash.

Economic ledger times and receipt availability are separate clocks. This adapter
only moves the knowability of existing equity observations; the original ledger,
orders, event sequence and equity values remain untouched.
"""

import base64
import copy
import hashlib
import json
from decimal import Decimal
from collections.abc import Sequence

from quantos.contracts import registered_research_run as native
from quantos.contracts.gold_forward_evaluation_design import FULL_REVIEW_SCHEMAS
from quantos.contracts.backtest_identity import exact_identity_hash
from quantos.contracts.decimal_arithmetic import ledger_decimal_context
from quantos.contracts.json_integrity import load_json_without_duplicate_keys as decode
from quantos.contracts.research_statistics import fixed_frequency_equity
from quantos.streaming_identity import StreamArray
from services.validation import research_result_projection as projection

METHOD = "NATIVE_GOLD_RECEIPT_AVAILABLE_EQUITY_GRID_V1"
require = native.require


def _time(value):
    require(type(value) is int and 0 <= value < 2**63, "GOLD_STATISTICS_TIME")
    return value


def _decimal(value):
    require(type(value) is str and len(value) <= 100, "GOLD_STATISTICS_DECIMAL")
    number = Decimal(value)
    require(number.is_finite(), "GOLD_STATISTICS_DECIMAL")
    return number


def receipt_available_history(simulation, settlements):
    """Keep original equity/sequence; delay all observations affected by a receipt."""
    history = simulation["equity_history"]
    events = simulation["ledger_events"]
    require(
        type(history) is list
        and 0 < len(history) <= 100_000
        and type(events) is list
        and len(events) <= 100_000
        and type(settlements) is list
        and len(settlements) <= 1000,
        "GOLD_STATISTICS_HISTORY",
    )
    known = _verified_settlements(events, settlements)
    require(
        all(any(p["timestamp"] == t and p["phase"] == "MARKET" for p in history) for t in known),
        "GOLD_STATISTICS_FUNDING_HISTORY",
    )
    return list(_available_rows(history, known))


def _verified_settlements(events, settlements):
    funding = []
    for event in events:
        if event.get("event_type") == "FUNDING_SETTLEMENT":
            require(len(funding)<1000, "GOLD_STATISTICS_FUNDING_COVERAGE")
            funding.append(event)
    known = {}
    for value in settlements:
        timestamp = _time(value["settlement_at_ms"])
        requested = _time(value["requested_at_ms"])
        available = _time(value["available_at_ms"])
        require(timestamp <= requested <= available and timestamp not in known, "GOLD_STATISTICS_RECEIPT_TIME")
        known[timestamp] = value
    require(len(funding) == len(known), "GOLD_STATISTICS_FUNDING_COVERAGE")
    for event in funding:
        value = known.get(_time(event["timestamp"]))
        require(
            value is not None
            and event["instrument_id"] == value["instrument_id"] == "XAUUSDT"
            and _decimal(event["funding_rate"]) == _decimal(value["funding_rate"])
            and _decimal(event["mark_price"]) == _decimal(value["mark_price"]),
            "GOLD_STATISTICS_FUNDING_BINDING",
        )
    require(len({e["timestamp"] for e in funding}) == len(funding), "GOLD_STATISTICS_FUNDING_DUPLICATE")
    return known


def _available_rows(history, known, found=None):
    prior = None
    prior_sequence = -1
    ordered = sorted(known.items())
    index = 0
    available = 0
    for row in history:
        timestamp = _time(row["timestamp"])
        sequence = _time(row["sequence"])
        key = timestamp, sequence
        require((prior is None or key > prior) and sequence > prior_sequence, "GOLD_STATISTICS_HISTORY_ORDER")
        _decimal(row["equity"])
        require(row["phase"] != "INITIAL" or prior is None, "GOLD_STATISTICS_INITIAL_HISTORY")
        if row["phase"] != "INITIAL":
            while index < len(ordered) and ordered[index][0] <= timestamp:
                available = max(available, ordered[index][1]["available_at_ms"])
                index += 1
        if found is not None and row['phase']=='MARKET' and timestamp in known:
            found.add(timestamp)
        yield dict(timestamp=max(timestamp, available), sequence=sequence, equity=row["equity"])
        prior = key
        prior_sequence = sequence


def receipt_available_history_stream(simulation, settlements, *, maximum_points, maximum_events):
    """Original cash-availability transform on a complete repeatable disk view.

    A full validation pass precedes exposure. This offline port has explicit new
    budgets; the original native/statistical entry's limits remain unchanged.
    It grants no registration, profitability, candidate or economic authority.
    """
    require(all(type(v) is int and 0<v<=100_000_000 for v in (maximum_points,maximum_events)),
            'GOLD_STATISTICS_STREAM_BUDGET')
    history, events = simulation['equity_history'], simulation['ledger_events']
    require(isinstance(history,Sequence) and 0<len(history)<=maximum_points
        and isinstance(events,Sequence) and len(events)<=maximum_events
        and type(settlements) is list and len(settlements)<=1000, 'GOLD_STATISTICS_HISTORY')
    known=_verified_settlements(events,settlements)
    found=set()
    for _ in _available_rows(history,known,found):
        pass
    require(found==set(known), 'GOLD_STATISTICS_FUNDING_HISTORY')
    return StreamArray(lambda: _available_rows(history,known))


def funding_settlements(document, dataset, *, source_parts=None):
    """Bind each knowledge time to the retained public response and native manifest."""
    from quantos.contracts.gold_native_source import INPUT_SCHEMAS, input_document
    if document.get('input_schema') in INPUT_SCHEMAS:
        input_document(document)
        from collections.abc import Sequence
        manifests = dataset['quote_manifest'].get('segments')
        require(isinstance(source_parts, Sequence) and isinstance(manifests, Sequence)
            and len(source_parts) == len(manifests) == document['source_binding']['part_count'],
            'GOLD_STATISTICS_SOURCE_MATERIAL_REQUIRED')
        known = {}
        for part, manifest in zip(source_parts, manifests, strict=True):
            require(manifest['capture_id'] == part['capture']['capture_id'], 'GOLD_STATISTICS_SEGMENT_IDENTITY')
            for value in funding_settlements(part, {'quote_manifest':manifest}):
                timestamp = _time(value['settlement_at_ms'])
                require(timestamp not in known or known[timestamp] == value, 'GOLD_STATISTICS_SEGMENT_FUNDING_CONFLICT')
                known[timestamp] = value
        require(len(known) <= 1000, 'GOLD_STATISTICS_FUNDING_BOUND')
        return [known[t] for t in sorted(known)]
    if 'input_schema' in document or 'continuation_segments' in document:
        from quantos.contracts import gold_segmented_input as segments
        parts = segments.documents(document)
        segments.journals(document)
        manifests = dataset['quote_manifest'].get('segments')
        require(dataset.get('input_segment_count') == len(parts)
            and type(manifests) is list and len(manifests) == len(parts),
            'GOLD_STATISTICS_SEGMENT_COUNT')
        known = {}
        for part, manifest in zip(parts, manifests):
            require(manifest['capture_id'] == part['capture']['capture_id'],
                'GOLD_STATISTICS_SEGMENT_IDENTITY')
            for value in funding_settlements(part_without_segments(part), {'quote_manifest':manifest}):
                timestamp = _time(value['settlement_at_ms'])
                require(timestamp not in known or known[timestamp] == value,
                        'GOLD_STATISTICS_SEGMENT_FUNDING_CONFLICT')
                known[timestamp] = value
        require(len(known) <= 1000, 'GOLD_STATISTICS_FUNDING_BOUND')
        return [known[t] for t in sorted(known)]
    records = document.get("observed_funding_records", [])
    observed = dataset["quote_manifest"].get("observed_funding")
    if observed is None:
        require(not records, "GOLD_STATISTICS_FUNDING_MANIFEST")
        return []
    require(type(records) is list and 0 < len(records) <= 1000, "GOLD_STATISTICS_FUNDING_RECEIPTS")
    bindings = []
    payloads = []
    for record in records:
        require(record["kind"] == "funding", "GOLD_STATISTICS_FUNDING_KIND")
        requested = _time(record["requested_at"])
        available = _time(record["received_at"])
        require(requested <= available and available - requested <= 30_000, "GOLD_STATISTICS_RECEIVE_WINDOW")
        raw = base64.b64decode(record["response_base64"], validate=True)
        require(
            len(raw) <= 10_000_000
            and hashlib.sha256(raw).hexdigest() == record["response_sha256"]
            and decode(raw) == record["payload"],
            "GOLD_STATISTICS_FUNDING_RESPONSE",
        )
        require(type(record["payload"]) is list and len(record["payload"]) <= 1000, "GOLD_STATISTICS_FUNDING_RESPONSE")
        bindings.append(
            dict(
                record_sha256=exact_identity_hash(record, name="funding_receipt"),
                requested_at_ms=requested,
                available_at_ms=available,
                response_sha256=record["response_sha256"],
            )
        )
        payloads.append((record["payload"], bindings[-1]))
    require(
        bindings == observed["receipts"] and observed["account_cash_verified"] is False,
        "GOLD_STATISTICS_FUNDING_RECEIPT_BINDING",
    )
    values = (
        [v["settlement"] for v in observed["settlements"]] if "settlements" in observed else [observed["settlement"]]
    )
    for value in values:
        timestamp = _time(value["settlement_at_ms"])
        require(
            value["schema"] == "GOLD_OBSERVED_FUNDING_SETTLEMENT_V1"
            and value["account_cash_verified"] is False
            and value["historical_publication_time_known"] is False,
            "GOLD_STATISTICS_FUNDING_QUALIFICATION",
        )
        matches = [
            (r, binding)
            for rows, binding in payloads
            for r in rows
            if r.get("symbol") == value["instrument_id"] and r.get("fundingTime") == timestamp
        ]
        require(
            matches
            and all(
                _decimal(r["fundingRate"]) == _decimal(value["funding_rate"])
                and _decimal(r["markPrice"]) == _decimal(value["mark_price"])
                for r, _ in matches
            ),
            "GOLD_STATISTICS_FUNDING_VALUE",
        )
        _, earliest = min(matches, key=lambda pair: pair[1]["available_at_ms"])
        require(
            timestamp <= earliest["requested_at_ms"]
            and all(value[k] == earliest[k] for k in ("requested_at_ms", "available_at_ms"))
            and value["source_sha256"] == earliest["response_sha256"],
            "GOLD_STATISTICS_FUNDING_AVAILABILITY",
        )
    return values


def part_without_segments(part):
    return {k:v for k,v in part.items() if k not in ('input_schema','continuation_segments')}


def _plain_dataset(value):
    from quantos.streaming_identity import json_parts
    # Dataset metadata keeps the original leaf/receipt/segment bounds. Full
    # account history is never materialized through this compatibility seam.
    return json.loads(b"".join(json_parts(value, maximum_bytes=native.MAX_OUTPUT_BYTES)))


def _report(run, *, reports=None):
    from services.validation.project_research_evidence_authority import _original
    from quantos.contracts.gold_forward_evaluation_design import bind_input, bind_plan

    plan = run["plan"]
    if plan['schema'] in native.EVALUATED_SOURCE_REANALYSIS_SCHEMAS:
        from quantos.contracts.gold_native_source import bind_plan as bind_source_plan
        from quantos.contracts.project_chunked_results import report_for
        document = _original(run, 'input.json')
        bind_source_plan(plan, document)
        return report_for(run, reports), document
    require(plan["schema"] in native.FORWARD_GOLD_SCHEMAS, "GOLD_STATISTICS_DECLARATION")
    document = _original(run, "input.json")
    bind_plan(plan["forward_design"], plan)
    bind_input(plan["forward_design"], plan, document)
    if plan["schema"] in native.CHUNKED_GOLD_SCHEMAS:
        from quantos.contracts.project_chunked_results import report_for
        return report_for(run, reports), document
    report = _original(run, "archive/report.json")
    require(
        report.get("schema") == "GOLD_BAR_PUBLIC_COMPARISON_V1"
        and report["release_identity"] == plan["source_identity"]
        and all(
            report.get(k) is False
            for k in ("authoritative", "economic_eligible", "submission_enabled", "historical_oos_evaluated")
        )
        and report["result_hash"]
        == exact_identity_hash(
            {k: v for k, v in report.items() if k != "result_hash"}, name="gold_bar_public_comparison"
        ),
        "GOLD_STATISTICS_REPORT",
    )
    return report, document


def _simulation(report, candidate, *, streamed=False):
    from quantos.contracts.gold_native_source import simulation_budget
    from quantos.streaming_identity import exact_stream_identity_hash
    identity_hash = exact_stream_identity_hash if streamed else exact_identity_hash
    if candidate == "net_portfolio":
        portfolio = report["portfolio"]
        require(
            portfolio["result_hash"]
            == identity_hash(
                {k: v for k, v in portfolio.items() if k != "result_hash"}, name="gold_net_portfolio"
            ),
            "GOLD_STATISTICS_PORTFOLIO",
        )
        simulation = portfolio["simulation"]
    elif candidate is None:
        simulation = report["cash_baseline"]
        require(not simulation["orders"] and not simulation["ending_state"]["positions"], "GOLD_STATISTICS_BASELINE")
    else:
        rows = [row for row in report["trials"] if row["candidate"] == candidate]
        require(len(rows) == 1, "GOLD_STATISTICS_CANDIDATE")
        simulation = rows[0]["simulation"]
    require(
        simulation_budget(report['dataset_identity'], simulation)
        and simulation["authoritative"] is False
        and simulation["evidence_status"] == "TECHNICAL_VALIDATION_ONLY"
        and simulation["result_hash"]
        == identity_hash(
            {k: v for k, v in simulation.items() if k != "result_hash"}, name="perpetual_simulation_result"
        ),
        "GOLD_STATISTICS_SIMULATION",
    )
    return simulation


@ledger_decimal_context()
def _series(simulation, sample, settlements, *, reviewed=False, streamed=False, grid_budget=None,
            evidence_kind='FORWARD'):
    require(evidence_kind in ('FORWARD', 'BACKTEST'), 'GOLD_STATISTICS_EVIDENCE_KIND')
    history = receipt_available_history_stream(simulation, settlements,
        maximum_points=100_000_000, maximum_events=100_000_000) if streamed else receipt_available_history(simulation, settlements)
    from quantos.streaming_identity import json_parts
    def observations():
        return history.items() if streamed else iter(history)

    claims = dict(
        full_research_risk_9_of_9=simulation["full_research_risk_9_of_9"],
        risk_history_ledger_replayed=simulation["risk_history_source"] == "LEDGER_EVENT_REPLAY",
        deployment_equivalent_risk=None,
    )
    metrics, risk, limitations = projection._native_metrics(
        simulation, sample, claims, evidence_kind, statistical_history=history, allow_uncovered_sample=True,
        maximum_history_points=100_000_000 if streamed else None, statistical_grid_budget=grid_budget
    )
    covered_sample = metrics["statistics_status"] != "INSUFFICIENT_SAMPLE_COVERAGE"
    grid = fixed_frequency_equity(observations(), **sample, budget=grid_budget) if covered_sample else None
    if streamed:
        last = None
        for last in observations():
            pass
        available_digest = hashlib.sha256()
        for raw in json_parts(history):
            available_digest.update(raw)
        available_hash = available_digest.hexdigest()
    else:
        last = history[-1]
        available_hash = native.digest(history)
    state = simulation["ending_state"]
    covered = (
        covered_sample
        and last["timestamp"] <= sample["end_ms"]
        and not state["positions"]
        and not state["pending_initial_margin_execution_prices"]
    )
    reasons = {
        "NO_INDEPENDENT_OOS_PROOF",
        "ACCOUNT_COSTS_NOT_CALIBRATED",
        "LOCAL_CLOCK_NOT_INDEPENDENTLY_VERIFIED",
    }
    if not reviewed:
        reasons.add("STATISTICAL_REVIEW_NOT_IMPLEMENTED")
    if metrics["statistics_status"] != "DIAGNOSTIC_ONLY":
        reasons.add(metrics["statistics_status"])
    if not covered:
        reasons.add("FULL_RUN_TERMINAL_NOT_COVERED")
    value = dict(
        status="INSUFFICIENT",
        reasons=sorted(reasons),
        native_result_sha256=simulation["result_hash"],
        available_history_sha256=available_hash,
        grid=grid,
        metrics=metrics,
        full_run_covered=covered,
    )
    return value, risk, limitations | reasons


def project_gold_trial(run, trial, *, reports=None, include_execution_risk=False, model_context=None):
    """Independent consumer reads the same declared original candidate/account."""
    report, document = _report(run, reports=reports)
    sample = {k: run["plan"]["forward_design"]["metrics"][k] for k in ("start_ms", "end_ms", "frequency_ms")}
    streamed = run["plan"]["schema"] in native.CHUNKED_GOLD_SCHEMAS
    dataset = _plain_dataset(report["dataset_identity"]) if streamed else report["dataset_identity"]
    simulation = _simulation(report, trial["candidate_id"], streamed=streamed)
    settlements = funding_settlements(document, dataset,
        source_parts=getattr(reports, 'source_inputs', {}).get(run['plan']['invocation_id']))
    value, risk, reasons = _series(
        simulation, sample, settlements, streamed=streamed,
        grid_budget=run['plan']['forward_design']['statistical_policy'].get('computation_budget')
    )
    if run['plan']['forward_design']['schema'] in FULL_REVIEW_SCHEMAS:
        reasons.discard('STATISTICAL_REVIEW_NOT_IMPLEMENTED')
        reasons.add('STATISTICAL_REVIEW_REQUIRES_COMPLETE_PROJECT_CUT')
    result = value["native_result_sha256"], value["metrics"], risk, reasons
    if include_execution_risk:
        from services.validation.gold_execution_risk_review import review_execution_risk
        selected = (report['portfolio'] if trial['candidate_id'] == 'net_portfolio' else
                    next(row for row in report['trials'] if row['candidate'] == trial['candidate_id']))
        diagnostic = review_execution_risk(simulation, selected['loss_control'],
            registered_policy=run['plan']['forward_design']['risk_policy'], streamed=streamed,
            maximum_points=100_000_000 if streamed else 100_000,
            maximum_events=100_000_000 if streamed else 100_000)
        from services.validation.gold_account_risk_inputs import build_account_risk_inputs, currency_from_dataset
        account_inputs = build_account_risk_inputs(simulation, sample=sample, settlements=settlements,
            account_currency=currency_from_dataset(dataset), streamed=streamed,
            maximum_points=100_000_000 if streamed else 100_000,
            maximum_events=100_000_000 if streamed else 100_000,
            grid_budget=run['plan']['forward_design']['statistical_policy'].get('computation_budget'),
            research_risk_policy=run['plan']['forward_design']['risk_policy'].get('research_risk_policy'),
            portfolio=selected if trial['candidate_id'] == 'net_portfolio' else None,
            market_state_policy=run['plan']['forward_design']['risk_policy'].get('market_state_policy'),
            market_bars=report.get('market_bars'), market_history_policy=report.get('market_history_policy'),
            capacity_policy=run['plan']['forward_design']['risk_policy'].get('capacity_policy'),
            capacity_history=report.get('capacity_history'), capacity_history_policy=report.get('capacity_history_policy'))
        diagnostic.update(schema='QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V8'
                          if account_inputs.capacity_inputs is not None else 'QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V7'
                          if account_inputs.market_states is not None else 'QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V6'
                          if account_inputs.net_attribution is not None else 'QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V5'
                          if 'risk_policy' in account_inputs.projection else 'QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V4'
                          if account_inputs.position_history is not None else 'QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V3',
                          account_risk_inputs=account_inputs.projection)
        risk_port = getattr(reports, 'review_gold_risk', None)
        if 'risk_policy' in account_inputs.projection and model_context is not None:
            from dataclasses import replace
            from services.validation.gold_model_risk_inputs import from_verified_statistics
            require(model_context['subject']['invocation_id'] == run['plan']['invocation_id']
                and all(model_context['subject'][k] == trial[k]
                        for k in ('native_trial_id', 'config_hash')), 'MODEL_SUBJECT')
            causality_policy=run['plan']['forward_design']['risk_policy'].get('causality_policy')
            causality_review=None
            causality_port=getattr(reports,'review_gold_causality',None)
            if causality_policy is not None and callable(causality_port):
                rows=selected['target_trace'] if trial['candidate_id']=='net_portfolio' else selected['signal_observations']
                causality_review=causality_port(bars=report['market_bars'],decision_rows=rows,
                    candidate_id=trial['candidate_id'],policy=causality_policy,subject=model_context['subject'],
                    native_result_hash=simulation['result_hash'],market_history_policy=report.get('market_history_policy'))
            model_inputs = from_verified_statistics(**model_context, candidate_id=trial['candidate_id'],
                native_result_hash=simulation['result_hash'],causality_policy=causality_policy,causality_review=causality_review)
            account_inputs.projection['model_inputs'] = model_inputs.projection
            account_inputs.projection['schema'] = 'QUANTOS_GOLD_ACCOUNT_RISK_INPUTS_V7'
            account_inputs.projection['source_groups']['alpha_metadata'] = dict(status='PARTIAL_ORIGINAL_FACTS',
                count=len(model_inputs.projection['known_fields']),
                value_sha256=model_inputs.projection['projection_sha256'])
            account_inputs.projection['limitations'] = sorted(set(account_inputs.projection['limitations'])
                | set(model_inputs.projection['limitations']))
            diagnostic['limitations'].extend(model_inputs.projection['limitations'])
            account_inputs = replace(account_inputs, model_inputs=model_inputs)
        if 'risk_policy' in account_inputs.projection and callable(risk_port):
            replay = risk_port(account_inputs)
            diagnostic.update(schema='QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V9', nine_engine_replay=replay,
                status='REJECTED' if replay['status'] == 'REJECTED' else diagnostic['status'],
                full_research_risk_9_of_9=replay['full_research_risk_9_of_9'])
            diagnostic['limitations'].remove('NINE_ENGINE_RISK_REPLAY_NOT_IMPLEMENTED')
            diagnostic['limitations'].extend(replay['limitations'])
            if not replay['engine_replay_completed']:
                diagnostic['limitations'].append('NINE_ENGINE_RISK_INPUTS_INCOMPLETE')
            diagnostic['limitations'] = sorted(set(diagnostic['limitations']))
            if account_inputs.model_inputs is not None:
                diagnostic['schema'] = 'QUANTOS_GOLD_EXECUTION_RISK_REVIEW_V10'
        return (*result, diagnostic)
    return result


@ledger_decimal_context()
def oos_account_material(simulation, baseline, *, sample, settlements, snapshot_as_of_ms, streamed,
                         evaluation_policy=None):
    """Read the declared holdout grid from continuous original cash accounts.

    Optional preregistered economics do not authenticate an unseen boundary or
    grant strategy qualification. The owning reader verifies source and result.
    Whole-run cash and window returns remain separately labelled.
    """
    from quantos.contracts.decimal_arithmetic import canonical_decimal
    from quantos.contracts.research_evaluation_plan import GOLD_OOS_MATERIAL_METHOD
    require(type(sample) is dict and set(sample) == {'start_ms', 'end_ms', 'frequency_ms'},
            'OOS_SAMPLE_FIELDS')
    start, end, frequency = (_time(sample[key]) for key in ('start_ms', 'end_ms', 'frequency_ms'))
    require(frequency > 0 and start <= end and (end - start) % frequency == 0, 'OOS_SAMPLE_GRID')
    require(end <= _time(snapshot_as_of_ms), 'OOS_FUTURE_SNAPSHOT')
    require(type(streamed) is bool, 'OOS_STREAM_MODE')
    summaries, grids = [], []
    for account in (simulation, baseline):
        history = receipt_available_history_stream(account, settlements,
            maximum_points=100_000_000, maximum_events=100_000_000) if streamed else receipt_available_history(account, settlements)
        last = None
        for last in history.items() if streamed else history:
            pass
        require(last is not None and last['timestamp'] <= snapshot_as_of_ms, 'OOS_FUTURE_ORIGINAL')
        value, _, _ = _series(account, sample, settlements, streamed=streamed, reviewed=True,
                              evidence_kind='BACKTEST')
        grid = value['grid']
        grids.append(grid)
        window = None
        if grid is not None:
            points = grid['points']
            initial, ending = (Decimal(point['equity']) for point in (points[0], points[-1]))
            window = dict(initial_equity=points[0]['equity'], ending_equity=points[-1]['equity'],
                net_equity_change=canonical_decimal(ending - initial),
                net_return=None if initial <= 0 else canonical_decimal(ending / initial - 1),
                point_count=len(points), sample_count=len(points) - 1,
                annualized_sharpe=grid['annualized_sharpe'], statistics_status=grid['statistics_status'])
        summaries.append(dict(native_result_hash=value['native_result_sha256'],
            available_history_sha256=value['available_history_sha256'],
            grid_sha256=None if grid is None else native.digest(grid), window=window,
            full_run_cash_identity={key: value['metrics'][key] for key in
                ('initial_equity', 'ending_equity', 'net_equity_change', 'fees', 'funding', 'reconciliation_residual')}))
    candidate, cash = summaries
    require(candidate['full_run_cash_identity']['initial_equity'] == cash['full_run_cash_identity']['initial_equity'],
            'OOS_BASELINE_ACCOUNT')
    late = sum(row['settlement_at_ms'] <= end < row['available_at_ms'] for row in settlements)
    complete = all(row['window'] is not None for row in summaries) and late == 0
    limitations = ['OOS_LOCK_AND_ACCESS_NOT_INDEPENDENTLY_VERIFIED',
        'OOS_MATERIAL_EXTRACTION_IS_NOT_A_STATISTICAL_DECISION', 'ACCOUNT_COSTS_NOT_CALIBRATED',
        'CONTINUOUS_ORIGINAL_ACCOUNT_NO_WINDOW_RESET']
    if late:
        limitations.append('OOS_REQUIRED_CASH_RECEIPTS_AFTER_WINDOW')
    if any(row['window'] is None for row in summaries):
        limitations.append('OOS_ORIGINAL_WINDOW_COVERAGE_INCOMPLETE')
    returns = [None if row['window'] is None else row['window']['net_return'] for row in summaries]
    value = dict(schema='QUANTOS_GOLD_OOS_ORIGINAL_MATERIAL_V1', method=GOLD_OOS_MATERIAL_METHOD,
        material_status='AVAILABLE' if complete else 'INSUFFICIENT', sample=dict(sample),
        snapshot_as_of_ms=snapshot_as_of_ms, candidate=candidate, benchmark=cash,
        benchmark_excess_net_return=None if None in returns else canonical_decimal(Decimal(returns[0]) - Decimal(returns[1])),
        late_receipts_at_window_end=late, statistical_decision='NOT_EVALUATED',
        independent_oos_verified=False, economic_authority=False, submission_enabled=False,
        scope='DECLARED_OOS_ORIGINAL_MATERIAL_NOT_INDEPENDENT_LOCK_OR_STATISTICAL_PASS',
        limitations=sorted(limitations))
    if evaluation_policy is not None:
        from services.validation.gold_oos_economic_review import review_grids
        value.update(schema='QUANTOS_GOLD_OOS_ORIGINAL_MATERIAL_V2',
            economic_review=review_grids(evaluation_policy, sample=sample,
                candidate_grid=grids[0], benchmark_grid=grids[1], coverage_complete=complete))
    value['material_sha256'] = native.digest(value)
    return value


def project_gold_oos_material(run, trial, *, project_cut_sha256, snapshot_as_of_ms, reports=None):
    """Consume committed V2/V3 OOS methods and original accounts."""
    from quantos.contracts.research_evaluation_plan import (
        GOLD_REANALYSIS_OOS_SCHEMAS, validate_core, core_sha256, core_status)
    from services.validation.project_research_evidence_authority import _original
    from quantos.contracts.project_chunked_results import report_for
    from quantos.contracts.gold_native_source import bind_plan
    plan = run['plan']
    core = validate_core(plan['evaluation_core'])
    require(plan['schema'] in native.EVALUATED_SOURCE_REANALYSIS_SCHEMAS
        and core['schema'] in GOLD_REANALYSIS_OOS_SCHEMAS, 'OOS_PREDECLARED_METHOD')
    require(trial['candidate_id'] == core['selection']['candidate_id'], 'OOS_PREDECLARED_CANDIDATE')
    if run['partial'] or not {'archive/report.json', 'archive/manifest.json'} <= set(run['originals']):
        value = dict(schema='QUANTOS_GOLD_OOS_ORIGINAL_MATERIAL_V1',
            method=core['oos_material_method'], material_status='INSUFFICIENT',
            sample=dict(core['splits']['locked_oos'], frequency_ms=core['sample']['frequency_ms']),
            snapshot_as_of_ms=snapshot_as_of_ms, candidate=None, benchmark=None, currency=None,
            benchmark_excess_net_return=None, late_receipts_at_window_end=None,
            statistical_decision='NOT_EVALUATED',
            scope='DECLARED_OOS_ORIGINAL_MATERIAL_NOT_INDEPENDENT_LOCK_OR_STATISTICAL_PASS',
            limitations=['OOS_NATIVE_ATTEMPT_NOT_COMPLETE', 'OOS_LOCK_AND_ACCESS_NOT_INDEPENDENTLY_VERIFIED'],
            independent_oos_verified=False, economic_authority=False, submission_enabled=False)
        if 'oos_evaluation_policy' in core:
            from services.validation.gold_oos_economic_review import review_grids
            value.update(schema='QUANTOS_GOLD_OOS_ORIGINAL_MATERIAL_V2',
                economic_review=review_grids(core['oos_evaluation_policy'], sample=value['sample'],
                    candidate_grid=None, benchmark_grid=None, coverage_complete=False))
    else:
        document = _original(run, 'input.json')
        bind_plan(plan, document)
        report = report_for(run, reports)
        dataset = _plain_dataset(report['dataset_identity'])
        settlements = funding_settlements(document, dataset,
            source_parts=getattr(reports, 'source_inputs', {}).get(plan['invocation_id']))
        value = oos_account_material(_simulation(report, trial['candidate_id'], streamed=True),
            _simulation(report, None, streamed=True), sample=dict(core['splits']['locked_oos'],
                frequency_ms=core['sample']['frequency_ms']), settlements=settlements,
            snapshot_as_of_ms=snapshot_as_of_ms, streamed=True,
            evaluation_policy=core.get('oos_evaluation_policy'))
        del value['material_sha256']
        from services.validation.gold_account_risk_inputs import currency_from_dataset
        value['currency'] = currency_from_dataset(dataset)
        if value['currency'] is None:
            value['material_status'] = 'INSUFFICIENT'
            value['limitations'].append('OOS_ACCOUNT_CURRENCY_UNAVAILABLE')
    value.update(invocation_id=plan['invocation_id'], native_trial_id=trial['native_trial_id'],
        config_hash=trial['config_hash'], candidate_id=trial['candidate_id'],
        project_cut_sha256=project_cut_sha256, evaluation_core_sha256=core_sha256(core),
        input_sha256=plan['input_sha256'], declared_benchmark=dict(core['benchmark']),
        oos_access_declaration=dict(core['oos_access']), oos_status=core_status(core)['oos_status'])
    value['limitations'] = sorted(set(value['limitations']))
    value['material_sha256'] = native.digest(value)
    return value


def project_gold_reanalysis_trial(run, trial, *, reports=None):
    """Declared train/validation statistics from originals; never OOS proof."""
    from services.validation.project_research_evidence_authority import _original
    from quantos.contracts.project_chunked_results import report_for
    from quantos.contracts.gold_native_source import bind_plan
    from quantos.contracts.research_evaluation_plan import statistical_sample
    plan = run['plan']
    require(plan['schema'] in native.EVALUATED_SOURCE_REANALYSIS_SCHEMAS, 'GOLD_REANALYSIS_EVALUATION')
    document = _original(run, 'input.json')
    bind_plan(plan, document)
    sample = statistical_sample(plan['evaluation_core'])
    report = report_for(run, reports)
    dataset = _plain_dataset(report['dataset_identity'])
    simulation = _simulation(report, trial['candidate_id'], streamed=True)
    value, risk, reasons = _series(simulation, sample,
        funding_settlements(document, dataset,
            source_parts=getattr(reports, 'source_inputs', {}).get(plan['invocation_id'])),
        streamed=True, evidence_kind='BACKTEST')
    return value['native_result_sha256'], value['metrics'], risk, reasons, None


def project_gold_statistics(*, project_cut, invocation_id, reports=None):
    from services.validation.project_research_population import validate_project_cut
    from quantos.contracts import gold_native_statistics as contract

    cut = validate_project_cut(project_cut)
    from quantos.contracts.project_chunked_results import full_report_population
    full_report_population(cut, reports)
    run = cut["runs"][invocation_id]
    plan = run["plan"]
    reviewed = plan["forward_design"]["schema"] in FULL_REVIEW_SCHEMAS
    require(plan["schema"] in native.FORWARD_GOLD_SCHEMAS, "GOLD_STATISTICS_DECLARATION")
    sample = {k: plan["forward_design"]["metrics"][k] for k in ("start_ms", "end_ms", "frequency_ms")}
    selected = [a for a in cut["attempts"] if a["invocation_id"] == invocation_id]
    complete = (
        not run["partial"] and len(selected) == len(plan["trials"]) and all(a["status"] == "PASSED" for a in selected)
    )
    streamed = plan["schema"] in native.CHUNKED_GOLD_SCHEMAS
    report, document = _report(run, reports=reports) if complete else (None, None)
    dataset = _plain_dataset(report["dataset_identity"]) if complete and streamed else (report["dataset_identity"] if complete else None)
    settlements = funding_settlements(document, dataset,
        source_parts=getattr(reports, 'source_inputs', {}).get(invocation_id)) if complete else []
    attempts = []
    for trial in plan["trials"]:
        value = dict(
            status="INSUFFICIENT",
            reasons=["NATIVE_ATTEMPT_NOT_COMPLETE"],
            native_result_sha256=None,
            available_history_sha256=None,
            grid=None,
            metrics=None,
            full_run_covered=False,
        )
        if complete:
            value, _, _ = _series(_simulation(report, trial["candidate_id"], streamed=streamed), sample, settlements,
                                  reviewed=reviewed, streamed=streamed,
                                  grid_budget=plan['forward_design']['statistical_policy'].get('computation_budget'))
        attempts.append(
            dict(
                candidate_id=trial["candidate_id"],
                native_trial_id=trial["native_trial_id"],
                config_hash=trial["config_hash"],
                **value,
            )
        )
    baseline = None
    if complete:
        baseline, _, _ = _series(_simulation(report, None, streamed=streamed), sample, settlements,
                                 reviewed=reviewed, streamed=streamed,
                                 grid_budget=plan['forward_design']['statistical_policy'].get('computation_budget'))
        require(
            all(a["metrics"]["initial_equity"] == baseline["metrics"]["initial_equity"] for a in attempts),
            "GOLD_STATISTICS_BASELINE_ACCOUNT",
        )
        baseline["id"] = plan["forward_design"]["benchmark_policy"]["id"]
    value = dict(
        schema=contract.SCHEMA,
        status="TECHNICAL_VALIDATION_ONLY",
        evidence_kind="FORWARD",
        method=METHOD,
        native_invocation_id=invocation_id,
        plan_sha256=native.digest(plan),
        input_sha256=plan["input_sha256"],
        source_identity_sha256=native.digest(plan["source_identity"]),
        declaration_sha256=native.digest(plan["forward_design"]),
        dataset_manifest_sha256=native.digest(dataset) if complete else None,
        original_record_sha256=run["originals"].get("archive/report.json", {}).get("sha256"),
        project_cut_sha256=cut["cut_sha256"],
        sample=sample,
        attempts=attempts,
        benchmark=baseline,
        population=dict(
            attempt_count=cut["attempt_count"],
            unique_definition_count=cut["unique_definition_count"],
            coverage=cut["population_coverage"],
            all_attempts=copy.deepcopy(cut["attempts"]),
            unfinished_runs=copy.deepcopy(cut["unfinished_runs"]),
        ),
        statistical_review="NOT_IMPLEMENTED",
        limitations=[
            "FORWARD_NOT_HISTORICAL_OOS",
            "NO_RESEARCH_OR_EXECUTION_AUTHORITY",
            "FIXED_GRID_IS_NOT_INTRAPERIOD_RISK_EVIDENCE",
            "EMBEDDED_ORIGINAL_BASELINE_IS_NOT_A_SEARCH_TRIAL",
            "NEW_ADMISSION_REQUIRES_FRESH_COMPLETE_PROJECT_CUT",
        ],
        economic_authority=False,
        promotion_approved=False,
        submission_enabled=False,
    )
    if reviewed:
        from services.validation import gold_project_statistics
        cohort = gold_project_statistics.project_cohort(project_cut=cut, invocation_id=invocation_id, reports=reports)
        value.update(schema=contract.REVIEW_SCHEMA, statistical_review="COMPLETED",
            review=gold_project_statistics.review_project_statistics(project_cut=cut,
                policy=plan["forward_design"], cohort=cohort, as_of_ms=cohort["frozen_at_ms"], reports=reports))
    value["projection_sha256"] = native.digest(value)
    return contract.validate(value)
