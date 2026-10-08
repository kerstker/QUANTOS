"""Selected-candidate facts from an already recomputed original review.

The project verifier supplies verified_statistics after replaying its kernels.
This internal projection is not a validator for caller-supplied certificates.
Missing forward, maintenance and leakage originals never become healthy flags.
Snapshot age is fixed at the retained evaluation time, not today's read time.
No fact can retroactively authorize a historical pre-trade decision.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

from quantos.contracts.decimal_arithmetic import canonical_decimal, ledger_decimal_context
from quantos.contracts.research_statistical_review import GOLD_REPORT_SCHEMAS, digest as statistical_digest
from quantos.streaming_identity import exact_stream_identity_hash as digest

SCHEMA = 'QUANTOS_GOLD_MODEL_RISK_INPUTS_V1'
FIELDS = ('age_days', 'validation_confidence', 'paper_confidence', 'decay_flag', 'overfit_flag', 'leakage_flag')
IDENTITY = ('invocation_id', 'native_trial_id', 'config_hash')


def require(ok, reason):
    if not ok:
        raise ValueError('GOLD_MODEL_INPUTS_' + reason)


def hashed(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None, 'HASH')
    return value


def time(value):
    require(type(value) is int and 0 <= value < 2**63, 'TIME')
    return value


def probability(value):
    if value is None:
        return None
    require(type(value) in (str, int, float, Decimal), 'PROBABILITY')
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('GOLD_MODEL_INPUTS_PROBABILITY') from None
    require(number.is_finite() and 0 <= number <= 1, 'PROBABILITY')
    return number


@dataclass(frozen=True)
class GoldModelRiskInputs:
    projection: dict

    @property
    def engine_metadata(self):
        p = self.projection
        require(p['projection_sha256'] == digest({k: v for k, v in p.items() if k != 'projection_sha256'}),
                'FACTS_CHANGED')
        facts = p['facts']
        require(set(facts) == set(FIELDS), 'FACT_FIELDS')
        if any(facts[k]['status'] != 'AVAILABLE' or facts[k]['value'] is None for k in FIELDS):
            return None
        values = {k: facts[k]['value'] for k in FIELDS}
        # No replication into three invented, independently evaluated alphas.
        item = {k: Decimal(values[k]) if k in FIELDS[:3] else values[k] for k in FIELDS}
        item.update(candidate_id=p['candidate_id'], native_trial_id=p['native_trial_id'])
        return (item,)


@ledger_decimal_context()
def from_verified_statistics(*, project_cut, subject, candidate_id, verified_statistics,
                             checked_at_ms, native_result_hash, causality_policy=None, causality_review=None,
                             net_forward_policy=None, net_forward_review=None, maintenance_policy=None, maintenance_review=None):
    """Internal seam; source caller must first recompute the statistical evidence."""
    hashed(native_result_hash)
    selected = {k: hashed(subject[k]) for k in IDENTITY}
    require(type(candidate_id) is str and 0 < len(candidate_id) <= 256, 'CANDIDATE')
    report = None if verified_statistics is None else verified_statistics['report']
    values = dict.fromkeys(FIELDS)
    sources = dict(age_days='ORIGINAL_RECOMPUTED_STATISTICAL_SNAPSHOT',
        validation_confidence='ORIGINAL_GLOBAL_DSR_PROBABILITY',
        overfit_flag='ORIGINAL_PBO_AND_EXPLICIT_OVERFIT_FINDING',
        paper_confidence='BOUND_ATTESTED_FORWARD_REALIZATIONS_REQUIRED',
        decay_flag='ORIGINAL_CANDIDATE_MAINTENANCE_RECORD_REQUIRED',
        leakage_flag='ORIGINAL_CANDIDATE_LEAKAGE_ASSESSMENT_REQUIRED')
    as_of = None
    report_hash = evidence_hash = None
    if report is not None:
        require(report['schema'] in GOLD_REPORT_SCHEMAS, 'STATISTICAL_KIND')
        require(report['project_cut_sha256'] == project_cut['cut_sha256'], 'CUT_BINDING')
        require(report['selected_attempt'] is not None
            and all(report['selected_attempt'][k] == selected[k] for k in IDENTITY), 'CANDIDATE_BINDING')
        require(all(type(report['population'][k]) is int and report['population'][k] == project_cut[k]
                    for k in ('attempt_count', 'unique_definition_count'))
                and type(report['metrics']['n_trials']) is int
                and report['metrics']['n_trials'] == project_cut['attempt_count'], 'POPULATION_BINDING')
        as_of = time(report['as_of_ms'])
        require(checked_at_ms is None or time(checked_at_ms) >= as_of, 'FUTURE_REVIEW')
        dsr, pbo = (probability(report['metrics'][k]) for k in ('dsr', 'pbo'))
        values['age_days'] = '0'  # eval at its original retained snapshot, never a later consumption clock
        values['validation_confidence'] = None if dsr is None else canonical_decimal(dsr)
        explicit_overfit = any('OVERFIT_DETECTED' in reason or 'OVERFIT_CONFIRMED' in reason
                              for reason in report['reasons'])
        values['overfit_flag'] = True if explicit_overfit else None if pbo is None else pbo > Decimal('0.50')
        report_hash, evidence_hash = statistical_digest(report), statistical_digest(verified_statistics)
    causality_hash = None
    if causality_policy is not None:
        from quantos.contracts.gold_causality_policy import policy_sha256, REVIEW_SCHEMA, SCOPE
        causality_hash=policy_sha256(causality_policy)
        if causality_review is not None:
            p=causality_review
            require(p['schema']==REVIEW_SCHEMA and p['scope']==SCOPE
                and p['review_sha256']==digest({k:v for k,v in p.items() if k!='review_sha256'}), 'CAUSALITY_CHANGED')
            require(p['policy_sha256']==causality_hash and p['candidate_id']==candidate_id
                and p['native_result_hash']==native_result_hash
                and all(p[k]==selected[k] for k in IDENTITY), 'CAUSALITY_BINDING')
            require(all(p[k] is False for k in ('independent_verification','economic_authority',
                    'submission_enabled','synthetic_future_probes_are_market_evidence')), 'CAUSALITY_AUTHORITY')
            last=p['actual_last_decision_ms']
            require(last is None or checked_at_ms is None or time(last)<=time(checked_at_ms), 'CAUSALITY_FUTURE')
            require(p['status'] in ('PASS','FAIL','INSUFFICIENT'), 'CAUSALITY_STATUS')
            counts=('decision_count','assessed_decisions','component_evaluations','failed_components','unassessed_components')
            require(all(type(p[k]) is int and p[k]>=0 for k in counts)
                and p['assessed_decisions']<=p['decision_count'], 'CAUSALITY_COUNTS')
            require((p['status']=='PASS' and p['leakage_flag'] is False and p['decision_count']>0
                    and p['decision_count']==p['assessed_decisions'] and p['unassessed_components']==0
                    and p['failed_components']==0)
                or (p['status']=='FAIL' and p['leakage_flag'] is True and p['failed_components']>0)
                or (p['status']=='INSUFFICIENT' and p['leakage_flag'] is None and p['failed_components']==0),
                'CAUSALITY_VERDICT')
            # Known native signal leakage is conclusive. A scoped signal pass
            # cannot clear trial-selection or final-OOS leakage assessments.
            if p['leakage_flag'] is True:
                values['leakage_flag']=True
            sources['leakage_flag']='ORIGINAL_SIGNAL_CAUSALITY_PLUS_OOS_AND_SEARCH_SELECTION_REQUIRED'
    else:
        require(causality_review is None, 'CAUSALITY_POLICY_REQUIRED')
    net_forward_hash = None
    if net_forward_policy is not None:
        from quantos.contracts.gold_net_forward_policy import policy_sha256 as forward_hash, validate_review
        net_forward_hash=forward_hash(net_forward_policy)
        require(net_forward_policy['series']['candidate_id']==candidate_id,'FORWARD_CANDIDATE')
        if net_forward_review is not None:
            require(as_of is not None,'FORWARD_ORIGINAL_SNAPSHOT_REQUIRED')
            review=validate_review(net_forward_review,policy=net_forward_policy,subject=subject,
                candidate_id=candidate_id,native_result_hash=native_result_hash,
                project_cut_sha256=project_cut['cut_sha256'],snapshot_as_of_ms=as_of)
            if review['status']=='MATURE':
                values['paper_confidence']=review['paper_confidence']
            sources['paper_confidence']='ORIGINAL_COST_NET_EIGHT_WEEK_FORWARD_MATURITY_NOT_PROFIT_PROBABILITY'
    else:
        require(net_forward_review is None,'FORWARD_POLICY_REQUIRED')
    maintenance_hash=None
    if maintenance_policy is not None:
        from quantos.contracts.gold_net_maintenance_policy import policy_sha256 as maintenance_digest, validate_review as validate_maintenance
        require(net_forward_policy is not None,'MAINTENANCE_FORWARD_POLICY_REQUIRED')
        maintenance_hash=maintenance_digest(maintenance_policy,net_forward_policy=net_forward_policy)
        if maintenance_review is not None:
            require(net_forward_review is not None and as_of is not None,'MAINTENANCE_ORIGINAL_FORWARD_REQUIRED')
            review=validate_maintenance(maintenance_review,policy=maintenance_policy,net_forward_policy=net_forward_policy,
                net_forward_review=net_forward_review,subject=subject,candidate_id=candidate_id,
                native_result_hash=native_result_hash,project_cut_sha256=project_cut['cut_sha256'],snapshot_as_of_ms=as_of)
            values['decay_flag']=review['decay_flag']
            sources['decay_flag']='ORIGINAL_PREDECLARED_COST_NET_WEEKLY_PERFORMANCE_MAINTENANCE_NOT_IC'
    else:
        require(maintenance_review is None,'MAINTENANCE_POLICY_REQUIRED')
    facts = {k: dict(status='UNAVAILABLE' if values[k] is None else 'AVAILABLE', value=values[k],
                    source=sources[k]) for k in FIELDS}
    value = dict(schema='QUANTOS_GOLD_MODEL_RISK_INPUTS_V4' if maintenance_policy is not None else
        'QUANTOS_GOLD_MODEL_RISK_INPUTS_V3' if net_forward_policy is not None else
        'QUANTOS_GOLD_MODEL_RISK_INPUTS_V2' if causality_policy is not None else SCHEMA, status='PARTIAL_ORIGINAL_FACTS',
        **selected, candidate_id=candidate_id, native_result_hash=native_result_hash,
        project_cut_sha256=hashed(project_cut['cut_sha256']),
        statistical_report_sha256=report_hash, statistical_evidence_sha256=evidence_hash,
        snapshot_as_of_ms=as_of, scope='CANDIDATE_SNAPSHOT_REVIEW_ONLY_NOT_PAST_PRETRADE',
        attempt_count=project_cut['attempt_count'], unique_definition_count=project_cut['unique_definition_count'],
        facts=facts, known_fields=sorted(k for k in FIELDS if values[k] is not None),
        missing_fields=sorted(k for k in FIELDS if values[k] is None),
        known_failure_fields=sorted(k for k in ('decay_flag','overfit_flag','leakage_flag') if values[k] is True),
        metadata_kind='ONE_SELECTED_CANDIDATE_NOT_REPLICATED_COMPONENT_CONFIDENCE',
        independent_verification=False, limitations=[
            'NO_INDEPENDENT_POLICY_SOURCE_OR_TIME_AUTHORITY',
            'FORWARD_AND_CANDIDATE_MAINTENANCE_ORIGINALS_NOT_YET_BOUND',
            'NO_ORIGINAL_CANDIDATE_LEAKAGE_ASSESSMENT',
            'REVIEW_SNAPSHOT_MODEL_FACTS_DO_NOT_AUTHORIZE_PAST_PRETRADE'],
        economic_authority=False, submission_enabled=False)
    if causality_policy is not None:
        value.update(causality_policy_sha256=causality_hash,causality_review=causality_review)
        value['limitations'].append('OOS_AND_SEARCH_SELECTION_LEAKAGE_NOT_YET_ASSESSED')
        if causality_review is not None:
            value['limitations'].extend(causality_review['limitations'])
        else:
            value['limitations'].append('NATIVE_CAUSALITY_PORT_OR_ORIGINALS_UNAVAILABLE')
        value['limitations']=sorted(set(value['limitations']))
    if net_forward_policy is not None:
        value.update(net_forward_policy_sha256=net_forward_hash,net_forward_review=net_forward_review)
        if net_forward_review is None:
            value['limitations'].append('NET_FORWARD_PORT_OR_ORIGINALS_UNAVAILABLE')
        else:
            value['limitations'].extend(net_forward_review['limitations'])
            value['limitations'].extend(net_forward_review['reasons'])
            if net_forward_review['status']=='MATURE':
                value['limitations'].remove('FORWARD_AND_CANDIDATE_MAINTENANCE_ORIGINALS_NOT_YET_BOUND')
                value['limitations'].append('CANDIDATE_MAINTENANCE_ORIGINALS_NOT_YET_BOUND')
        value['limitations']=sorted(set(value['limitations']))
    if maintenance_policy is not None:
        value.update(maintenance_policy_sha256=maintenance_hash,maintenance_review=maintenance_review)
        if maintenance_review is None:
            value['limitations'].append('NET_MAINTENANCE_PORT_OR_ORIGINALS_UNAVAILABLE')
        else:
            value['limitations'].extend(maintenance_review['limitations'])
            value['limitations'].extend(maintenance_review['reasons'])
            if maintenance_review['decay_flag'] is not None:
                for missing in ('FORWARD_AND_CANDIDATE_MAINTENANCE_ORIGINALS_NOT_YET_BOUND',
                                'CANDIDATE_MAINTENANCE_ORIGINALS_NOT_YET_BOUND'):
                    if missing in value['limitations']:
                        value['limitations'].remove(missing)
                if values['paper_confidence'] is None:
                    value['limitations'].append('ORIGINAL_EIGHT_WEEK_FORWARD_MATURITY_REQUIRED')
        value['limitations']=sorted(set(value['limitations']))
    value['projection_sha256'] = digest(value)
    return GoldModelRiskInputs(value)
