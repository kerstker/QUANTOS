"""Non-executable first-day Demo scope; order approval stays in the issuer.

The signature authenticates material integrity under a supplied registry key.
It does not authenticate the human, source authority or installation. Those
require the original protected operator workflow and independent review.
"""
from copy import deepcopy
import re
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import GOLD_POLICY_SCHEMA, require, validate_policy
from services.validation import demo_submission as submission

SCHEMA = 'DEMO_GOLD_STRATEGY_BUDGET_SCOPE_V1'
BUNDLE_SCHEMA = 'DEMO_GOLD_BUDGET_SCOPE_BUNDLE_V1'
DAY_SECONDS = 86400
INSTALLATION_ALLOWANCE_SECONDS = 300
DOMAIN = b'QUANTOS_DEMO_GOLD_BUDGET_SCOPE_V1\x00'
SCHEMA_V2 = 'DEMO_GOLD_STRATEGY_BUDGET_SCOPE_V2'
BUNDLE_SCHEMA_V2 = 'DEMO_GOLD_BUDGET_SCOPE_BUNDLE_V2'
DOMAIN_V2 = b'QUANTOS_DEMO_GOLD_BUDGET_SCOPE_V2\x00'
COMMON = ('schema environment policy_id deployment_sha256 registry_sha256 executor_release_commit '
    'account_ref_sha256 authority_id key_id valid_from_ms valid_until_ms grant_ttl_ms max_grants '
    'max_orders max_total_notional max_cancel_grants max_cancel_orders scope bindings').split()
BUDGET = ('max_grants max_orders max_total_notional max_cancel_grants max_cancel_orders').split()
ENROLLMENT = ('schema purpose request_sha256 operator_approval_sha256 original_registry_sha256 '
    'original_policy_sha256 original_profile_sha256 original_cycle_sha256 original_cycle_review_sha256 '
    'full_state_sha256 runtime_sha256 strategy_definition_sha256 stop_image_sha256 confirmed_at_ms duration_seconds '
    'installation_allowance_seconds installation_deadline_ms pilot_max_loss_fraction '
    'execution_approval_granted pilot_installation_verified submission_enabled economic_authority').split()
ENROLLMENT_V2 = ENROLLMENT + ('original_source_commit original_setup_sha256 original_spec_sha256 '
    'partition_binding_sha256 target_declarations_sha256 original_issuer_history').split()


def is_scope(value):
    """Finite schema classification only; this does not validate or grant authority."""
    return type(value) is dict and value.get('schema') in (SCHEMA, SCHEMA_V2)


def _declaration_view(scope):
    """Private structural view for unchanged native declaration checks only."""
    result = {key: deepcopy(scope[key]) for key in COMMON}
    result['schema'] = GOLD_POLICY_SCHEMA
    return result


def validate_scope(scope):
    _fields(scope, ' '.join(COMMON)+' budget_group scope_enrollment signature_base64url')
    require(is_scope(scope), 'GOLD_SCOPE_SCHEMA')
    validate_policy(_declaration_view(scope))
    group = scope['budget_group']
    _fields(group, 'group_id strategy_definition_sha256 valid_from_ms valid_until_ms '+' '.join(BUDGET))
    submission._identifier(group['group_id']);submission._digest(group['strategy_definition_sha256'])
    require(all(type(group[key]) is type(scope[key]) and group[key] == scope[key]
        for key in ('valid_from_ms', 'valid_until_ms', *BUDGET)), 'GOLD_SCOPE_GROUP_LIMITS')
    v2=scope['schema']==SCHEMA_V2
    enrollment = scope['scope_enrollment'];fields=ENROLLMENT_V2 if v2 else ENROLLMENT
    _fields(enrollment, ' '.join(fields))
    require(enrollment['schema'] == ('DEMO_GOLD_FIRST_SCOPE_BINDING_V1' if v2 else 'DEMO_GOLD_BUDGET_SCOPE_BINDING_V1')
        and enrollment['purpose'] == ('FIRST_GOLD_SCOPE_ONLY' if v2 else 'DEMO_GOLD_FIRST_DAY_FUNCTIONAL_PILOT_SCOPE_ONLY'),
        'GOLD_SCOPE_PURPOSE')
    if v2:
        from live.execution.demo_gold_first_scope_history import validate_anchor
        validate_anchor(enrollment['original_issuer_history'])
        require(type(enrollment['original_source_commit']) is str
            and re.fullmatch('[a-f0-9]{40}', enrollment['original_source_commit']), 'GOLD_SCOPE_ORIGINAL_SOURCE')
    for key in fields:
        if key.endswith('_sha256'): submission._digest(enrollment[key])
    for key in ('confirmed_at_ms', 'duration_seconds', 'installation_allowance_seconds', 'installation_deadline_ms'):
        submission._integer(enrollment[key])
    at = enrollment['confirmed_at_ms']
    require(enrollment['duration_seconds'] == DAY_SECONDS
        and enrollment['installation_allowance_seconds'] == INSTALLATION_ALLOWANCE_SECONDS
        and scope['valid_from_ms'] == at
        and enrollment['installation_deadline_ms'] == at+INSTALLATION_ALLOWANCE_SECONDS*1000
        and scope['valid_until_ms'] == enrollment['installation_deadline_ms']+DAY_SECONDS*1000,
        'GOLD_SCOPE_FIXED_WINDOW')
    require(enrollment['pilot_max_loss_fraction'] == '0.05'
        and enrollment['strategy_definition_sha256'] == group['strategy_definition_sha256']
        and all(enrollment[key] is False for key in ('execution_approval_granted',
            'pilot_installation_verified', 'submission_enabled', 'economic_authority')), 'GOLD_SCOPE_NO_EXECUTION')
    submission._decode(scope['signature_base64url'], 64)
    return scope


def signed_bytes(scope):
    require(is_scope(scope), 'GOLD_SCOPE_SCHEMA')
    body = {key:value for key,value in scope.items() if key != 'signature_base64url'}
    return (DOMAIN_V2 if scope['schema']==SCHEMA_V2 else DOMAIN)+submission._canonical(body)


def historical_scope_binding(scope, registry):
    """Verify retained scope integrity for observation, never current permission."""
    validate_scope(scope);submission.validate_registry(registry)
    require(scope['deployment_sha256'] == registry['deployment_sha256']
        and scope['registry_sha256'] == submission.registry_sha256(registry), 'GOLD_SCOPE_REGISTRY')
    keys = [key for key in registry['keys'] if (key['authority_id'], key['key_id'])
        == (scope['authority_id'], scope['key_id'])]
    require(len(keys) == 1 and keys[0] == registry['keys'][-1] and keys[0]['status'] == 'ACTIVE'
        and keys[0]['valid_from_ms'] == scope['valid_from_ms']
        and keys[0]['valid_until_ms'] == scope['valid_until_ms'], 'GOLD_SCOPE_KEY')
    require(all(key['valid_until_ms'] <= scope['valid_from_ms']
        and key['public_key_sha256'] != keys[0]['public_key_sha256'] for key in registry['keys'][:-1]),
        'GOLD_SCOPE_INDEPENDENT_KEY_REQUIRED')
    public = Ed25519PublicKey.from_public_bytes(submission._decode(keys[0]['public_key_base64url'], 32))
    try: public.verify(submission._decode(scope['signature_base64url'], 64), signed_bytes(scope))
    except InvalidSignature: raise ValueError('GOLD_SCOPE_SIGNATURE') from None
    return keys[0]


def current_scope_key(scope, registry, now_ms):
    key = historical_scope_binding(scope, registry);submission._integer(now_ms)
    require(scope['valid_from_ms'] <= now_ms < scope['valid_until_ms'], 'GOLD_SCOPE_EXPIRED')
    return key


def budget_binding(scope):
    validate_scope(scope)
    return _digest({**{name:scope[name] for name in ('budget_group', 'account_ref_sha256',
        'deployment_sha256', 'registry_sha256', 'executor_release_commit', 'authority_id', 'key_id', 'scope')},
        'bindings':scope['bindings']})


def pilot_window(scope, *, registry, now_ms):
    """Review a prospective start without installing or resetting a baseline."""
    current_scope_key(scope, registry, now_ms)
    require(now_ms <= scope['scope_enrollment']['installation_deadline_ms'], 'GOLD_SCOPE_INSTALLATION_EXPIRED')
    return dict(started_at_ms=now_ms, ends_at_ms=now_ms+DAY_SECONDS*1000,
        scope_valid_until_ms=scope['valid_until_ms'], installed=False, started=False,
        submission_enabled=False, economic_authority=False)
