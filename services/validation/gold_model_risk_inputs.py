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
                             checked_at_ms, native_result_hash):
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
    facts = {k: dict(status='UNAVAILABLE' if values[k] is None else 'AVAILABLE', value=values[k],
                    source=sources[k]) for k in FIELDS}
    value = dict(schema=SCHEMA, status='PARTIAL_ORIGINAL_FACTS',
        **selected, candidate_id=candidate_id, native_result_hash=native_result_hash,
        project_cut_sha256=hashed(project_cut['cut_sha256']),
        statistical_report_sha256=report_hash, statistical_evidence_sha256=evidence_hash,
        snapshot_as_of_ms=as_of, scope='CANDIDATE_SNAPSHOT_REVIEW_ONLY_NOT_PAST_PRETRADE',
        attempt_count=project_cut['attempt_count'], unique_definition_count=project_cut['unique_definition_count'],
        facts=facts, known_fields=sorted(k for k in FIELDS if values[k] is not None),
        missing_fields=sorted(k for k in FIELDS if values[k] is None),
        known_failure_fields=['overfit_flag'] if values['overfit_flag'] is True else [],
        metadata_kind='ONE_SELECTED_CANDIDATE_NOT_REPLICATED_COMPONENT_CONFIDENCE',
        independent_verification=False, limitations=[
            'NO_INDEPENDENT_POLICY_SOURCE_OR_TIME_AUTHORITY',
            'FORWARD_AND_CANDIDATE_MAINTENANCE_ORIGINALS_NOT_YET_BOUND',
            'NO_ORIGINAL_CANDIDATE_LEAKAGE_ASSESSMENT',
            'REVIEW_SNAPSHOT_MODEL_FACTS_DO_NOT_AUTHORIZE_PAST_PRETRADE'],
        economic_authority=False, submission_enabled=False)
    value['projection_sha256'] = digest(value)
    return GoldModelRiskInputs(value)
