"""Fixed-path closed-bar experiment job for the existing guarded research queue."""
import base64
import hashlib
from decimal import Decimal

from services.execution.backtest_authority import exact_identity_hash
from quantos.runtime_paths import runtime_state_root
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.gold_cost_review import _read
from tools.gold_bar_experiment import archive, resume_archive, JOURNAL_FILES, _encode, _bytes
from tools.gold_bar_review import review_request
from tools.perpetual_replay_research import source_identity
from services.research.gold_bar_diagnostics import account_diagnostics, target_diagnostics


def _arguments(params):
    if (type(params) is not dict or set(params) != {'attempt'}
            or type(params['attempt']) is not int or not 1 <= params['attempt'] <= 1000):
        raise ValueError('INVALID_GOLD_BAR_ATTEMPT')
    root = runtime_state_root() / 'data/cache/gold_bar_research'
    request = root / 'requests' / str(params['attempt'])
    output = root / 'results'
    if request.resolve() != request.absolute() or output.resolve() != output.absolute():
        raise ValueError('UNSAFE_GOLD_BAR_RESEARCH_PATH')
    return read_arguments(request, original_source=source_identity()), output


def read_arguments(request, *, original_source):
    """Read the original request; historical source admission belongs to caller."""
    plan = decode(_read(request, 'plan.json'))
    if (type(plan) is not dict
            or not {'quantity', 'initial_collateral', 'fee_rate', 'release_identity'} <= set(plan)
            or set(plan) - {'quantity', 'initial_collateral', 'fee_rate', 'release_identity',
                            'observed_funding', 'portfolio_plan', 'register_original_trials',
                            'forward_design', 'settlement_window_ms', 'segmented_input'}
            or plan['release_identity'] != original_source
            or any(type(plan[n]) is not str for n in ('quantity', 'initial_collateral', 'fee_rate'))):
        raise ValueError('INVALID_GOLD_BAR_RESEARCH_PLAN')
    if 'register_original_trials' in plan and plan['register_original_trials'] is not True:
        raise ValueError('INVALID_GOLD_REGISTRATION_MODE')
    if 'forward_design' in plan:
        from quantos.contracts.gold_forward_evaluation_design import validate
        from quantos.contracts.registered_research_run import digest
        design = validate(plan['forward_design'])
        if (plan.get('register_original_trials') is not True
                or design['source_identity_sha256'] != digest(original_source)):
            raise ValueError('INVALID_GOLD_FORWARD_DESIGN_BINDING')
    if 'settlement_window_ms' in plan:
        if ('forward_design' not in plan or type(plan['settlement_window_ms']) is not int
                or not 1 <= plan['settlement_window_ms'] <= 1000
                or plan['forward_design']['configuration'].get('settlement_window_ms') != plan['settlement_window_ms']):
            raise ValueError('INVALID_GOLD_BAR_FUNDING_PLAN')
    arguments = dict(release_identity=plan['release_identity'],
        **{n: Decimal(plan[n]) for n in ('quantity', 'initial_collateral', 'fee_rate')},
        capture=decode(_read(request, 'quotes.json')),
        journal_files={n: base64.b64encode(_read(request / 'journal', n)).decode()
                       for n in JOURNAL_FILES})
    if 'settlement_window_ms' in plan:
        arguments['settlement_window_ms'] = plan['settlement_window_ms']
    if 'observed_funding' in plan:
        binding = plan['observed_funding']
        if (type(binding) is not dict or set(binding) != {'settlement_window_ms', 'records_sha256'}
                or type(binding['settlement_window_ms']) is not int
                or not 1 <= binding['settlement_window_ms'] <= 1000):
            raise ValueError('INVALID_GOLD_BAR_FUNDING_PLAN')
        raw = _read(request, 'funding.json')
        if hashlib.sha256(raw).hexdigest() != binding['records_sha256']:
            raise ValueError('GOLD_BAR_FUNDING_RECEIPT_BINDING')
        records = decode(raw)
        if type(records) is not list or not 1 <= len(records) <= 1000:
            raise ValueError('INVALID_GOLD_BAR_FUNDING_RECORDS')
        arguments.update(observed_funding_records=records,
                         settlement_window_ms=binding['settlement_window_ms'])
        if 'settlement_window_ms' in plan and binding['settlement_window_ms'] != plan['settlement_window_ms']:
            raise ValueError('INVALID_GOLD_BAR_FUNDING_PLAN')
    if 'portfolio_plan' in plan:
        from tools.gold_bar_experiment import trial_candidates
        arguments['portfolio_plan'] = plan['portfolio_plan']
        trial_candidates(arguments)
    if 'segmented_input' in plan:
        from quantos.contracts import gold_segmented_input as segmented
        binding = plan['segmented_input']
        if (type(binding) is not dict or set(binding) != {'schema','segments_sha256'}
                or binding['schema'] != segmented.SCHEMA):
            raise ValueError('GOLD_SEGMENT_REQUEST_BINDING')
        raw = _read(request, 'segments.json')
        if hashlib.sha256(raw).hexdigest() != binding['segments_sha256']:
            raise ValueError('GOLD_SEGMENT_REQUEST_CHANGED')
        arguments.update(input_schema=segmented.SCHEMA, continuation_segments=decode(raw))
        segmented.journals(_encode(arguments))
    return arguments


def _registered_request(params):
    root = runtime_state_root()/'data/cache/gold_bar_research/requests'/str(params['attempt'])
    return decode(_read(root, 'plan.json')).get('register_original_trials') is True


def run_research(params):
    arguments, output = _arguments(params)
    if _registered_request(params):
        from tools.registered_gold_collection import run
        return run(params)
    if (output/str(params['attempt'])/'registered-native.json').exists():
        raise ValueError('GOLD_REGISTERED_REQUEST_CHANGED')
    output.mkdir(parents=True, exist_ok=True)
    destination = output / str(params['attempt'])
    archive(arguments, destination)
    return _publication(destination, params['attempt'])


def resume_research(params):
    """Recover interrupted computation or publication under the original source."""
    arguments, output = _arguments(params)
    if _registered_request(params):
        from tools.registered_gold_collection import run
        return run(params, recovering=True)
    if (output/str(params['attempt'])/'registered-native.json').exists():
        raise ValueError('GOLD_REGISTERED_REQUEST_CHANGED')
    destination = output / str(params['attempt'])
    if _read(destination, 'input.json') != _bytes(_encode(arguments)):
        raise ValueError('GOLD_BAR_RECOVERY_REQUEST_CHANGED')
    resume_archive(arguments, destination)
    return _publication(destination, params['attempt'])


def _publication(destination, attempt):
    review = review_request(destination)
    report = decode(_read(destination, 'report.json'))
    return publication_result(report, review, attempt)


def retained_publication(destination, attempt):
    """Verify retained native review and project the existing job result only."""
    from tools.gold_bar_review import retained_request
    publication = publication_result(decode(_read(destination, 'report.json')),
                                     retained_request(destination), attempt)
    if (destination/'registered-native.json').exists():
        from tools.registered_gold_collection import retained_evidence
        publication['report'].update(retained_evidence(destination, attempt))
    return publication


def publication_result(report, review, attempt):
    """Original projection shared by publication and readonly recovery."""
    if (report['result_hash'] != review['result_hash']
            or exact_identity_hash({k: v for k, v in report.items() if k != 'result_hash'},
                                   name='gold_bar_public_comparison') != review['result_hash']):
        raise ValueError('GOLD_BAR_REPORT_CHANGED')
    candidates = []
    for trial in report['trials']:
        simulation = trial['simulation']
        candidates.append(dict(candidate=trial['candidate'], outcome=trial['outcome'],
            input_ready=any(o['status'] == 'READY' for o in trial['signal_observations']),
            order_count=len(simulation['orders']),
            filled_order_count=sum(o['filled_at'] is not None for o in simulation['orders']),
            open_position_count=len(simulation['ending_state']['positions']),
            account=account_diagnostics(simulation), loss_control=trial['loss_control']))
    projection = dict(candidates=candidates, trial_ids=review['trial_ids'],
        fee_rate=report['fee_rate'], fee_policy=report['fee_policy'],
        economic_eligible=False, authoritative=False, submission_enabled=False)
    if 'portfolio' in report:
        portfolio = report['portfolio']
        sim = portfolio['simulation']
        projection['portfolio'] = dict(trial_id=review['trial_ids'][-1],
            result_hash=portfolio['result_hash'], construction=portfolio['construction'],
            order_count=len(sim['orders']),
            filled_order_count=sum(o['filled_at'] is not None for o in sim['orders']),
            open_position_count=len(sim['ending_state']['positions']),
            account=account_diagnostics(sim), loss_control=portfolio['loss_control'],
            target_diagnostics=target_diagnostics(portfolio['target_trace']),
            economic_eligible=False, submission_enabled=False)
    return dict(report=projection, schema='GOLD_BAR_RESEARCH_RESULT_V1', attempt=attempt,
        trial_ids=review['trial_ids'], candidate_count=3, result_hash=review['result_hash'],
        verification_status=review['local_reproduction_status'],
        independent_review_status=review['status'], economic_eligible=False, authoritative=False,
        submission_enabled=False)
