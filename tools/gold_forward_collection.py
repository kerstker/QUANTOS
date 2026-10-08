"""Declare future Gold research, retain public originals, then use the original queue.

Collection does not authorize a strategy. Existing observations cannot be adopted
as this declaration; failed collection and native attempts are retained.
"""
import argparse
import asyncio
import copy
import json
import shutil
import time
from pathlib import Path

from quantos.contracts import gold_forward_evaluation_design as design
from quantos.contracts import gold_collection_budget as collection
from quantos.contracts import gold_native_source as source_shape
from quantos.contracts import gold_chunked_result as result_shape
from quantos.contracts import registered_research_run as native
from quantos.contracts.json_integrity import load_json_without_duplicate_keys as decode
from quantos.runtime_paths import runtime_state_root
from tools import registered_research_run as original
from tools import gold_public_session as public
from tools.gold_bar_capture import capture
from tools.perpetual_replay_research import source_identity

LEGACY_SCHEMA = 'GOLD_FORWARD_COLLECTION_PLAN_V1'
SCHEMA = 'GOLD_FORWARD_COLLECTION_PLAN_V2'
FIELDS = frozenset('schema source forward_design seconds interval_seconds segment_samples '
    'collection_budget result_storage history_storage source_storage '
    'economic_authority submission_enabled plan_sha256'.split())


def _require(ok, reason):
    if not ok:
        raise ValueError('GOLD_FORWARD_COLLECTION_' + reason)


def _now():
    value = time.time_ns() // 1_000_000
    _require(type(value) is int and 0 <= value < 2**63, 'CLOCK')
    return value


def _validate(value):
    _require(type(value) is dict and set(value) == FIELDS and value['schema'] in (LEGACY_SCHEMA, SCHEMA), 'PLAN')
    native.canonical(value, maximum=native.MAX_PLAN_BYTES)
    _require(value['economic_authority'] is False and value['submission_enabled'] is False, 'AUTHORITY')
    _require(native.digest({k: v for k, v in value.items() if k != 'plan_sha256'}) == value['plan_sha256'], 'PLAN_CHANGED')
    declaration = design.validate(value['forward_design'])
    if value['schema'] == SCHEMA:
        _require('settlement_window_ms' in declaration['configuration'], 'FUNDING_WINDOW_REQUIRED')
    _require(declaration['schema'] in design.LONG_REVIEW_SCHEMAS
        and declaration['source_identity_sha256'] == native.digest(value['source'])
        and value['source'] == source_identity(), 'SOURCE')
    _require(declaration['selection']['method'] == 'PREDECLARED_CANDIDATE', 'PREDECLARED_SELECTION_REQUIRED')
    window = declaration['window']
    _require(window['observation_start_ms'] - declaration['declared_at_ms'] >= 180_000
        and value['seconds'] == len(window['closed_bars']) * design.INTERVAL // 1000 + 40
        and type(value['seconds']) is int and value['interval_seconds'] == 10
        and type(value['interval_seconds']) is int and value['segment_samples'] == 120
        and type(value['segment_samples']) is int, 'WINDOW')
    budget = collection.for_duration(value['collection_budget'], value['seconds'] + public.BAR_MARGIN_SECONDS)
    source_shape.storage(value['source_storage'])
    result_shape.storage(value['result_storage'])
    native.validate_history_storage(value['history_storage'])
    replay = value['source_storage'].get('replay_budget')
    _require(value['source_storage']['schema'] == source_shape.BUDGETED_STORAGE_SCHEMA
        and replay is not None
        and replay['maximum_environment_segments'] >= budget['maximum_quote_segments']
        and replay['maximum_elapsed_ms'] >= (value['seconds'] + public.BAR_MARGIN_SECONDS) * 1000,
        'REPLAY_BUDGET')
    # One shared sample between segments; resource limits remain the original caps.
    nominal_segments = (value['seconds'] + public.QUOTE_MARGIN_SECONDS + 1189) // 1190 + 1
    _require(nominal_segments <= budget['maximum_quote_segments'], 'SEGMENT_BUDGET')
    return value


def prepare(directory, *, program_id, attempt, first_close_ms, window_bars, configuration,
            selected_candidate, proposal_sha256,
            collection_budget, result_storage, history_storage, source_storage, research_risk_policy=None):
    _require(type(configuration) is dict and 'settlement_window_ms' in configuration, 'FUNDING_WINDOW_REQUIRED')
    source = source_identity()
    declaration = design.declare(program_id=program_id, attempt=attempt, source_identity=source,
        configuration=configuration, first_close_ms=first_close_ms, window_bars=window_bars,
        declared_at_ms=_now(), long_window=True,
        selected_candidate=selected_candidate, proposal_sha256=proposal_sha256,
        research_risk_policy=research_risk_policy)
    value = dict(schema=SCHEMA, source=source, forward_design=declaration,
        seconds=window_bars * design.INTERVAL // 1000 + 40, interval_seconds=10, segment_samples=120,
        collection_budget=copy.deepcopy(collection_budget), result_storage=copy.deepcopy(result_storage),
        history_storage=copy.deepcopy(history_storage), source_storage=copy.deepcopy(source_storage),
        economic_authority=False, submission_enabled=False)
    value['plan_sha256'] = native.digest(value)
    _validate(value)
    root = Path(directory).absolute()
    _require(root.resolve() == root, 'PATH')
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    original._put(root / 'plan.json', value)
    return value


def read_plan(directory):
    root = original._directory(Path(directory).absolute())
    return _validate(original._load(root / 'plan.json', maximum=native.MAX_PLAN_BYTES))


async def collect(directory):
    root = Path(directory).absolute()
    plan = read_plan(root)
    window = plan['forward_design']['window']
    for name in ('history', 'pair', 'collection.json', 'collection-result.json'):
        _require(not (root / name).exists(), 'COLLECTION_ALREADY_STARTED')
    reserved = (plan['collection_budget']['maximum_quote_bytes'] + plan['collection_budget']['maximum_bar_bytes']
        + 3 * plan['source_storage']['maximum_bytes'] + plan['source_storage']['maximum_preparation_bytes']
        + plan['history_storage']['maximum_bytes'] + plan['result_storage']['maximum_bytes']
        + plan['result_storage']['maximum_index_bytes'] + 128 * 1024**2)
    _require(shutil.disk_usage(root).free >= reserved, 'DISK_BUDGET')
    while True:
        now = _now()
        _require(now >= plan['forward_design']['declared_at_ms'], 'CLOCK_ROLLBACK')
        if now >= window['observation_start_ms']:
            break
        await asyncio.sleep(min(1, (window['observation_start_ms'] - now) / 1000))
    _require(now < window['closed_bars'][0] and read_plan(root) == plan, 'MISSED_WINDOW')
    await asyncio.to_thread(capture, root / 'history')
    started = _now()
    _require(window['observation_start_ms'] <= started < window['closed_bars'][0]
        and read_plan(root) == plan, 'MISSED_WINDOW')
    receipt_raw = public._read(root / 'history', 'receipt.json')
    receipt = decode(receipt_raw)
    _require(window['observation_start_ms'] <= receipt['requested_at_ms']
        <= receipt['received_at_ms'] <= started, 'HISTORY_TIME')
    remaining = (window['closed_bars'][-1] + 40_000 - started + 999) // 1000
    record = dict(plan_sha256=plan['plan_sha256'], started_at_ms=started, pair_seconds=remaining,
        history_receipt_sha256=native.digest(receipt_raw))
    original._put(root / 'collection.json', record)
    result = await public.run(root / 'history', root / 'pair', seconds=remaining,
        interval_seconds=plan['interval_seconds'], segment_samples=plan['segment_samples'],
        collection_budget=plan['collection_budget'], quote_transport='POOLED_RETAINED_GAPS_V1',
        late_open_policy=public.LATE_OPEN_POLICY)
    _require(read_plan(root) == plan, 'PLAN_CHANGED')
    terminal = dict(plan_sha256=plan['plan_sha256'], paired_status=result['status'],
        paired_summary_sha256=native.digest(public._read(root / 'pair', 'summary.json')),
        economic_authority=False, submission_enabled=False)
    original._put(root / 'collection-result.json', terminal)
    return terminal


def _paired(root, plan):
    record = original._load(root / 'collection.json')
    terminal = original._load(root / 'collection-result.json')
    pair = decode(public._read(root / 'pair', 'plan.json'))
    window = plan['forward_design']['window']
    started = record['started_at_ms']
    _require(record['plan_sha256'] == terminal['plan_sha256'] == plan['plan_sha256']
        and terminal['paired_status'] == 'RAW_PAIRED_SESSION_COLLECTED'
        and terminal['economic_authority'] is False and terminal['submission_enabled'] is False
        and terminal['paired_summary_sha256'] == native.digest(public._read(root / 'pair', 'summary.json'))
        and window['observation_start_ms'] <= started < window['closed_bars'][0]
        and record['pair_seconds'] == (window['closed_bars'][-1]+40_000-started+999)//1000
        and pair['seconds'] == record['pair_seconds']
        and pair['collection_budget'] == plan['collection_budget'] and pair['source'] == plan['source']
        and pair.get('quote_transport') == public.quote_transport_profile('POOLED_RETAINED_GAPS_V1')
        and pair.get('late_open_policy') == public.LATE_OPEN_POLICY
        and record['history_receipt_sha256'] == native.digest(public._read(root / 'history', 'receipt.json')),
        'COLLECTION_BINDING')
    _require(public._read(root / 'history', 'receipt.json') == public._read(root / 'pair', 'history-receipt.json')
        and public._read(root / 'history', 'response.json') == public._read(root / 'pair', 'history-response.json'),
        'HISTORY_CHANGED')
    reviewed = public.review(root / 'pair')
    _require(reviewed['source'] == plan['source'], 'SOURCE')
    return decode(public._read(root / 'pair/quotes', 'summary.json'))['segment_count']


def finish(directory, *, recover=False):
    """Cold-verify the whole capture and use original registration, queue and review."""
    from services.research.run_registry.registry import RunRegistry
    from services.research.run_registry.store import ResearchRunStore
    from services.research_runner.runner import JobQueue
    from tools import gold_chunked_source as source, registered_gold_chunked_job as job
    from tools import registered_research_job as workspace
    from tools.registered_gold_collection import invocation_id

    _require(type(recover) is bool, 'RECOVERY_MODE')
    root = Path(directory).absolute()
    plan = read_plan(root)
    count = _paired(root, plan)
    config = plan['forward_design']['configuration']
    original_input = root / 'source-input'
    native_id = invocation_id(plan['forward_design']['attempt'])
    if recover:
        project, _ = workspace.workspace_paths()
        native_plan = native.validate_plan(original._load(project / 'runs' / native_id / 'plan.json',
                                                         maximum=native.MAX_PLAN_BYTES))
        _require(native_plan['invocation_id'] == native_id
            and native_plan['schema'] == native.BUDGETED_SOURCE_GOLD_FORWARD_SCHEMA
            and native_plan['source_identity'] == plan['source']
            and native_plan['forward_design'] == plan['forward_design']
            and native_plan['search']['search_id'] == native_id
            and native_plan['search']['source'] == 'REGISTERED_GOLD_COLLECTOR_V1'
            and native_plan['search']['dataset_version'] == plan['plan_sha256']
            and all(native_plan[k] == plan[k] for k in ('result_storage', 'history_storage', 'source_storage')),
            'RECOVERY_PLAN_BINDING')
    else:
        _require(not original_input.exists(), 'SOURCE_ALREADY_STARTED')
        source.from_session(root / 'pair/quotes', root / 'pair/bars', original_input,
            segment_count=count, release_identity=plan['source'],
            **{k: config[k] for k in ('quantity', 'initial_collateral', 'fee_rate')},
            maximum_bytes=plan['source_storage']['maximum_bytes'],
            settlement_window_ms=config.get('settlement_window_ms'), portfolio_plan=config.get('portfolio_plan'))
        _require(_paired(root, plan) == count, 'COLLECTION_CHANGED')
        native_plan = job.prepare_source_run(original_input,
            search_id=native_id, dataset_version=plan['plan_sha256'], registered_at_ms=_now(), invocation_id=native_id,
            result_storage=plan['result_storage'], history_storage=plan['history_storage'],
            source_storage=plan['source_storage'], forward_design=plan['forward_design'])
    registry = RunRegistry(ResearchRunStore(str(runtime_state_root() / 'data/cache/research_runs.db')))
    queue = JobQueue(registry)
    kind = 'registered_gold_chunked_recover' if recover else 'registered_gold_chunked'
    research_id = queue.submit(kind, {'native_invocation_id': native_id}, _now(),
        invocation_id='gold-forward:' + native_id + (':recover' if recover else ':research'))
    queue.join()
    research = registry.get(research_id)
    _require(research.status == 'done', 'RESEARCH_' + research.status.upper() + ':' + str(research.error))
    review_id = queue.submit('registered_review_request', {'native_invocation_id': native_id}, _now(),
        invocation_id='gold-forward:' + native_id + ':review')
    queue.join()
    review = registry.get(review_id)
    _require(review.status == 'done', 'REVIEW_' + review.status.upper() + ':' + str(review.error))
    evidence = json.loads(review.metrics['registered_review_report_json'])
    _require(read_plan(root) == plan and _paired(root, plan) == count, 'PLAN_CHANGED')
    result = dict(plan_sha256=plan['plan_sha256'], native_invocation_id=native_plan['invocation_id'],
        research_run_id=research_id, review_run_id=review_id, review_request_id=evidence['review_request_id'],
        statistical_decision=evidence['statistical_decision'], authentication=evidence['authentication'],
        qualification=evidence['qualification'], economic_authority=False, submission_enabled=False)
    original._put(root / ('recovery-result.json' if recover else 'research-result.json'), result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'collect', 'run', 'finish', 'recover'))
    parser.add_argument('--output', required=True)
    parser.add_argument('--request')
    args = parser.parse_args(argv)
    if args.command == 'prepare':
        if args.request is None:
            parser.error('--request is required for prepare')
        path = Path(args.request).absolute()
        result = prepare(args.output, **decode(original._read(path)))
    elif args.command == 'collect':
        result = asyncio.run(collect(args.output))
    elif args.command == 'run':
        collected = asyncio.run(collect(args.output))
        _require(collected['paired_status'] == 'RAW_PAIRED_SESSION_COLLECTED', 'COLLECTION_INCOMPLETE')
        result = finish(args.output)
    else:
        result = finish(args.output, recover=args.command == 'recover')
    print(json.dumps(result, sort_keys=True, allow_nan=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
