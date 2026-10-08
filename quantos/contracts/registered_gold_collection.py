"""Original Gold registration metadata; no evaluation or trading authority."""
from quantos.contracts import registered_research_run as native
from quantos.contracts.gold_long_statistics_budget import MAX_CLOSED_BARS

SCHEMA = 'QUANTOS_REGISTERED_GOLD_COLLECTION_V1'
DECLARED_SCHEMA = 'QUANTOS_REGISTERED_GOLD_COLLECTION_V2'
REVIEW_SCHEMA = 'QUANTOS_REGISTERED_GOLD_COLLECTION_V3'
LONG_REVIEW_SCHEMA = 'QUANTOS_REGISTERED_GOLD_COLLECTION_V4'
FIELDS = frozenset(('schema', 'native_invocation_id', 'plan_sha256', 'input_sha256',
    'registered_at_ms', 'attempt_count', 'native_trial_ids', 'evaluation_plan', 'statistical_review',
    'risk_review', 'locked_oos', 'candidate_admission', 'economic_authority',
    'promotion_approved', 'submission_enabled'))


def validate(value):
    native.require(type(value) is dict, 'GOLD_REGISTRATION_FIELDS')
    reviewed = value.get('schema') in (REVIEW_SCHEMA, LONG_REVIEW_SCHEMA)
    declared = value.get('schema') in (DECLARED_SCHEMA, REVIEW_SCHEMA, LONG_REVIEW_SCHEMA)
    native.require(set(value) == FIELDS | ({'forward_declaration'} if declared else set()), 'GOLD_REGISTRATION_FIELDS')
    native.canonical(value, maximum=65_536)
    native.require(value['schema'] in (SCHEMA, DECLARED_SCHEMA, REVIEW_SCHEMA, LONG_REVIEW_SCHEMA), 'GOLD_REGISTRATION_SCHEMA')
    for name in ('native_invocation_id', 'plan_sha256', 'input_sha256'):
        native.require(native._hash(value[name]), 'GOLD_REGISTRATION_IDENTITY')
    native.require(type(value['registered_at_ms']) is int and 0 <= value['registered_at_ms'] <= 2**63-1
        and type(value['attempt_count']) is int and value['attempt_count'] in (3,4)
        and type(value['native_trial_ids']) is list
        and len(value['native_trial_ids']) == value['attempt_count']
        and all(native._hash(v) for v in value['native_trial_ids'])
        and len(set(value['native_trial_ids'])) == value['attempt_count'], 'GOLD_REGISTRATION_POPULATION')
    if declared:
        declaration = value['forward_declaration']
        native.require(type(declaration) is dict and set(declaration) ==
            {'design_sha256', 'declared_at_ms', 'observation_start_ms', 'closed_bars', 'time_evidence'},
            'GOLD_DECLARATION_FIELDS')
        native.require(native._hash(declaration['design_sha256'])
            and type(declaration['declared_at_ms']) is int and type(declaration['observation_start_ms']) is int
            and 0 <= declaration['declared_at_ms'] < declaration['observation_start_ms'] < value['registered_at_ms']
            and declaration['time_evidence'] == 'LOCAL_CLOCK_NOT_INDEPENDENTLY_VERIFIED', 'GOLD_DECLARATION_TIME')
        closes = declaration['closed_bars']
        native.require(type(closes) is list and 1 <= len(closes) <= (
            MAX_CLOSED_BARS if value['schema'] == LONG_REVIEW_SCHEMA else 4)
            and all(type(t) is int and 0 < t <= value['registered_at_ms'] for t in closes)
            and closes == [declaration['observation_start_ms']+900000*(i+1) for i in range(len(closes))],
            'GOLD_DECLARATION_WINDOW')
    native.require(value['evaluation_plan'] == ('WINDOW_DECLARED_UNVERIFIED' if declared else 'NOT_PREREGISTERED')
        and value['statistical_review'] == ('STATISTICAL_POLICY_DECLARED' if reviewed else 'FORWARD_ADAPTER_UNAVAILABLE')
        and value['risk_review'] == 'NOT_INDEPENDENTLY_REPLAYED'
        and value['locked_oos'] == 'UNKNOWN' and value['candidate_admission'] == 'NOT_APPROVED'
        and all(value[name] is False for name in ('economic_authority', 'promotion_approved', 'submission_enabled')),
        'GOLD_REGISTRATION_AUTHORITY')
    return value


def validate_report(report):
    """Bind registration metadata to this existing Gold projection."""
    native.require(type(report) is dict, 'GOLD_REGISTRATION_REPORT')
    if 'native_registration' not in report:
        native.require('native_statistics' not in report, 'GOLD_STATISTICS_UNREGISTERED')
        return report
    value = validate(report['native_registration'])
    native.require(report.get('trial_ids') == value['native_trial_ids']
        and type(report.get('candidates')) is list and len(report['candidates']) == 3
        and value['attempt_count'] == (4 if 'portfolio' in report else 3)
        and all(report.get(k) is False for k in ('economic_eligible', 'authoritative', 'submission_enabled')),
        'GOLD_REGISTRATION_REPORT_BINDING')
    if 'portfolio' in report:
        native.require(type(report['portfolio']) is dict
            and report['portfolio'].get('trial_id') == value['native_trial_ids'][-1],
            'GOLD_REGISTRATION_PORTFOLIO')
    if 'native_statistics' in report:
        from quantos.contracts.gold_native_statistics import validate as statistics
        from quantos.contracts.research_statistical_review import LONG_GOLD_REPORT_SCHEMA
        projection = statistics(report['native_statistics'])
        native.require(value['schema'] in (DECLARED_SCHEMA, REVIEW_SCHEMA, LONG_REVIEW_SCHEMA)
            and (projection['schema'] == 'QUANTOS_GOLD_NATIVE_STATISTICS_V2') == (
                value['schema'] in (REVIEW_SCHEMA, LONG_REVIEW_SCHEMA))
            and (value['schema'] == LONG_REVIEW_SCHEMA) == (
                (projection.get('review') or {}).get('schema') == LONG_GOLD_REPORT_SCHEMA)
            and all(projection[k] == value[k] for k in ('native_invocation_id','plan_sha256','input_sha256'))
            and projection['declaration_sha256'] == value['forward_declaration']['design_sha256']
            and [a['native_trial_id'] for a in projection['attempts']] == value['native_trial_ids']
            and projection['sample'] == dict(start_ms=value['forward_declaration']['closed_bars'][0],
                end_ms=value['forward_declaration']['closed_bars'][-1]+10000, frequency_ms=10000),
            'GOLD_STATISTICS_REPORT_BINDING')
        accounts = report['candidates']+([report['portfolio']] if 'portfolio' in report else [])
        for account, series in zip(accounts, projection['attempts'], strict=True):
            native.require(series['metrics'] is not None and all(series['metrics'][k] == account['account'][k]
                for k in ('initial_equity','ending_equity','net_equity_change','fees','funding',
                          'reconciliation_residual','max_observed_drawdown')), 'GOLD_STATISTICS_ACCOUNT_BINDING')
    return report
