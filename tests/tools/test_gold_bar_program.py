import asyncio
import json
import shutil

import pytest

from tests.tools.test_gold_bar_observe import recorded
from tests.research.test_gold_bar_dataset import raw_rows, STEP
from tests.execution.test_perpetual_public_replay import START, gold_capture_fixture


@pytest.mark.parametrize(('ai_plan', 'review_failure', 'publication_failure'),
    [(False, False, False), (True, False, False), (True, True, False), (True, False, True)])
def test_collection_enters_original_queue_and_keeps_candidate_results(tmp_path, monkeypatch, ai_plan,
        review_failure, publication_failure, computation_interruption=False, recovery_failure=False,
        stop_case=None, portfolio=False, seeded=False, registered_source=None):
    from tools import gold_bar_program as program, gold_bar_experiment, gold_bar_research
    rows = raw_rows()
    shift = START // STEP * STEP - 1001 * STEP
    for row in rows:
        row[0] += shift
        row[6] += shift
    _, journal = recorded(tmp_path, monkeypatch, values=rows, received=START + 1500, source=registered_source)
    from tests.research.test_ai_factory_provider import LOCAL, Transport
    from tools.ai_factory_provider import propose
    calls = []

    def proposal(*args, **kwargs):
        calls.append(1)
        return propose(*args, **kwargs, environ=LOCAL,
                       transport=Transport({'candidate_id': 0, 'reason': 'test channel'}))

    monkeypatch.setattr(program, 'propose', proposal)
    state = tmp_path / 'state'
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(state))
    source = registered_source if registered_source is not None else {'fixture': True}
    for module in (program, gold_bar_experiment, gold_bar_research):
        monkeypatch.setattr(module, 'source_identity', lambda: source)
    monkeypatch.setattr(program, 'next_close', lambda now: START // STEP * STEP)
    clock = [START//STEP*STEP-STEP-180000]
    if registered_source is not None:
        monkeypatch.setattr(program.time, 'time_ns', lambda: clock[0]*1000000)

    async def wait(target):
        if registered_source is not None:
            clock[0] = target+100000

    async def collect(history, output, *, seconds):
        shutil.copytree(journal, output)
        return json.loads((journal / 'summary.json').read_text())

    monkeypatch.setattr(program, 'wait_for_capture', wait)
    monkeypatch.setattr(program, 'capture', lambda path: path.mkdir())
    monkeypatch.setattr(program, 'observe', collect)
    monkeypatch.setattr(program, 'capture_public', lambda **kw: gold_capture_fixture())
    if stop_case == 'failed':
        def failed_computation(arguments):
            raise ValueError('SYNTHETIC_TERMINAL_COMPUTATION_FAILURE')
        monkeypatch.setattr(gold_bar_experiment, '_execute', failed_computation)
    elif stop_case == 'cancelled':
        queues = []
        class ObservedQueue(program.JobQueue):
            def __init__(self, registry):
                super().__init__(registry)
                queues.append(self)
        monkeypatch.setattr(program, 'JobQueue', ObservedQueue)
        original_review = gold_bar_research.review_request
        def cancel_during_publication(path):
            queue = queues[0]
            running = queue._reg.list(kind='gold_bar_research')[0]
            assert queue.cancel(running.run_id)
            return original_review(path)
        monkeypatch.setattr(gold_bar_research, 'review_request', cancel_during_publication)
    if stop_case:
        with pytest.raises(ValueError, match='REGISTERED_RESEARCH_FAILED'):
            asyncio.run(program.run_program(program_id=1, first_attempt=1, rounds=2, ai_plan=ai_plan,
                register_original_trials=registered_source is not None))
        root = state / 'data/cache/gold_bar_research'
        terminal = json.loads((root / 'results/1/manifest.json').read_text())
        assert terminal['terminal'] == ('FAILED' if stop_case == 'failed' else 'PASSED')
        events = [json.loads(line) for line in (root / 'programs/1/events.jsonl').read_text().splitlines()]
        assert not any(e['status'] == 'RESEARCH_RECOVERY_SUBMITTED' for e in events)
        assert not (root / 'requests/2').exists()
        return
    if publication_failure:
        original_review = gold_bar_research.review_request
        pending = [True]
        def interrupted(path):
            if pending:
                pending.pop()
                raise RuntimeError('SYNTHETIC_PUBLICATION_INTERRUPTION')
            return original_review(path)
        monkeypatch.setattr(gold_bar_research, 'review_request', interrupted)
    if computation_interruption:
        original_write = gold_bar_experiment._write
        pending = [True]
        def interrupted_computation(path, value):
            result = original_write(path, value)
            if path.name == 'running.json' and pending:
                pending.pop()
                raise RuntimeError('SYNTHETIC_COMPUTATION_INTERRUPTION')
            return result
        monkeypatch.setattr(gold_bar_experiment, '_write', interrupted_computation)
    if recovery_failure:
        def fail_recovery(params):
            raise ValueError('SYNTHETIC_RECOVERY_FAILURE')
        monkeypatch.setattr(gold_bar_research, 'resume_research', fail_recovery)
        with pytest.raises(ValueError, match='REGISTERED_RESEARCH_RECOVERY_FAILED'):
            asyncio.run(program.run_program(program_id=1, first_attempt=1, rounds=2, ai_plan=ai_plan,
                register_original_trials=registered_source is not None))
        root = state / 'data/cache/gold_bar_research'
        summary = json.loads((root / 'programs/1/summary.json').read_text())
        assert summary['rounds'] == []
        events = [json.loads(line) for line in (root / 'programs/1/events.jsonl').read_text().splitlines()]
        assert sum(e['status'] == 'RESEARCH_RECOVERY_SUBMITTED' for e in events) == 1
        assert not (root / 'requests/2').exists()
        return
    if review_failure:
        from tools import gold_bar_receipt
        def fail_check(params):
            raise ValueError('SYNTHETIC_RECEIPT_FAILURE')
        monkeypatch.setattr(gold_bar_receipt, 'run_check', fail_check)
        with pytest.raises(ValueError, match='REGISTERED_REVIEW_FAILED'):
            asyncio.run(program.run_program(program_id=1, first_attempt=1, rounds=2, ai_plan=ai_plan,
                register_original_trials=registered_source is not None))
        root = state / 'data/cache/gold_bar_research'
        summary = json.loads((root / 'programs/1/summary.json').read_text())
        assert summary['status'] == 'RESEARCH_PROGRAM_INCOMPLETE'
        assert len(summary['rounds']) == 1
        assert 'review' not in summary['rounds'][0]
        assert (root / 'results/1/report.json').exists()
        assert not (root / 'requests/2').exists()
        assert len(calls) == 1
        return
    seed = None
    if seeded:
        from tools.ai_factory_collaboration import bounded_proposal
        seed = tmp_path/'prior-hypothesis'
        bounded_proposal('Prior technical account diagnostics; choose a future hypothesis',
            [0, 1, 2], [], protocol='SINGLE_V1', output=seed,
            options={'candidate_catalog': 'GOLD_BAR_CANDIDATES_V1'}, propose_fn=proposal)
        calls.clear()
    kwargs = {'initial_proposal': seed} if seeded else {}
    if registered_source is None:
        kwargs['register_original_trials'] = False
    result = asyncio.run(program.run_program(program_id=1, first_attempt=1, rounds=1,
        ai_plan=ai_plan, portfolio=portfolio, **kwargs))
    assert result['status'] == 'RESEARCH_PROGRAM_COMPLETE'
    assert result['submission_enabled'] is False
    assert len(result['rounds']) == 1
    row = result['rounds'][0]
    assert row['status'] == 'done'
    if publication_failure or computation_interruption:
        assert row['recovered_from'] != row['run_id']
    assert row['review']['status'] == 'INDEPENDENT_AUTHORITY_UNCONFIGURED'
    assert row['review']['trial_count'] == (4 if portfolio else 3)
    assert row['review']['result_hash'] == row['result_hash']
    assert row['review']['run_id'] != row['run_id']
    assert len(list((state / 'data/cache/gold_bar_research/results/1/receipt-checks').glob('*.json'))) == 1
    assert len(calls) == int(ai_plan and not seeded)
    if seeded:
        assert (state/'data/cache/gold_bar_research/programs/1/hypothesis-1/result.json').read_bytes() == (seed/'result.json').read_bytes()
    if ai_plan:
        assert row['hypothesis']['candidate'] == 'channel'
        assert row['hypothesis']['outcome'] == 'NO_FILL'
        assert len(row['hypothesis']['proposal_hash']) == 64
    else:
        assert 'hypothesis' not in row
    assert row['report']['candidates'][0]['outcome'] == 'NO_FILL'
    assert row['report']['candidates'][0]['filled_order_count'] == 0
    assert row['report']['candidates'][0]['order_count'] == 1
    assert (state / 'data/cache/gold_bar_research/results/1/trials.db').exists()
    assert (state / 'data/cache/research_runs.db').exists()
    with pytest.raises(FileExistsError):
        asyncio.run(program.run_program(program_id=1, first_attempt=1, rounds=1, ai_plan=ai_plan))


def test_retained_hypothesis_enters_registered_experiment_without_second_model_call(tmp_path, monkeypatch):
    test_collection_enters_original_queue_and_keeps_candidate_results(tmp_path, monkeypatch,
        ai_plan=True, review_failure=False, publication_failure=False, seeded=True)


@pytest.mark.parametrize('recovery_failure', [False, True])
def test_program_automatically_recovers_interruption_once(tmp_path, monkeypatch, recovery_failure):
    test_collection_enters_original_queue_and_keeps_candidate_results(tmp_path, monkeypatch,
        ai_plan=True, review_failure=False, publication_failure=False,
        computation_interruption=True, recovery_failure=recovery_failure)


@pytest.mark.parametrize('stop_case', ['failed', 'cancelled'])
def test_program_does_not_recover_terminal_failure_or_user_cancellation(tmp_path, monkeypatch, stop_case):
    test_collection_enters_original_queue_and_keeps_candidate_results(tmp_path, monkeypatch,
        ai_plan=False, review_failure=False, publication_failure=False, stop_case=stop_case)


def test_failed_capture_is_retained_and_does_not_submit_or_start_next_round(tmp_path, monkeypatch):
    from tools import gold_bar_program as program
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    monkeypatch.setattr(program, 'source_identity', lambda: {'fixture': True})

    async def wait(target):
        pass

    def fail(path):
        path.mkdir()
        (path / 'partial.json').write_text('{}')
        raise OSError('network unavailable')

    monkeypatch.setattr(program, 'wait_for_capture', wait)
    monkeypatch.setattr(program, 'capture', fail)
    with pytest.raises(OSError):
        asyncio.run(program.run_program(program_id=2, first_attempt=10, rounds=2))
    root = tmp_path / 'data/cache/gold_bar_research'
    summary = json.loads((root / 'programs/2/summary.json').read_text())
    assert summary['status'] == 'RESEARCH_PROGRAM_INCOMPLETE'
    assert summary['rounds'] == []
    assert (root / 'requests/10/history/partial.json').exists()
    assert not (root / 'requests/10/plan.json').exists()
    assert not (root / 'requests/11').exists()
    assert not (root / 'results').exists()


def test_parallel_failures_keep_both_stream_and_quote_diagnostics(tmp_path, monkeypatch):
    from tools import gold_bar_program as program
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    monkeypatch.setattr(program, 'source_identity', lambda: {'fixture': True})

    async def wait(target):
        pass

    async def observe(*args, **kwargs):
        return {'status': 'INCOMPLETE', 'reason': 'STREAM_FAILED_ConnectionClosedError', 'closed_bars': 1}

    def fail(**kwargs):
        kwargs['on_record']({'kind': 'exchange', 'fixture': True})
        raise TimeoutError('quote timeout')

    monkeypatch.setattr(program, 'wait_for_capture', wait)
    monkeypatch.setattr(program, 'capture', lambda path: path.mkdir())
    monkeypatch.setattr(program, 'observe', observe)
    monkeypatch.setattr(program, 'capture_public', fail)
    with pytest.raises(ValueError, match='JOINT_CAPTURE_FAILED'):
        asyncio.run(program.run_program(program_id=3, first_attempt=3, rounds=1))
    root = tmp_path / 'data/cache/gold_bar_research/requests/3'
    status = json.loads((root / 'capture-status.json').read_text())
    assert status['stream']['reason'] == 'STREAM_FAILED_ConnectionClosedError'
    assert status['quotes'] == {'status': 'FAILED', 'error_type': 'TimeoutError'}
    assert json.loads((root / 'quote-receipts/0000.json').read_text())['kind'] == 'exchange'
    assert not (root / 'plan.json').exists()


def test_continuous_program_submits_two_closes_as_one_experiment(tmp_path, monkeypatch):
    from tests.tools.test_gold_bar_observed_replay import continuous_inputs
    from tools import gold_bar_program as program, gold_bar_experiment, gold_bar_research
    journal, capture, _ = continuous_inputs(tmp_path, monkeypatch)
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path / 'state'))
    for module in (program, gold_bar_experiment, gold_bar_research):
        monkeypatch.setattr(module, 'source_identity', lambda: {'fixture': True})
    monkeypatch.setattr(program, 'next_close', lambda now: START // STEP * STEP)

    async def wait(target):
        pass

    async def observe(history, output, *, seconds):
        assert seconds == 1800
        shutil.copytree(journal, output)
        return json.loads((journal / 'summary.json').read_text())

    def collect(**kwargs):
        assert kwargs['samples'] == 120
        assert kwargs['dense_close_times'] == (START // STEP * STEP, START // STEP * STEP + STEP)
        return capture

    monkeypatch.setattr(program, 'wait_for_capture', wait)
    monkeypatch.setattr(program, 'capture', lambda path: path.mkdir())
    monkeypatch.setattr(program, 'observe', observe)
    monkeypatch.setattr(program, 'capture_gold_continuous', collect)
    result = asyncio.run(program.run_program(program_id=4, first_attempt=4, rounds=1, continuous=True,
        register_original_trials=False))
    assert result['status'] == 'RESEARCH_PROGRAM_COMPLETE'
    assert len(result['rounds']) == 1
    root = tmp_path / 'state/data/cache/gold_bar_research'
    report = json.loads((root / 'results/4/report.json').read_text())
    assert len(report['dataset_identity']['closed_decision_times']) == 2
    assert len(report['trials']) == 3


def test_eight_round_program_feedback_fits_provider_and_keeps_every_prior_window():
    from tools.gold_bar_program import hypothesis_objective
    from tools.ai_factory_provider import propose
    from tests.research.test_ai_factory_provider import LOCAL, Transport
    completed = [dict(attempt=i, result_hash=str(i) * 64,
        report={'candidates': [dict(candidate=c, outcome='INPUT_UNAVAILABLE',
            order_count=240, filled_order_count=240, open_position_count=1,
            account=dict(net_equity_change='-123456789012345678901234.56789',
                         fees='12345678901234567890123.456789',
                         max_observed_drawdown='0.00000001234567890123456789'))
            for c in ('channel', 'trend', 'reversion')]},
        review={'status': 'VERIFIED_SIGNATURE_ENGINEERING_ONLY', 'artifact_sha256': str(i) * 64})
        for i in range(1, 8)]
    objective = hypothesis_objective(completed)
    assert len(objective) <= 2000
    memory = json.loads(objective.split('Prior windows: ', 1)[1])
    assert [row[0] for row in memory['windows']] == list(range(1, 8))
    assert memory['candidate_order'] == ['channel', 'trend', 'reversion']
    assert len(memory['evidence_sha256']) == 64
    assert all(len(row[2]) == 3 for row in memory['windows'])
    assert memory['windows'][0][2][0][-3:] == ['-1.2346e+23', '1.2346e+22', '1.2346e-8']
    propose(objective, [0, 1, 2], [], environ=LOCAL,
        candidate_catalog='GOLD_BAR_CANDIDATES_V1',
        transport=Transport({'candidate_id': 0, 'reason': 'review earlier outcomes'}))
    changed = json.loads(json.dumps(completed))
    changed[0]['result_hash'] = 'f' * 64
    assert hypothesis_objective(changed) != objective
    for failures in (1, 2):
        failed = [dict(attempt=8-i, status='CAPTURE_FAILED_NOT_EVALUATED',
                       quote_error='ConnectionError', capture_status_sha256='f'*64)
                  for i in range(failures)]
        bounded = hypothesis_objective(completed[:7-failures], failed)
        assert len(bounded) <= 2000
        assert json.loads(bounded.split('Prior windows: ', 1)[1])['capture_failures_not_evaluated'] == failed


def test_model_feedback_keeps_legacy_missing_costs_unknown():
    from tools.gold_bar_program import hypothesis_objective
    completed = [dict(attempt=1, result_hash='a' * 64,
        report={'candidates': [dict(candidate=c, outcome='OPEN_AT_END',
            order_count=1, filled_order_count=1, open_position_count=1)
            for c in ('channel', 'trend', 'reversion')]}, review={'status': 'INDEPENDENT_AUTHORITY_UNCONFIGURED'})]
    objective = hypothesis_objective(completed)
    memory = json.loads(objective.split('Prior windows: ', 1)[1])
    assert all(row[-3:] == [None, None, None] for row in memory['windows'][0][2])
    assert 'mark-to-market' in objective and 'uncalibrated' in objective


def test_slow_review_advances_next_capture_before_proposing(tmp_path, monkeypatch):
    from tools import gold_bar_program as program, gold_bar_experiment, gold_bar_research, gold_bar_receipt
    values = raw_rows()
    first = START // STEP * STEP
    for row in values:
        row[0] += first - 1001 * STEP
        row[6] += first - 1001 * STEP
    _, journal = recorded(tmp_path, monkeypatch, values=values, received=START + 1500)
    state = tmp_path / 'state'
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(state))
    for module in (program, gold_bar_experiment, gold_bar_research):
        monkeypatch.setattr(module, 'source_identity', lambda: {'fixture': True})
    clock = [START + 1500]
    monkeypatch.setattr(program.time, 'time_ns', lambda: clock[0] * 1000000)
    actual_next = program.next_close
    monkeypatch.setattr(program, 'next_close', lambda now: first if now < first + STEP // 2 else actual_next(now))
    original_check = gold_bar_receipt.run_check
    def slow_review(params):
        result = original_check(params)
        clock[0] = first + STEP - 54000
        return result
    monkeypatch.setattr(gold_bar_receipt, 'run_check', slow_review)
    targets = []
    async def wait(target):
        targets.append(target)
        if len(targets) == 2:
            assert target == first + 2 * STEP
            assert target - clock[0] >= 180000
            raise RuntimeError('STOP_BEFORE_SECOND_CAPTURE')
    async def collect(history, output, *, seconds):
        shutil.copytree(journal, output)
        return json.loads((journal / 'summary.json').read_text())
    monkeypatch.setattr(program, 'wait_for_capture', wait)
    monkeypatch.setattr(program, 'capture', lambda path: path.mkdir())
    monkeypatch.setattr(program, 'observe', collect)
    monkeypatch.setattr(program, 'capture_public', lambda **kw: gold_capture_fixture())
    with pytest.raises(RuntimeError, match='STOP_BEFORE_SECOND_CAPTURE'):
        asyncio.run(program.run_program(program_id=1, first_attempt=1, rounds=2, register_original_trials=False))
    root = state / 'data/cache/gold_bar_research/programs/1'
    plan = json.loads((root / 'window-plan-2.json').read_text())
    assert plan['nominal_close_ms'] == first + STEP
    assert plan['target_close_ms'] == first + 2 * STEP
    assert plan['reason'] == 'PROCESSING_AND_MINIMUM_LEAD'
    assert json.loads((root / 'round-1.json').read_text())['status'] == 'done'



def test_program_retains_portfolio_fourth_trial_and_independent_review(tmp_path, monkeypatch):
    test_collection_enters_original_queue_and_keeps_candidate_results(tmp_path, monkeypatch,
        ai_plan=False, review_failure=False, publication_failure=False, portfolio=True)


@pytest.mark.parametrize('rounds', [2, 3])
def test_bounded_transport_failures_keep_attempts_and_move_to_unseen_windows(tmp_path, monkeypatch, rounds):
    from urllib.error import URLError
    from tools import gold_bar_program as program
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    monkeypatch.setattr(program, 'source_identity', lambda: {'fixture': True})
    targets = []
    async def wait(target):
        targets.append(target)
    async def observe(*args, **kwargs):
        await asyncio.sleep(60)
    def fail(**kwargs):
        kwargs['on_error']({'error_type':'URLError', 'retry_scheduled':False})
        raise URLError('transport unavailable')
    monkeypatch.setattr(program, 'wait_for_capture', wait)
    monkeypatch.setattr(program, 'capture', lambda path: path.mkdir())
    monkeypatch.setattr(program, 'observe', observe)
    monkeypatch.setattr(program, 'capture_public', fail)
    if rounds == 3:
        with pytest.raises(ValueError, match='JOINT_CAPTURE_FAILED'):
            asyncio.run(program.run_program(program_id=4, first_attempt=4, rounds=rounds, max_capture_failures=2))
        result = json.loads((tmp_path/'data/cache/gold_bar_research/programs/4/summary.json').read_text())
    else:
        result = asyncio.run(program.run_program(program_id=4, first_attempt=4, rounds=rounds,
                                                max_capture_failures=2))
    assert result['status'] == 'RESEARCH_PROGRAM_INCOMPLETE'
    assert result['rounds'] == []
    assert [r['attempt'] for r in result['capture_failures']] == list(range(4, 4+rounds))
    assert len(targets) == rounds
    assert targets[1] > targets[0]
    root = tmp_path/'data/cache/gold_bar_research'
    assert not (root/'results').exists()
    for attempt in range(4, 4+rounds):
        assert (root/f'requests/{attempt}/quote-receipts/error-0000.json').exists()
    assert 'CAPTURE_FAILED_NOT_EVALUATED' in program.hypothesis_objective([], result['capture_failures'])


def test_quote_capture_recovery_does_not_retry_http_certificate_or_data_errors():
    import ssl
    from urllib.error import HTTPError, URLError
    from tools.gold_bar_program import recoverable_quote_failure
    assert recoverable_quote_failure([asyncio.CancelledError(), URLError(ssl.SSLEOFError())])
    for error in (HTTPError('https://example.invalid', 429, 'limit', None, None),
                  URLError(ssl.SSLCertVerificationError()), ValueError('bad data')):
        assert not recoverable_quote_failure([asyncio.CancelledError(), error])
