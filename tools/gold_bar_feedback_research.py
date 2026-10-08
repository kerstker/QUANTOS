"""Consume delivered Demo bar diagnostics into one bounded research hypothesis."""
import argparse
import json
import threading
import time

from quantos.runtime_paths import runtime_state_root
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from services.research.gold_bar_candidates import CATALOG
from tools.ai_factory_collaboration import bounded_proposal, verify_collaboration
from tools.ai_factory_provider import propose
from tools.ashare_alpha_runtime_lock import acquire_runtime_lock
from tools.demo_feedback_publish import REPORTED_BAR_SCHEMAS, hash_value, validate_bar_export
from tools.gold_bar_capture import _write
from tools.gold_cost_review import _read
from tools.perpetual_replay_research import source_identity
from pathlib import Path
from quantos.contracts.research_evidence_json import canonical

_LOCK = threading.Lock()


def hypothesis_result(target, feedback_sha256, proposal, source):
    return dict(schema='GOLD_BAR_FEEDBACK_HYPOTHESIS_V1', status='HYPOTHESIS_RETAINED',
        feedback_sha256=feedback_sha256, proposal_hash=proposal['result_hash'],
        proposal_directory=str(target/'hypothesis'), candidate_id=proposal['candidate_id'],
        source=source, submission_enabled=False, economic_eligible=False, experiment_started=False)


def review_retained_hypothesis(base, feedback_sha256, *, original_source, original_base):
    """Read historical originals without a model call or receipt publication.

    ``original_base`` is the transport-verified original path, used as text only.
    Source/custody/barrier verification remains the caller's responsibility.
    """
    hash_value(feedback_sha256)
    base = Path(base); original_base = Path(original_base)
    if not original_base.is_absolute() or '..' in original_base.parts:
        raise ValueError('BAR_FEEDBACK_ORIGINAL_PATH')
    raw = _read(base/'bar-execution-feedback', feedback_sha256+'.json')
    value = decode(raw)
    cash = value.get('cash_feedback', value)
    validate_bar_export(value, source_commit=original_source['commit'], feedback_sha256=feedback_sha256,
        now=cash.get('available_at_ms'), cycle=value.get('bundle_sha256'))
    target = base/'bar-feedback-research'/feedback_sha256
    if _read(target, 'feedback.json') != raw:
        raise ValueError('BAR_FEEDBACK_RESEARCH_INPUT_CHANGED')
    if not (target/'hypothesis/plan.json').exists():
        if (target/'result.json').exists() or any((target/'hypothesis').rglob('*.json')):
            raise ValueError('BAR_FEEDBACK_HYPOTHESIS_MISSING')
        return dict(result=None, partial=['HYPOTHESIS_NOT_STARTED'])
    proposal = verify_collaboration(target/'hypothesis')
    if not (target/'result.json').exists():
        return dict(result=None, proposal=proposal, partial=['HYPOTHESIS_RESULT_NOT_PERSISTED'])
    if proposal is None:
        raise ValueError('BAR_FEEDBACK_HYPOTHESIS_INCOMPLETE')
    result = decode(_read(target, 'result.json'))
    expected = hypothesis_result(original_base/'bar-feedback-research'/feedback_sha256,
                                 feedback_sha256, proposal, original_source)
    if canonical(result) != canonical(expected):
        raise ValueError('BAR_FEEDBACK_RESEARCH_RESULT_CHANGED')
    return dict(result=result, proposal=proposal, partial=[])


def run(feedback_sha256):
    hash_value(feedback_sha256)
    base = runtime_state_root()/'data/cache/ai_factory'
    source = source_identity()
    raw = _read(base/'bar-execution-feedback', feedback_sha256+'.json')
    decoded = decode(raw)
    # Publication already checked the external executor cycle. Research retains
    # that public binding; it does not re-verify the private opening record.
    value = validate_bar_export(decoded, source_commit=source['commit'],
        feedback_sha256=feedback_sha256, now=time.time_ns()//1000000,
        cycle=decoded.get('bundle_sha256') if type(decoded) is dict else None)
    target = base/'bar-feedback-research'/feedback_sha256
    if target.resolve() != target.absolute():
        raise ValueError('BAR_FEEDBACK_RESEARCH_PATH')
    target.parent.mkdir(parents=True, exist_ok=True)
    reported = value['schema'] in REPORTED_BAR_SCHEMAS
    if reported:
        outcome = 'REPORTED_FLAT_ROUNDTRIP_WITH_RETAINED_GAPS'
        evidence = f"account_report_sha256={value['account_report_sha256']}; stream_coverage_repaired=false"
        if value['schema'] == 'GOLD_BAR_REPORTED_ACCOUNT_FEEDBACK_V2':
            evidence += (f"; opening_record_sha256={value['opening_record_sha256']}"
                         f"; bundle_sha256={value['bundle_sha256']}")
    else:
        outcome = ('NO_FILL' if value['cash_feedback']['schema'] ==
                   'DEMO_EXECUTION_RESEARCH_FEEDBACK_V1' else 'FLAT_ROUNDTRIP')
        evidence = f"cash_feedback_sha256={value['cash_feedback_sha256']}"
    objective = (
        'Choose a fixed hypothesis for a future unseen gold window; all three remain candidates. '
        'This is technical Demo feedback, not economic validation or a profitability score. '
        'The episode is terminal and flat with zero wallet residual. Strategy PnL is unknown. '
        'Do not infer returns from wallet balance. '
        f"Prior candidate={value['definition']['candidate']}; outcome={outcome}; "
        f"feedback_sha256={feedback_sha256}; definition_hash={value['definition_hash']}; "
        f"{evidence}."
    )
    with acquire_runtime_lock(target.parent/('.'+feedback_sha256+'.lock'),
                              process_lock=_LOCK) as acquired:
        if not acquired:
            raise ValueError('BAR_FEEDBACK_RESEARCH_BUSY')
        if target.exists():
            if _read(target, 'feedback.json') != raw:
                raise ValueError('BAR_FEEDBACK_RESEARCH_INPUT_CHANGED')
        else:
            target.mkdir(mode=0o700)
            with (target/'feedback.json').open('xb') as stream:
                stream.write(raw)
        proposal = bounded_proposal(objective, [0, 1, 2], [], protocol='SINGLE_V1',
            output=target/'hypothesis', options={'candidate_catalog': CATALOG}, propose_fn=propose)
        if (verify_collaboration(target/'hypothesis') != proposal
                or source_identity() != source
                or _read(base/'bar-execution-feedback', feedback_sha256+'.json') != raw
                or _read(target, 'feedback.json') != raw):
            raise ValueError('BAR_FEEDBACK_RESEARCH_CHANGED')
        result = hypothesis_result(target, feedback_sha256, proposal, source)
        if (target/'result.json').exists():
            if decode(_read(target, 'result.json')) != result:
                raise ValueError('BAR_FEEDBACK_RESEARCH_RESULT_CHANGED')
        else:
            _write(target/'result.json', result)
        return result


def run_research(params):
    if type(params) is not dict or set(params) != {'feedback_sha256'}:
        raise ValueError('BAR_FEEDBACK_RESEARCH_PARAMS')
    return run(params['feedback_sha256'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--feedback-sha256', required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.feedback_sha256)), flush=True)


if __name__ == '__main__':
    main()
