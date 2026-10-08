"""One fresh-window research child per retained feedback hypothesis."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import threading

from quantos.runtime_paths import runtime_state_root
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.ai_factory_collaboration import verify_collaboration
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
from tools.demo_feedback_publish import hash_value
from tools.gold_bar_capture import _write
from tools.gold_bar_feedback_research import run as verify_retained_hypothesis
from tools.gold_cost_review import _read
from tools.perpetual_replay_research import source_identity

_LOCK = threading.Lock()


def experiment_binding(key, retained, source):
    return dict(feedback_sha256=key, proposal_hash=retained['proposal_hash'], source=source,
        account_mode='INDEPENDENT_TECHNICAL_EXPERIMENT', rounds=1,
        window_bars=4, submission_enabled=False)


def experiment_result(binding, plan, raw, retained):
    result = decode(raw)
    if (plan.get('source') != binding['source'] or plan.get('rounds') != 1 or plan.get('first_attempt') != 1
            or plan.get('initial_proposal', {}).get('proposal_hash') != retained['proposal_hash']
            or result.get('status') not in ('RESEARCH_PROGRAM_COMPLETE', 'RESEARCH_PROGRAM_STOPPED_LOSS_LIMIT')
            or type(result.get('rounds')) is not list or len(result['rounds']) != 1
            or any(result.get(k) is not False for k in ('economic_eligible', 'authoritative', 'submission_enabled'))):
        raise ValueError('BAR_FEEDBACK_EXPERIMENT_RESULT')
    return dict(schema='GOLD_BAR_FEEDBACK_EXPERIMENT_V1', status=result['status'],
        **binding, program_summary_sha256=hashlib.sha256(raw).hexdigest(),
        economic_eligible=False, authoritative=False)


def review_retained_experiment(base, key, *, original_source, original_base, jobs):
    """Read original child/parent bindings; never launch, repair, or re-register."""
    from quantos.contracts.research_evidence_json import canonical
    from tools.gold_bar_feedback_research import review_retained_hypothesis
    from tools.gold_bar_program import verify_retained_program
    base = Path(base)
    parent = review_retained_hypothesis(base, key, original_source=original_source, original_base=original_base)
    if parent['partial']:
        raise ValueError('BAR_FEEDBACK_EXPERIMENT_HYPOTHESIS_INCOMPLETE')
    target = base/'bar-feedback-experiments'/key
    binding = decode(_read(target, 'binding.json'))
    if canonical(binding) != canonical(experiment_binding(key, parent['result'], original_source)):
        raise ValueError('BAR_FEEDBACK_EXPERIMENT_BINDING')
    research = target/'state/data/cache/gold_bar_research'
    directory = research/'programs/1'
    if not (directory/'plan.json').exists():
        if (target/'result.json').exists() or any(research.rglob('*.json')):
            raise ValueError('BAR_FEEDBACK_EXPERIMENT_PROGRAM_MISSING')
        return dict(result=None, program=None, partial=['CHILD_PROGRAM_NOT_PERSISTED'])
    program = verify_retained_program(directory, requests_root=research/'requests',
        results_root=research/'results', jobs=jobs, original_source=original_source)
    if not (target/'result.json').exists():
        return dict(result=None, program=program, partial=['EXPERIMENT_RESULT_NOT_PERSISTED', *program['partial']])
    if program['partial']:
        raise ValueError('BAR_FEEDBACK_EXPERIMENT_PROGRAM_INCOMPLETE')
    expected = experiment_result(binding, program['plan'], _read(directory, 'summary.json'), parent['result'])
    result = decode(_read(target, 'result.json'))
    if canonical(result) != canonical(expected):
        raise ValueError('BAR_FEEDBACK_EXPERIMENT_RESULT_CHANGED')
    return dict(result=result, program=program, partial=[])


def run_research(params):
    if type(params) is not dict or set(params) != {'feedback_sha256'}:
        raise ValueError('BAR_FEEDBACK_EXPERIMENT_PARAMS')
    key = params['feedback_sha256']
    hash_value(key)
    base = runtime_state_root()/'data/cache/ai_factory'
    hypothesis = base/'bar-feedback-research'/key
    # A missing or incomplete model result cannot turn this job into another AI call.
    _read(hypothesis, 'result.json')
    if verify_collaboration(hypothesis/'hypothesis') is None:
        raise ValueError('BAR_FEEDBACK_HYPOTHESIS_INCOMPLETE')
    retained = verify_retained_hypothesis(key)
    source = source_identity()
    target = base/'bar-feedback-experiments'/key
    if target.resolve() != target.absolute():
        raise ValueError('BAR_FEEDBACK_EXPERIMENT_PATH')
    target.parent.mkdir(parents=True, exist_ok=True)
    binding = experiment_binding(key, retained, source)
    with acquire_runtime_lock(target.parent/('.'+key+'.lock'), process_lock=_LOCK) as acquired:
        if not acquired:
            raise ValueError('BAR_FEEDBACK_EXPERIMENT_BUSY')
        if target.exists():
            if decode(_read(target, 'binding.json')) != binding:
                raise ValueError('BAR_FEEDBACK_EXPERIMENT_BINDING')
            # An interrupted process is retained for review, never a fresh window retry.
            original = decode(_read(target, 'result.json'))
        else:
            original = None
            target.mkdir(mode=0o700)
            _write(target/'binding.json', binding)
            state = target/'state'
            state.mkdir(mode=0o700)
            code = Path(__file__).resolve().parents[1]
            env = {k: v for k, v in os.environ.items() if k in (
                'PATH', 'LANG', 'LC_ALL', 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
                'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'SSL_CERT_FILE', 'SSL_CERT_DIR')}
            env.update(QUANTOS_STATE_ROOT=str(state), PYTHONPATH=str(code),
                PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
                LIVE_TRADING_ENABLED='false', REAL_BROKER_ENABLED='false',
                ALLOW_ORDER_SUBMISSION='false', ALLOW_AI_DIRECT_TRADING='false',
                REQUIRE_HUMAN_APPROVAL='true')
            argv = [sys.executable, '-P', '-B', '-m', 'tools.gold_bar_program',
                '--program-id', '1', '--first-attempt', '1', '--rounds', '1', '--ai-plan',
                '--initial-proposal', str(hypothesis/'hypothesis'), '--continuous',
                '--window-bars', '4', '--settlement-window-ms', '1000', '--portfolio',
                '--register-original-trials']
            with (target/'process.log').open('xb') as log:
                child = subprocess.run(argv, cwd=code, env=env, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT, timeout=7200, check=False)
            if child.returncode != 0:
                raise ValueError('BAR_FEEDBACK_EXPERIMENT_CHILD_FAILED')
        directory = target/'state/data/cache/gold_bar_research/programs/1'
        plan = decode(_read(directory, 'plan.json'))
        raw = _read(directory, 'summary.json')
        summary = experiment_result(binding, plan, raw, retained)
        if source_identity() != source or verify_retained_hypothesis(key) != retained:
            raise ValueError('BAR_FEEDBACK_EXPERIMENT_RESULT')
        if original is None:
            _write(target/'result.json', summary)
        elif original != summary:
            raise ValueError('BAR_FEEDBACK_EXPERIMENT_RESULT_CHANGED')
        return summary
