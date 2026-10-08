"""Finite future-signal terms, distinct from an executable issuer policy.

This verifies a public artifact's structure and signature, not a human identity,
installation, strategy economics or a future decision proof. Original policy
parsers deliberately reject this artifact. A protected installation and an
independent per-decision proof consumer are still required before delegation
can participate in execution; no policy, approval, reservation or key is made.
Installation must independently compare the declared pilot and budget binding
with original protected records; a self-consistent signed copy is insufficient
to prove history continuity or authorize a replacement baseline.
"""
import re
from copy import deepcopy

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from live.execution.demo_gold_pilot import config_check
from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import (
    GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA, budget_group_binding, current_key,
    require, validate_policy,
)
from services.validation import demo_submission as submission

SCHEMA='DEMO_GOLD_EXECUTION_DELEGATION_V1'
DOMAIN=b'QUANTOS_DEMO_GOLD_EXECUTION_DELEGATION_V1\x00'
SCHEMA_V2='DEMO_GOLD_EXECUTION_DELEGATION_V2'
DOMAIN_V2=b'QUANTOS_DEMO_GOLD_EXECUTION_DELEGATION_V2\x00'
TERMS=('schema purpose confirmation operator_approval_sha256 confirmed_at_ms '
    'original_budget_group_binding_sha256 runtime_sha256 strategy_source_commit '
    'trial_manifest_sha256 pilot_request_sha256 pilot_configuration opening_until_ms '
    'exit_until_ms max_holding_ms entry_timeout_ms')
TERMS_V2=TERMS+' active_budget_group_binding_sha256'
INTENT_FIELDS=' '.join(name for name in TERMS_V2.split() if name not in
    ('schema','purpose','confirmation','operator_approval_sha256','confirmed_at_ms'))


def _body_check(value):
    future=type(value) is dict and value.get('schema')==SCHEMA_V2
    submission._fields(value,'schema '+('future_template continuity_event_sha256' if future else 'policy_template')+' terms automatic_renewal '
        'strategy_changes_allowed economic_authority submission_enabled signature_base64url')
    require(value['schema']==(SCHEMA_V2 if future else SCHEMA) and all(value[key] is False for key in
        ('automatic_renewal','strategy_changes_allowed','economic_authority','submission_enabled')),
        'GOLD_DELEGATION_SCOPE')
    if future:
        from live.execution.demo_gold_future_template import validate_template, template_group_binding
        policy=validate_template(value['future_template'])
        submission._digest(value['continuity_event_sha256'])
        binding=template_group_binding(policy)
    else:
        policy=value['policy_template'];validate_policy(policy)
        require(policy['schema']==GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA,'GOLD_DELEGATION_EXIT_RESERVE_REQUIRED')
        binding=budget_group_binding(policy)
    terms=value['terms'];submission._fields(terms,TERMS_V2 if future else TERMS)
    require(terms['schema']==('DEMO_GOLD_EXECUTION_DELEGATION_TERMS_V2' if future else 'DEMO_GOLD_EXECUTION_DELEGATION_TERMS_V1')
        and terms['purpose']=='FIXED_BAR_RULES_FUTURE_SIGNALS_WITH_INDEPENDENT_PROOF'
        and terms['confirmation']=='AUTHORIZE_BOUNDED_FUTURE_SIGNALS_AND_RESERVED_EXITS',
        'GOLD_DELEGATION_EXPLICIT_CONFIRMATION')
    for name in (TERMS_V2 if future else TERMS).split():
        if name.endswith('_sha256'):submission._digest(terms[name])
    require(type(terms['strategy_source_commit']) is str
        and re.fullmatch('[a-f0-9]{40}',terms['strategy_source_commit']) is not None,
        'GOLD_DELEGATION_STRATEGY_SOURCE')
    require(terms['active_budget_group_binding_sha256' if future else 'original_budget_group_binding_sha256']==binding,
        'GOLD_DELEGATION_BUDGET_CHANGED')
    pilot=terms['pilot_configuration'];config_check(pilot)
    require(pilot['account_ref_sha256']==policy['account_ref_sha256'],'GOLD_DELEGATION_PILOT_ACCOUNT')
    if future:
        require(policy['pilot']==dict(request_sha256=terms['pilot_request_sha256'],
            configuration_sha256=_digest(pilot),opening_origin_sha256=pilot['opening_origin_sha256'],
            started_at_ms=pilot['started_at_ms'],ends_at_ms=pilot['ends_at_ms']),
            'GOLD_DELEGATION_PILOT_TEMPLATE')
    for name in ('confirmed_at_ms','opening_until_ms','exit_until_ms','max_holding_ms','entry_timeout_ms'):
        submission._integer(terms[name])
    require(terms['entry_timeout_ms']==5000 and 0<terms['max_holding_ms']<=86400000,
        'GOLD_DELEGATION_SIGNAL_OR_HOLDING')
    require(policy['valid_from_ms']<=pilot['started_at_ms']<=terms['confirmed_at_ms']
        <terms['opening_until_ms']<=pilot['ends_at_ms']-terms['entry_timeout_ms']
        and pilot['ends_at_ms']<=terms['exit_until_ms']<=policy['valid_until_ms'],
        'GOLD_DELEGATION_ORIGINAL_WINDOW')
    submission._canonical(value)
    return value


def signing_payload(value):
    """Bytes for a separate controlled signer; old Scope signatures never fit."""
    _body_check(value)
    domain=DOMAIN_V2 if value['schema']==SCHEMA_V2 else DOMAIN
    return domain+submission._canonical({k:v for k,v in value.items() if k!='signature_base64url'})


def delegation_base(value):
    """Strictly parse the declared base; no signature, custody or send authority."""
    _body_check(value)
    return value['future_template' if value['schema']==SCHEMA_V2 else 'policy_template']


def _continuity_check(value, event):
    require(type(event) is dict,'GOLD_DELEGATION_CONTINUITY_REQUIRED')
    from live.execution.demo_issuer_group_continuity import verify_group_change_history

    checked=verify_group_change_history(event)
    terms=value['terms'];template=value['future_template']
    require(checked['event_sha256']==value['continuity_event_sha256']==event['record_hash']
        and checked['future_template']==template
        and checked['future_template_sha256']==_digest(template)
        and checked['from_group_binding_sha256']==terms['original_budget_group_binding_sha256']
        and checked['to_group_binding_sha256']==terms['active_budget_group_binding_sha256']
        and checked['pilot']==template['pilot']
        and checked['event_at_ms']<=terms['confirmed_at_ms'],
        'GOLD_DELEGATION_CONTINUITY_BINDING')
    return checked


def build_delegation_v2(intent, *, future_template, confirmation, continuity_event):
    """Build unsigned public material after a separately verified human ceremony.

    The controlled signer must independently verify fixed-file custody, the
    complete subject/operation binding and actual original-store ancestry. This
    helper never authenticates an operator or creates a confirmation.
    """
    submission._fields(intent,INTENT_FIELDS)
    submission._fields(confirmation,'schema purpose subject_sha256 operator_id approved_at_ms '
        'consumer_enabled submission_enabled economic_authority')
    require(confirmation['schema']=='GOLD_FUTURE_BUDGET_OPERATOR_CONFIRMATION_V1'
        and confirmation['purpose']=='DELEGATION_V2_SIGN'
        and type(confirmation['operator_id']) is str and 0<len(confirmation['operator_id'])<=128
        and all(confirmation[k] is False for k in ('consumer_enabled','submission_enabled','economic_authority')),
        'GOLD_DELEGATION_OPERATOR_PURPOSE')
    submission._digest(confirmation['subject_sha256'])
    at=submission._integer(confirmation['approved_at_ms'])
    require(at>0,'GOLD_DELEGATION_OPERATOR_TIME')
    require(type(continuity_event) is dict and 'record_hash' in continuity_event,
        'GOLD_DELEGATION_CONTINUITY_REQUIRED')
    value=dict(schema=SCHEMA_V2,future_template=deepcopy(future_template),
        continuity_event_sha256=continuity_event['record_hash'],
        terms=dict(deepcopy(intent),schema='DEMO_GOLD_EXECUTION_DELEGATION_TERMS_V2',
            purpose='FIXED_BAR_RULES_FUTURE_SIGNALS_WITH_INDEPENDENT_PROOF',
            confirmation='AUTHORIZE_BOUNDED_FUTURE_SIGNALS_AND_RESERVED_EXITS',
            operator_approval_sha256=_digest(confirmation),confirmed_at_ms=at),
        automatic_renewal=False,strategy_changes_allowed=False,economic_authority=False,
        submission_enabled=False,signature_base64url='')
    _body_check(value)
    _continuity_check(value,continuity_event)
    return value


def verify(value,*,registry,source_commit,account_ref_sha256,now_ms,continuity_event=None):
    """Check current supplied trust material; no key lookup or installation."""
    _body_check(value)
    submission._integer(now_ms)
    future=value['schema']==SCHEMA_V2
    policy=value['future_template' if future else 'policy_template'];terms=value['terms']
    require(type(source_commit) is str and policy['executor_release_commit']==source_commit
        and type(account_ref_sha256) is str and policy['account_ref_sha256']==account_ref_sha256,
        'GOLD_DELEGATION_EXECUTION_IDENTITY')
    if future:
        from live.execution.demo_gold_future_template import current_template_key
        _continuity_check(value,continuity_event)
        key=current_template_key(policy,registry,now_ms)
    else:
        require(continuity_event is None,'GOLD_DELEGATION_UNEXPECTED_CONTINUITY')
        key=current_key(policy,registry,now_ms)
    require(key['valid_from_ms']<=terms['confirmed_at_ms']
        and terms['exit_until_ms']<=key['valid_until_ms'],'GOLD_DELEGATION_KEY_WINDOW')
    require(terms['confirmed_at_ms']<=now_ms<terms['exit_until_ms'],'GOLD_DELEGATION_EXPIRED_OR_FUTURE')
    public=Ed25519PublicKey.from_public_bytes(submission._decode(key['public_key_base64url'],32))
    try:
        public.verify(submission._decode(value['signature_base64url'],64),signing_payload(value))
    except InvalidSignature:
        raise ValueError('GOLD_DELEGATION_SIGNATURE') from None
    return value
