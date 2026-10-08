"""Finite public collection -> registered three-candidate research program.

Each round is a separate technical experiment, not a continuous trading account.
No credential, economic admission, resident service or execution is configured.
"""
import argparse
import asyncio
import hashlib
import json
import time
import ssl
import urllib.error
from threading import Event
from tools.gold_joint_capture import collect_joint

from quantos.runtime_paths import runtime_state_root
from services.research.gold_bar_candidates import INTERVAL_MS, CANDIDATES, CATALOG
from services.research.gold_bar_diagnostics import compact_diagnostic
from tools.ai_factory_collaboration import bounded_proposal
from tools.ai_factory_provider import propose
from services.research.run_registry.registry import RunRegistry
from services.research.run_registry.store import ResearchRunStore
from services.research_runner.runner import JobQueue
from tools.gold_bar_capture import capture, _write
from tools.gold_bar_journal import restore
from tools.gold_bar_observe import observe
from tools.perpetual_public_capture import capture_public, capture_gold_continuous, capture_gold_extended, quote_transport_profile
from tools.perpetual_replay_research import source_identity


def round_result(attempt, run):
    """Original job projection, shared with the read-only archive verifier."""
    return dict(attempt=attempt, run_id=run['run_id'], status=run['status'],
        result_hash=run['metrics']['result_hash'],
        report=json.loads(run['metrics']['gold_bar_report_json']))


def program_summary(completed, capture_failures, program_loss_stop):
    return dict(status='RESEARCH_PROGRAM_STOPPED_LOSS_LIMIT' if program_loss_stop else
        'RESEARCH_PROGRAM_INCOMPLETE' if capture_failures else 'RESEARCH_PROGRAM_COMPLETE',
        rounds=completed, economic_eligible=False, authoritative=False, submission_enabled=False,
        **({'loss_stop': program_loss_stop} if program_loss_stop else {}),
        **({'capture_failures': capture_failures} if capture_failures else {}))


def verify_retained_program(directory, *, requests_root, results_root, jobs, original_source):
    """Verify original program relationships without submitting or resuming work.

    Jobs must come from the original native read-only run-registry parser. The
    caller supplies the independently checked source and file custody. Receipt
    checks retain their historical status; this grants no current qualification.
    """
    from pathlib import Path
    from collections import Counter
    from quantos.contracts.research_evidence_json import canonical
    from quantos.contracts.json_integrity import load_json_without_duplicate_keys as decode
    from tools.gold_cost_review import _read, _encode
    from tools import gold_bar_research as research, gold_bar_experiment as experiment
    from tools.ai_factory_collaboration import verify_collaboration

    def check(ok, reason):
        if not ok:
            raise ValueError('GOLD_BAR_RETAINED_PROGRAM_' + reason)

    def same(left, right):
        return canonical(left) == canonical(right)

    directory = Path(directory); requests_root = Path(requests_root); results_root = Path(results_root)
    plan = decode(_read(directory, 'plan.json'))
    summary = decode(_read(directory, 'summary.json')) if (directory/'summary.json').exists() else None
    summary_rounds = summary.get('rounds', []) if type(summary) is dict else []
    check(type(summary_rounds) is list and all(type(row) is dict and type(row.get('attempt')) is int
          for row in summary_rounds) and len({row['attempt'] for row in summary_rounds}) == len(summary_rounds), 'SUMMARY_ROUNDS')
    check(plan.get('schema') == 'GOLD_BAR_PROGRAM_V1' and same(plan.get('source'), original_source)
          and plan.get('submission_enabled') is False, 'PLAN')
    check('register_original_trials' not in plan or plan['register_original_trials'] is True, 'REGISTRATION_MODE')
    prospective = 'forward_evaluation_design' in plan
    check(not prospective or (plan['forward_evaluation_design'] in ('PROSPECTIVE_V1', 'PROSPECTIVE_V2', 'PROSPECTIVE_V3')
          and plan.get('register_original_trials') is True), 'FORWARD_MODE')
    for key, low, high in (('program_id', 1, 1000), ('first_attempt', 1, 1000), ('rounds', 1, 8),
                           ('window_bars', 1, 4), ('first_close_ms', 1, 2**63-1)):
        check(type(plan.get(key)) is int and low <= plan[key] <= high, 'PLAN_NUMBER')
    check(plan['first_attempt'] + plan['rounds'] <= 1001
          and directory.name == str(plan['program_id']), 'IDENTITY')
    members = {'plan.json', 'summary.json', 'events.jsonl'}
    for index in range(1, plan['rounds'] + 1):
        members.update((f'window-plan-{index}.json', f'round-{index}.json',
                        f'hypothesis-{index}', f'portfolio-memory-{index}.json',
                        f"capture-failure-{plan['first_attempt']+index-1}.json"))
        if prospective:
            members.add(f'evaluation-design-{index}.json')
    check(set(p.name for p in directory.iterdir()) <= members, 'UNKNOWN_MEMBER')
    check(type(plan.get('ai_plan')) is bool and type(plan.get('continuous')) is bool
          and plan.get('minimum_lead_ms') == 180000
          and plan.get('window_schedule') == 'NEXT_UNOBSERVED_WINDOW_AFTER_REVIEW_V1', 'PLAN_WINDOW')
    events = []
    if (directory/'events.jsonl').exists():
        events = [decode(line) for line in _read(directory, 'events.jsonl').splitlines()]
    allowed = {'WINDOW_ADVANCED','USING_RETAINED_HYPOTHESIS','PROPOSING_HYPOTHESIS',
        'WAITING_CLOSED_BAR','COLLECTING','CAPTURE_FAILURE_RETAINED','RESEARCH_SUBMITTED',
        'RESEARCH_FAILED','RESEARCH_RECOVERY_SUBMITTED','REVIEW_SUBMITTED','REVIEW_FAILED',
        'ROUND_COMPLETE','PROGRAM_LOSS_STOP'}
    for event in events:
        check(type(event) is dict and event.get('status') in allowed
              and type(event.get('observed_at_ms')) is int and event['observed_at_ms'] >= 0
              and type(event.get('attempt')) is int
              and plan['first_attempt'] <= event['attempt'] < plan['first_attempt'] + plan['rounds']
              and event.get('submission_enabled') is False, 'EVENT')
    completed = []; failures = []; partial = []; previous_close = None; loss_stop = None
    consumed_jobs = set()

    def job(run_id, kind, attempt, suffix):
        row = jobs.get(run_id)
        check(type(row) is dict and row.get('kind') == kind and same(row.get('params'), {'attempt': attempt})
              and row.get('invocation_id') == f"gold-bar-program-{plan['program_id']}-{suffix}-{attempt}", 'JOB')
        consumed_jobs.add(run_id)
        return row

    for index in range(1, plan['rounds'] + 1):
        attempt = plan['first_attempt'] + index - 1
        window_path = directory/f'window-plan-{index}.json'
        round_path = directory/f'round-{index}.json'
        request = requests_root/str(attempt); output = results_root/str(attempt)
        if not window_path.exists():
            check(not round_path.exists() and not request.exists() and not output.exists(), 'WINDOW_MISSING')
            if loss_stop is None:
                partial.append(dict(attempt=attempt, reason='WINDOW_NOT_PLANNED'))
            continue
        check(loss_stop is None, 'WORK_AFTER_STOP')
        window = decode(_read(directory, window_path.name))
        planned = window.get('planned_at_ms')
        check(type(planned) is int and planned >= 0, 'WINDOW_TIME')
        nominal = plan['first_close_ms'] if previous_close is None else previous_close + INTERVAL_MS * plan['window_bars']
        close = max(nominal, prospective_close(planned) if prospective else next_close(planned))
        expected_window = dict(attempt=attempt, planned_at_ms=planned, nominal_close_ms=nominal,
            target_close_ms=close, minimum_lead_ms=180000,
            reason='PROCESSING_AND_MINIMUM_LEAD' if close != nominal else 'NOMINAL_CADENCE',
            account_mode=plan['account_mode'], submission_enabled=False)
        check(same(window, expected_window), 'WINDOW_BINDING'); previous_close = close
        selected_events = [row for row in events if row['attempt'] == attempt]
        for event in selected_events:
            if 'run_id' in event:
                kind, suffix = (('gold_bar_receipt','review') if event['status'] in ('REVIEW_SUBMITTED','REVIEW_FAILED')
                    else ('gold_bar_recover','recovery') if event['status'] == 'RESEARCH_RECOVERY_SUBMITTED'
                    else ('gold_bar_research','attempt'))
                if event['status'] == 'ROUND_COMPLETE' and any(e['status'] == 'RESEARCH_RECOVERY_SUBMITTED' for e in selected_events):
                    kind, suffix = 'gold_bar_recover', 'recovery'
                job(event['run_id'], kind, attempt, suffix)
        hypothesis = directory/f'hypothesis-{index}'
        proposal = verify_collaboration(hypothesis) if (hypothesis/'plan.json').exists() else None
        check(proposal is None or plan['ai_plan'], 'UNPLANNED_HYPOTHESIS')
        if index == 1 and 'initial_proposal' in plan:
            binding = plan['initial_proposal']
            check(proposal is not None and binding['proposal_hash'] == proposal['result_hash'], 'INITIAL_PROPOSAL')
            for name, sha in binding['files'].items():
                check(hashlib.sha256(_read(hypothesis, name)).hexdigest() == sha, 'INITIAL_PROPOSAL_FILES')
        forward_design = None
        if prospective:
            design_path = directory/f'evaluation-design-{index}.json'
            if design_path.exists():
                from quantos.contracts.gold_forward_evaluation_design import declare
                forward_design = decode(_read(directory, design_path.name))
                check(type(forward_design) is dict and type(forward_design.get('declared_at_ms')) is int
                      and forward_design['declared_at_ms'] >= planned, 'DECLARATION_TIME')
                check(same(forward_design, declare(program_id=plan['program_id'], attempt=attempt,
                    source_identity=original_source, configuration=_design_configuration(plan),
                    first_close_ms=close, window_bars=plan['window_bars'],
                    declared_at_ms=forward_design['declared_at_ms'],
                    selected_candidate=CANDIDATES[proposal['candidate_id']] if proposal else None,
                    proposal_sha256=proposal['result_hash'] if proposal else None,
                    legacy=plan['forward_evaluation_design'] == 'PROSPECTIVE_V1',
                    comparison_scope='COMPLETE_PROJECT' if plan['forward_evaluation_design'] == 'PROSPECTIVE_V2'
                        else 'DECLARATION')), 'DECLARATION_BINDING')
            else:
                check(not request.exists() and not output.exists() and not round_path.exists(), 'DECLARATION_MISSING')
        failure_path = directory/f'capture-failure-{attempt}.json'
        if failure_path.exists():
            failure = decode(_read(directory, failure_path.name))
            status_raw = _read(request, 'capture-status.json'); status = decode(status_raw)
            check(same(failure, dict(attempt=attempt, status='CAPTURE_FAILED_NOT_EVALUATED',
                quote_error=status['quotes']['error_type'], capture_status_sha256=hashlib.sha256(status_raw).hexdigest()))
                and status['quotes']['status'] == 'FAILED' and not output.exists() and not round_path.exists(), 'CAPTURE_FAILURE')
            failures.append(failure); continue
        if not (request/'plan.json').exists():
            check(not output.exists() and not round_path.exists(), 'REQUEST_MISSING')
            partial.append(dict(attempt=attempt, reason='CAPTURE_NOT_COMPLETED')); continue
        arguments = research.read_arguments(request, original_source=original_source)
        funding_sha = hashlib.sha256(_read(request, 'funding.json')).hexdigest() if 'observed_funding_records' in arguments else None
        check(same(decode(_read(request, 'plan.json')), _research_plan_value(plan, original_source,
            arguments['capture'], plan.get('settlement_window_ms'), funding_sha, forward_design)), 'REQUEST_PLAN_BINDING')
        if 'observed_funding_records' in arguments:
            check(same(arguments['observed_funding_records'], arguments['capture'].get('funding_observations', [])), 'FUNDING_RECORDS')
        cursor, review = restore(request/'journal')
        check(_read(request/'history', 'response.json') == _read(request/'journal', 'warmup.json')
              and same(decode(_read(request/'history', 'receipt.json')),
                       decode(_read(request/'journal', 'warmup-receipt.json'))), 'HISTORY_JOURNAL')
        capture = arguments['capture']
        originals = list(capture['bootstrap'].values())
        originals.extend(record for sample in capture['samples'] for record in sample.values())
        originals.append(capture['exchange_after'])
        if 'funding_after' in capture:
            originals.append(capture['funding_after'])
        originals.extend(capture.get('funding_observations', []))
        receipts = request/'quote-receipts'
        leaves = sorted(p for p in receipts.iterdir() if not p.name.startswith('error-'))
        check([p.name for p in leaves] == [f'{n:04d}.json' for n in range(len(originals))]
              and Counter(canonical(decode(_read(receipts, p.name))) for p in leaves)
                  == Counter(canonical(value) for value in originals), 'QUOTE_RECEIPTS')
        stream = decode(_read(request/'journal', 'summary.json'))
        check(same(decode(_read(request, 'capture-status.json')), dict(
            stream={key: stream.get(key) for key in ('status','reason','closed_bars')},
            quotes=dict(status='CAPTURED', capture_id=capture['capture_id']))), 'CAPTURE_STATUS')
        closes = [close + n*INTERVAL_MS for n in range(plan['window_bars'])]
        check(review['closed_bars'] == len(closes) and [bar.close_ms for bar in cursor.bars[-len(closes):]] == closes
              and same(review['source'], original_source), 'JOURNAL_WINDOW')
        if not (output/'input.json').exists():
            check(not round_path.exists(), 'ARCHIVE_MISSING')
            partial.append(dict(attempt=attempt, reason='RESEARCH_NOT_PERSISTED')); continue
        check(_read(output, 'input.json') == experiment._bytes(experiment._encode(arguments)), 'REQUEST_ARCHIVE')
        if round_path.exists():
            row = decode(_read(directory, round_path.name))
        else:
            partial.append(dict(attempt=attempt, reason='ROUND_NOT_PERSISTED'))
            saved = [row for row in summary_rounds if row['attempt'] == attempt]
            if not saved:
                continue
            row = saved[0]
        recovery = row.get('recovered_from')
        run = job(row['run_id'], 'gold_bar_recover' if recovery else 'gold_bar_research', attempt,
                  'recovery' if recovery else 'attempt')
        check(run['status'] == 'done', 'ROUND_JOB_STATUS')
        publication = research.retained_publication(output, attempt)
        expected = round_result(attempt, run)
        check(expected['result_hash'] == publication['result_hash'] and same(expected['report'], publication['report']), 'ROUND_REPORT')
        if recovery:
            check(job(recovery, 'gold_bar_research', attempt, 'attempt')['status'] == 'error', 'RECOVERY_STATUS')
            expected['recovered_from'] = recovery
        if proposal is not None:
            selected = CANDIDATES[proposal['candidate_id']]
            expected['hypothesis'] = dict(candidate=selected, proposal_hash=proposal['result_hash'],
                outcome=next(c['outcome'] for c in expected['report']['candidates'] if c['candidate'] == selected), economic_eligible=False)
        elif plan['ai_plan']:
            check(False, 'HYPOTHESIS_MISSING')
        check(any(e['status'] == 'RESEARCH_SUBMITTED' for e in selected_events), 'SUBMISSION_EVENT_MISSING')
        if 'review' not in row:
            check(not round_path.exists() and same(row, expected), 'UNFINISHED_ROUND_BINDING')
            partial.append(dict(attempt=attempt, reason='REVIEW_NOT_COMPLETED'))
            completed.append(row)
            continue
        review_run = job(row['review']['run_id'], 'gold_bar_receipt', attempt, 'review')
        check(review_run['status'] == 'done', 'REVIEW_JOB_STATUS')
        metrics = review_run['metrics']
        expected['review'] = dict(run_id=review_run['run_id'], status=metrics['verification_status'],
            **{key: metrics[key] for key in ('result_hash','trial_count','checked_at_ms','artifact_sha256')}, economic_eligible=False)
        check(metrics['result_hash'] == publication['result_hash'] and type(row.get('reviewed_at_ms')) is int
              and row['reviewed_at_ms'] >= metrics['checked_at_ms'], 'REVIEW_TIME')
        expected['reviewed_at_ms'] = row['reviewed_at_ms']
        check(same(row, expected), 'ROUND_BINDING')
        checks = []
        for leaf in sorted((output/'receipt-checks').glob('*.json')):
            raw = _read(leaf.parent, leaf.name); receipt = decode(raw)
            check(leaf.name == hashlib.sha256(raw).hexdigest()+'.json' and raw == _encode(receipt), 'RECEIPT_HASH')
            check(receipt.get('schema') == 'GOLD_BAR_RESEARCH_RECEIPT_CHECK_V1'
                and receipt.get('result_hash') == publication['result_hash']
                and same(receipt.get('trial_ids'), publication['trial_ids'])
                and receipt.get('artifact_sha256') == hashlib.sha256(_read(output,'independent-request.json')).hexdigest()
                and all(receipt.get(k) is False for k in ('authoritative','economic_eligible','submission_enabled')), 'RECEIPT_BINDING')
            if receipt['checked_at_ms'] == metrics['checked_at_ms'] and receipt['verification']['status'] == metrics['verification_status']:
                checks.append(receipt)
        check(bool(checks), 'REVIEW_RECEIPT_MISSING')
        completed.append(row)
        check(any(e['status'] == 'REVIEW_SUBMITTED' and e['run_id'] == review_run['run_id'] for e in selected_events), 'REVIEW_EVENT_MISSING')
        if not any(e['status'] == 'ROUND_COMPLETE' and e['run_id'] == run['run_id'] for e in selected_events):
            partial.append(dict(attempt=attempt, reason='ROUND_EVENT_NOT_PERSISTED'))
        candidates = list(row['report']['candidates'])
        if 'portfolio' in row['report']:
            candidates.append(dict(candidate='net_portfolio', loss_control=row['report']['portfolio']['loss_control']))
        check(all(c.get('loss_control',{}).get('status') in ('ACTIVE','STOPPED_FLAT','STOPPING_UNRESOLVED') for c in candidates), 'LOSS_CONTROL')
        stopped = [c['candidate'] for c in candidates if c['loss_control']['status'] != 'ACTIVE']
        if stopped:
            loss_stop = dict(attempt=attempt, candidates=stopped,
                unresolved=[c['candidate'] for c in candidates if c['loss_control']['status'] == 'STOPPING_UNRESOLVED'])
    if summary is None:
        partial.append(dict(reason='SUMMARY_NOT_PERSISTED'))
    elif 'error_type' in summary:
        check(summary.get('status') == 'RESEARCH_PROGRAM_INCOMPLETE' and same(summary.get('rounds'), completed)
              and type(summary.get('error_type')) is str and type(summary.get('failed_attempt')) is int
              and all(summary.get(k) is False for k in ('economic_eligible','authoritative','submission_enabled'))
              and same(summary.get('capture_failures',[]), failures), 'FAILED_SUMMARY')
        partial.append(dict(reason='PROGRAM_INTERRUPTED', error_type=summary['error_type']))
    else:
        check(not partial and same(summary, program_summary(completed, failures, loss_stop)), 'SUMMARY_BINDING')
        if failures:
            partial.append(dict(reason='CAPTURE_FAILED_NOT_EVALUATED'))
    return dict(plan=plan, summary=summary, events=events, jobs=sorted(consumed_jobs), partial=partial)


def recoverable_quote_failure(collected):
    """Only known quote transport failures before research submission may continue."""
    if not isinstance(collected, (list, tuple)) or len(collected) != 2:
        return False
    stream, error = collected
    if not (isinstance(stream, asyncio.CancelledError)
            or isinstance(stream, dict) and stream.get('status') == 'OBSERVED'):
        return False
    if isinstance(error, urllib.error.HTTPError) or isinstance(
            getattr(error, 'reason', error), ssl.SSLCertVerificationError):
        return False
    return isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError))


def next_close(now):
    target = (now // INTERVAL_MS + 1) * INTERVAL_MS
    return target if target - now >= 180000 else target + INTERVAL_MS


def prospective_close(now):
    """Leave the existing model budget before the whole observed bar opens."""
    opening = ((now + 180000 + INTERVAL_MS - 1) // INTERVAL_MS) * INTERVAL_MS
    return max(next_close(now), opening + INTERVAL_MS)


def _design_configuration(plan):
    return {key: plan[key] for key in ('quantity', 'initial_collateral', 'fee_rate',
        'portfolio_plan', 'settlement_window_ms') if key in plan}


async def wait_for_capture(target):
    while True:
        remaining = target - 100000 - time.time_ns() // 1_000_000
        if remaining <= 0:
            if remaining < -30000:
                raise ValueError('MISSED_CAPTURE_WINDOW')
            return
        await asyncio.sleep(min(10, remaining / 1000))


def hypothesis_objective(completed, capture_failures=()):
    """Bounded complete program feedback; never truncate failed/no-fill windows."""
    previous = [dict(attempt=row['attempt'], result_hash=row['result_hash'],
        candidates=row['report']['candidates'], review=row['review']) for row in completed]
    outcomes = ['INPUT_UNAVAILABLE', 'NO_SIGNAL', 'NO_FILL', 'OPEN_AT_END', 'FLAT_TERMINAL']
    reviews = sorted({row['review']['status'] for row in previous})
    def candidate_values(row, name):
        candidate = next(c for c in row['candidates'] if c['candidate'] == name)
        account = candidate.get('account') or {}
        return [outcomes.index(candidate['outcome']),
            *[candidate[k] for k in ('order_count', 'filled_order_count', 'open_position_count')],
            *[compact_diagnostic(account.get(k)) for k in ('net_equity_change', 'fees', 'max_observed_drawdown')]]
    memory = dict(candidate_order=list(CANDIDATES),
        columns=['attempt', 'review_index', 'candidates'], reviews=reviews, outcomes=outcomes,
        candidate_columns=['outcome_index', 'orders', 'fills', 'open', 'net_USDT', 'fee_USDT', 'dd_ratio'],
        evidence_sha256=hashlib.sha256(json.dumps(previous, sort_keys=True,
            allow_nan=False, separators=(',', ':')).encode()).hexdigest(),
        windows=[[row['attempt'], reviews.index(row['review']['status']),
            [candidate_values(row, name) for name in CANDIDATES]] for row in previous])
    if capture_failures:
        memory['capture_failures_not_evaluated'] = list(capture_failures)
    objective = ('Choose a fixed hypothesis for the next unseen window; all three are tested. '
        'Net is mark-to-market including open positions; fees uncalibrated. No economic eligibility. '
        'Summaries use 5 significant digits; null is unknown. Prior windows: '
        + json.dumps(memory, sort_keys=True, allow_nan=False, separators=(',', ':')))
    if len(objective) > 2000:
        raise ValueError('PROGRAM_FEEDBACK_EXCEEDS_PROVIDER_LIMIT')
    return objective


def _research_plan(request, plan, source, document, settlement_window_ms, forward_design=None):
    records = document.get('funding_observations', [])
    digest = None
    if settlement_window_ms is not None and records:
        _write(request / 'funding.json', records)
        digest = hashlib.sha256((request / 'funding.json').read_bytes()).hexdigest()
    return _research_plan_value(plan, source, document, settlement_window_ms, digest, forward_design)


def _research_plan_value(plan, source, document, settlement_window_ms, funding_sha256, forward_design=None):
    """Original request assembly, with publication I/O kept in its caller."""
    result = dict(release_identity=source,
                  **{k: plan[k] for k in ('quantity', 'initial_collateral', 'fee_rate')})
    if 'portfolio_plan' in plan:
        result['portfolio_plan'] = plan['portfolio_plan']
    if plan.get('register_original_trials') is True:
        result['register_original_trials'] = True
    if forward_design is not None:
        result['forward_design'] = forward_design
        if settlement_window_ms is not None:
            result['settlement_window_ms'] = settlement_window_ms
    records = document.get('funding_observations', [])
    if settlement_window_ms is not None and records:
        result['observed_funding'] = dict(settlement_window_ms=settlement_window_ms,
                                          records_sha256=funding_sha256)
    return result


async def run_program(*, program_id, first_attempt, rounds, ai_plan=False, continuous=False,
                      window_bars=2, settlement_window_ms=None, portfolio=False, initial_proposal=None,
                      initial_memory=None, max_capture_failures=0, quote_retry_profile='LEGACY_V1',
                      register_original_trials=True):
    if (type(register_original_trials) is not bool or type(portfolio) is not bool or type(continuous) is not bool or type(ai_plan) is not bool or any(type(v) is not int for v in (program_id, first_attempt, rounds))
            or not 1 <= program_id <= 1000 or not 1 <= rounds <= 8
            or not 1 <= first_attempt <= 1001 - rounds
            or type(window_bars) is not int or not 2 <= window_bars <= 4
            or window_bars != 2 and not continuous):
        raise ValueError('INVALID_GOLD_BAR_PROGRAM')
    transport = quote_transport_profile(quote_retry_profile)
    if type(max_capture_failures) is not int or not 0 <= max_capture_failures <= 2:
        raise ValueError('INVALID_CAPTURE_FAILURE_BUDGET')
    if initial_memory is not None and initial_proposal is None:
        raise ValueError('INITIAL_MEMORY_REQUIRES_PROPOSAL')
    if initial_memory is not None and not portfolio:
        raise ValueError('INITIAL_MEMORY_REQUIRES_PORTFOLIO')
    if initial_proposal is not None and not ai_plan:
        raise ValueError('INITIAL_PROPOSAL_REQUIRES_AI_PLAN')
    if settlement_window_ms is not None and (not continuous or type(settlement_window_ms) is not int
            or not 1 <= settlement_window_ms <= 1000):
        raise ValueError('INVALID_PROGRAM_FUNDING_WINDOW')
    root = runtime_state_root() / 'data/cache/gold_bar_research'
    if root.resolve() != root.absolute():
        raise ValueError('UNSAFE_GOLD_BAR_PROGRAM_PATH')
    directory = root / 'programs' / str(program_id)
    for attempt in range(first_attempt, first_attempt + rounds):
        if any((root / section / str(attempt)).exists() for section in ('requests', 'results')):
            raise FileExistsError('GOLD_BAR_ATTEMPT_ALREADY_USED')
    directory.mkdir(mode=0o700, parents=True)
    source = source_identity()
    target = (prospective_close if register_original_trials else next_close)(time.time_ns() // 1_000_000)
    plan = dict(schema='GOLD_BAR_PROGRAM_V1', program_id=program_id,
        first_attempt=first_attempt, rounds=rounds, first_close_ms=target, ai_plan=ai_plan,
        source=source, quantity='0.02', initial_collateral='1000', fee_rate='0.0005',
        quote_samples=None if window_bars > 2 else 120 if continuous else 12, quote_interval_seconds=10,
        quote_collection='THROUGH_LAST_CLOSE_PLUS_30_SECONDS' if window_bars > 2 else 'FIXED_BASE_SAMPLES',
        observation_timing='THROUGH_LAST_CLOSE_PLUS_40_SECONDS' if window_bars > 2 else 'FIXED_DURATION',
        dense_close_sampling=continuous,
        window_schedule='NEXT_UNOBSERVED_WINDOW_AFTER_REVIEW_V1', minimum_lead_ms=180000,
        observation_seconds=window_bars * 900 if continuous else 300, continuous=continuous,
        window_bars=window_bars if continuous else 1,
        account_mode='ONE_ACCOUNT_PER_CONTINUOUS_TAPE' if continuous else 'INDEPENDENT_TECHNICAL_WINDOWS', submission_enabled=False)
    if register_original_trials:
        plan['register_original_trials'] = True
        plan['forward_evaluation_design'] = 'PROSPECTIVE_V3'
    if quote_retry_profile != 'LEGACY_V1':
        plan['quote_transport'] = transport
    if max_capture_failures:
        plan['capture_recovery'] = dict(max_failures=max_capture_failures,
            policy='NEW_UNSEEN_WINDOW_WITH_RETAINED_FAILED_ATTEMPT',
            failed_windows_evaluated=False, gaps_filled=False)
    if portfolio:
        plan['portfolio_plan'] = dict(quantities={n:'0.01' for n in CANDIDATES}, max_notional='150')
    if settlement_window_ms is not None:
        plan['settlement_window_ms'] = settlement_window_ms
    initial = None
    imported_memory = None
    if initial_proposal is not None:
        from tools.gold_initial_proposal import freeze_proposal
        initial, files = freeze_proposal(initial_proposal, directory/'hypothesis-1', memory=initial_memory)
        plan['initial_proposal'] = dict(proposal_hash=initial['result_hash'], files=files,
            known_at_ms=time.time_ns() // 1_000_000, scope='FIRST_UNSEEN_WINDOW_ONLY')
        if initial_memory is not None:
            from tools.gold_cost_review import _read as read_initial
            from services.research.gold_portfolio_memory import continue_memory
            imported_memory = json.loads(read_initial(directory/'hypothesis-1', 'research-memory.json'))
            continued = continue_memory(imported_memory, [], next_attempt=first_attempt,
                                        as_of_ms=time.time_ns() // 1_000_000)
            if len(continued['records']) + rounds - 1 > 7:
                raise ValueError('INITIAL_MEMORY_PROGRAM_BUDGET')
            plan['initial_proposal']['memory_scope'] = 'PRIOR_TECHNICAL_RECORDS_FOR_FUTURE_PROPOSALS'
    _write(directory / 'plan.json', plan)
    completed = []
    capture_failures = []
    program_loss_stop = None
    active_attempt = first_attempt
    queue = None

    def event(status, **extra):
        row = dict(status=status, observed_at_ms=time.time_ns() // 1_000_000,
                   attempt=active_attempt, submission_enabled=False, **extra)
        with (directory / 'events.jsonl').open('a') as handle:
            handle.write(json.dumps(row, sort_keys=True) + '\n')
            handle.flush()
        print(json.dumps(row), flush=True)

    try:
        registry = RunRegistry(ResearchRunStore(str(runtime_state_root() / 'data/cache/research_runs.db')))
        queue = JobQueue(registry)
        previous_close = None
        for index in range(rounds):
            active_attempt = first_attempt + index
            nominal = (target if previous_close is None else
                       previous_close + INTERVAL_MS * plan['window_bars'])
            planned_at = time.time_ns() // 1_000_000
            close = max(nominal, prospective_close(planned_at) if register_original_trials else next_close(planned_at))
            _write(directory / f'window-plan-{index + 1}.json', dict(
                attempt=active_attempt, planned_at_ms=planned_at, nominal_close_ms=nominal,
                target_close_ms=close, minimum_lead_ms=180000,
                reason='PROCESSING_AND_MINIMUM_LEAD' if close != nominal else 'NOMINAL_CADENCE',
                account_mode=plan['account_mode'], submission_enabled=False))
            if close != nominal:
                event('WINDOW_ADVANCED', nominal_close_ms=nominal, target_close_ms=close)
            previous_close = close
            proposal = initial if index == 0 else None
            if proposal is not None:
                event('USING_RETAINED_HYPOTHESIS', proposal_hash=proposal['result_hash'], target_close_ms=close)
            elif ai_plan:
                objective = hypothesis_objective(completed, capture_failures)
                from services.research.gold_portfolio_memory import continue_memory
                memory = continue_memory(imported_memory, completed, next_attempt=active_attempt,
                                      as_of_ms=time.time_ns() // 1_000_000)
                options = {'candidate_catalog': CATALOG}
                if memory is not None:
                    options['research_memory'] = memory
                    _write(directory / f'portfolio-memory-{index + 1}.json', memory)
                event('PROPOSING_HYPOTHESIS', target_close_ms=close)
                proposal = await asyncio.to_thread(bounded_proposal, objective, [0, 1, 2], [],
                    protocol='SINGLE_V1', output=directory / f'hypothesis-{index + 1}',
                    options=options, propose_fn=propose)
            forward_design = None
            if register_original_trials:
                from quantos.contracts.gold_forward_evaluation_design import declare
                from tools.registered_research_run import _put
                if source_identity() != source:
                    raise ValueError('PROGRAM_SOURCE_CHANGED')
                forward_design = declare(program_id=program_id, attempt=active_attempt,
                    source_identity=source, configuration=_design_configuration(plan), first_close_ms=close,
                    window_bars=plan['window_bars'], declared_at_ms=time.time_ns() // 1_000_000,
                    selected_candidate=CANDIDATES[proposal['candidate_id']] if proposal else None,
                    proposal_sha256=proposal['result_hash'] if proposal else None)
                _put(directory/f'evaluation-design-{index + 1}.json', forward_design)
            event('WAITING_CLOSED_BAR', target_close_ms=close)
            await wait_for_capture(close)
            if source_identity() != source:
                raise ValueError('PROGRAM_SOURCE_CHANGED')
            request = root / 'requests' / str(active_attempt)
            request.mkdir(mode=0o700, parents=True)
            event('COLLECTING')
            await asyncio.to_thread(capture, request / 'history')
            expected_closes = [close + i * INTERVAL_MS for i in range(plan['window_bars'])]
            extended = window_bars > 2
            observation_seconds = plan['observation_seconds']
            if extended:
                # End after the last requested close, before a further unplanned bar.
                observation_seconds = (expected_closes[-1] + 40000 - time.time_ns() // 1_000_000 + 999) // 1000

            capture_stop = Event()

            async def quotes():
                receipts = request / 'quote-receipts'
                receipts.mkdir(mode=0o700)
                ordinal = 0
                error_ordinal = 0

                def retain(record):
                    nonlocal ordinal
                    _write(receipts / f'{ordinal:04d}.json', record)
                    ordinal += 1

                def retain_error(record):
                    nonlocal error_ordinal
                    _write(receipts / f'error-{error_ordinal:04d}.json', record)
                    error_ordinal += 1

                collector = capture_gold_extended if extended else capture_gold_continuous if continuous else capture_public
                settings = ({'close_times': tuple(expected_closes)} if extended else
                    dict(samples=plan['quote_samples'], **({'dense_close_times': tuple(expected_closes)} if continuous
                         else {'symbol': 'XAUUSDT'})))
                if settlement_window_ms is not None:
                    settings['observe_funding'] = True
                if quote_retry_profile != 'LEGACY_V1':
                    settings['retry_profile'] = quote_retry_profile
                document = await asyncio.to_thread(collector,
                    **settings, interval_seconds=plan['quote_interval_seconds'],
                    on_record=retain, on_error=retain_error, cancel_event=capture_stop)
                _write(request / 'quotes.json', document)
                return document

            collected = await collect_joint(
                observe(request / 'history', request / 'journal', seconds=observation_seconds,
                        **({'extended': True} if extended else {})),
                quotes(), capture_stop)
            capture_status = {}
            for name, value in zip(('stream', 'quotes'), collected):
                if isinstance(value, BaseException):
                    capture_status[name] = dict(status='FAILED', error_type=type(value).__name__)
                elif name == 'stream':
                    capture_status[name] = {key: value.get(key) for key in ('status', 'reason', 'closed_bars')}
                else:
                    capture_status[name] = dict(status='CAPTURED', capture_id=value['capture_id'])
            _write(request / 'capture-status.json', capture_status)
            if (any(isinstance(value, BaseException) for value in collected)
                    or collected[0].get('status') != 'OBSERVED'):
                if max_capture_failures and recoverable_quote_failure(collected):
                    failure = dict(attempt=active_attempt, status='CAPTURE_FAILED_NOT_EVALUATED',
                        quote_error=type(collected[1]).__name__,
                        capture_status_sha256=hashlib.sha256(
                            (request/'capture-status.json').read_bytes()).hexdigest())
                    capture_failures.append(failure)
                    _write(directory/f'capture-failure-{active_attempt}.json', failure)
                    if len(capture_failures) <= max_capture_failures:
                        event('CAPTURE_FAILURE_RETAINED', failed_attempt=active_attempt,
                              next_unseen_window=index+1 < rounds)
                        continue
                raise ValueError('JOINT_CAPTURE_FAILED')
            cursor, review = restore(request / 'journal')
            if (review['closed_bars'] != len(expected_closes)
                    or [bar.close_ms for bar in cursor.bars[-len(expected_closes):]] != expected_closes
                    or review['source'] != source or source_identity() != source):
                raise ValueError('PROGRAM_CLOSED_BAR_OR_SOURCE_MISMATCH')
            # This final file makes the fixed-path request consumable by the worker.
            ready_plan = _research_plan(request, plan, source, collected[1], settlement_window_ms, forward_design)
            if forward_design is not None:
                _put(request/'plan.json', ready_plan)
            else:
                _write(request/'plan.json', ready_plan)
            uid = queue.submit('gold_bar_research', {'attempt': active_attempt},
                time.time_ns() // 1_000_000_000,
                invocation_id=f'gold-bar-program-{program_id}-attempt-{active_attempt}')
            event('RESEARCH_SUBMITTED', run_id=uid)
            await asyncio.to_thread(queue.join)
            run = registry.get(uid)
            recovered_from = None
            if run.status != 'done':
                event('RESEARCH_FAILED', run_id=uid)
                if run.status != 'error':
                    raise ValueError('REGISTERED_RESEARCH_FAILED')
                from tools.gold_cost_review import _read
                from services.governance.json_integrity import load_json_without_duplicate_keys
                archive_root = root / 'results' / str(active_attempt)
                try:
                    terminal_path = archive_root / 'manifest.json'
                    if register_original_trials:
                        from tools.registered_gold_collection import publication_recoverable
                        recoverable = publication_recoverable({'attempt':active_attempt})
                    elif terminal_path.exists() or terminal_path.is_symlink():
                        terminal = load_json_without_duplicate_keys(_read(archive_root, 'manifest.json'))
                        recoverable = type(terminal) is dict and terminal.get('terminal') == 'PASSED'
                    else:
                        registration = load_json_without_duplicate_keys(_read(archive_root, 'running.json'))
                        recoverable = (type(registration) is dict
                            and registration.get('schema') == 'GOLD_BAR_RUNNING_ARCHIVE_V1'
                            and type(registration.get('manifest')) is dict
                            and registration['manifest'].get('source') == source)
                except (OSError, ValueError):
                    raise ValueError('REGISTERED_RESEARCH_FAILED') from None
                if not recoverable:
                    raise ValueError('REGISTERED_RESEARCH_FAILED')
                recovered_from = uid
                uid = queue.submit('gold_bar_recover', {'attempt': active_attempt},
                    time.time_ns() // 1_000_000_000,
                    invocation_id=f'gold-bar-program-{program_id}-recovery-{active_attempt}')
                event('RESEARCH_RECOVERY_SUBMITTED', run_id=uid, original_run_id=recovered_from)
                await asyncio.to_thread(queue.join)
                run = registry.get(uid)
                if run.status != 'done':
                    raise ValueError('REGISTERED_RESEARCH_RECOVERY_FAILED')
            completed.append(round_result(active_attempt, run.to_dict()))
            if recovered_from is not None:
                completed[-1]['recovered_from'] = recovered_from
            if proposal is not None:
                selected = CANDIDATES[proposal['candidate_id']]
                completed[-1]['hypothesis'] = dict(candidate=selected,
                    proposal_hash=proposal['result_hash'],
                    outcome=next(row['outcome'] for row in completed[-1]['report']['candidates']
                                 if row['candidate'] == selected),
                    economic_eligible=False)
            review_uid = queue.submit('gold_bar_receipt', {'attempt': active_attempt},
                time.time_ns() // 1_000_000_000,
                invocation_id=f'gold-bar-program-{program_id}-review-{active_attempt}')
            event('REVIEW_SUBMITTED', run_id=review_uid)
            await asyncio.to_thread(queue.join)
            review_run = registry.get(review_uid)
            if review_run.status != 'done':
                event('REVIEW_FAILED', run_id=review_uid)
                raise ValueError('REGISTERED_REVIEW_FAILED')
            if review_run.metrics['result_hash'] != completed[-1]['result_hash']:
                raise ValueError('PROGRAM_REVIEW_RESULT_MISMATCH')
            completed[-1]['review'] = dict(run_id=review_uid,
                status=review_run.metrics['verification_status'],
                result_hash=review_run.metrics['result_hash'],
                trial_count=review_run.metrics['trial_count'],
                checked_at_ms=review_run.metrics['checked_at_ms'],
                artifact_sha256=review_run.metrics['artifact_sha256'],
                economic_eligible=False)
            completed[-1]['reviewed_at_ms'] = time.time_ns() // 1_000_000
            _write(directory / f'round-{index + 1}.json', completed[-1])
            event('ROUND_COMPLETE', run_id=uid)
            candidates = list(completed[-1]['report']['candidates'])
            if 'portfolio' in completed[-1]['report']:
                candidates.append(dict(candidate='net_portfolio',
                    loss_control=completed[-1]['report']['portfolio']['loss_control']))
            if any(c.get('loss_control', {}).get('status') not in
                   ('ACTIVE', 'STOPPED_FLAT', 'STOPPING_UNRESOLVED') for c in candidates):
                raise ValueError('PROGRAM_LOSS_CONTROL_UNAVAILABLE')
            stopped = [c['candidate'] for c in candidates if c['loss_control']['status'] != 'ACTIVE']
            if stopped:
                program_loss_stop = dict(attempt=active_attempt, candidates=stopped,
                    unresolved=[c['candidate'] for c in candidates
                                if c['loss_control']['status'] == 'STOPPING_UNRESOLVED'])
                event('PROGRAM_LOSS_STOP', candidates=stopped, unresolved=program_loss_stop['unresolved'])
                break
        result = program_summary(completed, capture_failures, program_loss_stop)
    except BaseException as exc:
        _write(directory / 'summary.json', dict(status='RESEARCH_PROGRAM_INCOMPLETE',
            rounds=completed, failed_attempt=active_attempt, error_type=type(exc).__name__,
            **({'capture_failures': capture_failures} if capture_failures else {}),
            economic_eligible=False, authoritative=False, submission_enabled=False))
        raise
    finally:
        if queue is not None:
            await asyncio.to_thread(queue.join)
    _write(directory / 'summary.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--program-id', type=int, required=True)
    parser.add_argument('--first-attempt', type=int, required=True)
    parser.add_argument('--rounds', type=int, required=True)
    parser.add_argument('--ai-plan', action='store_true', help='Use the configured research model before each capture')
    parser.add_argument('--register-original-trials', action='store_true', default=True,
                        help='Register new Gold trials in the original managed project before computation; no evaluation authority')
    parser.add_argument('--initial-proposal', help='Completed gold SINGLE_V1 proposal directory for the first unseen window')
    parser.add_argument('--max-capture-failures', type=int, default=0,
                        help='0–2 retained quote transport failures allowed before stopping; each consumes a round')
    parser.add_argument('--quote-retry-profile', choices=('LEGACY_V1', 'BOUNDED_BACKOFF_V1', 'FRESH_CADENCE_V1'),
                        default='LEGACY_V1', help='Prospective public-request retry budget; no window restart')
    parser.add_argument('--initial-memory', help='Exact research-memory JSON bound by the imported proposal')
    parser.add_argument('--continuous', action='store_true', help='Keep one simulated account across two closed bars per tape')
    parser.add_argument('--window-bars', type=int, default=2,
                        help='Continuous tape: 2 (original), or 3–4 newly observed closes')
    parser.add_argument('--settlement-window-ms', type=int,
                        help='Explicit 1–1000ms research funding timing assumption; continuous tapes only')
    parser.add_argument('--portfolio', action='store_true',
                        help='Also register the fixed shared-account target ensemble')
    args = parser.parse_args(argv)
    print(json.dumps(asyncio.run(run_program(**vars(args))), sort_keys=True))


if __name__ == '__main__':
    main()
