"""Pure common issuer declaration checks, with no executable-schema admission.

Outer policy/template validators own their exact key sets. These helpers do not
accept an authorization schema or establish installation, human or proof custody.
"""
from decimal import Decimal
import re
from live.execution.demo_batch_risk import _fields
from services.validation import demo_submission as submission

class IssuerRejected(ValueError):
    pass


def require(condition,reason):
    if not condition:raise IssuerRejected(reason)


def amount(value):
    require(type(value) is str and re.fullmatch(r'\d{1,20}(\.\d{1,18})?',value) is not None,
            'ISSUER_EXACT_AMOUNT')
    result=Decimal(value)
    require(result>0,'ISSUER_POSITIVE_AMOUNT')
    return result


def check_common_fields(value, *, identifier_field, gold_only):
    require(value['environment']=='BINANCE_USDM_DEMO','ISSUER_POLICY_SCOPE')
    for name in (identifier_field,'authority_id','key_id'):submission._identifier(value[name])
    for name in ('deployment_sha256','registry_sha256','account_ref_sha256'):submission._digest(value[name])
    require(type(value['executor_release_commit']) is str and re.fullmatch('[a-f0-9]{40}',value['executor_release_commit']) is not None,'ISSUER_POLICY_RELEASE')
    for name in ('valid_from_ms','valid_until_ms','grant_ttl_ms','max_grants','max_orders','max_cancel_grants','max_cancel_orders'):
        submission._integer(value[name])
    require(0<value['valid_until_ms']-value['valid_from_ms']<=7*86400000 and
            0<value['grant_ttl_ms']<=60000,'ISSUER_POLICY_TIME')
    for name in ('max_grants','max_cancel_grants'):require(0<value[name]<=4096,'ISSUER_POLICY_BUDGET')
    for name in ('max_orders','max_cancel_orders'):require(0<value[name]<=50000,'ISSUER_POLICY_BUDGET')
    amount(value['max_total_notional'])
    scope=value['scope'];_fields(scope,'symbol sides time_in_force allow_opening max_quantity_per_order max_notional_per_order max_notional_per_grant')
    require(scope['symbol'] in (('XAUUSDT',) if gold_only else ('BTCUSDC','BTCUSDT')) and type(scope['allow_opening']) is bool,'ISSUER_POLICY_ORDER_SCOPE')
    for name,allowed in (('sides',{'BUY','SELL'}),('time_in_force',{'GTX','IOC'})):
        choices=scope[name]
        require(type(choices) is list and 1<=len(choices)<=2 and all(type(v) is str for v in choices) and
                len(set(choices))==len(choices) and set(choices)<=allowed,'ISSUER_POLICY_ORDER_SCOPE')
    for name in ('max_quantity_per_order','max_notional_per_order','max_notional_per_grant'):amount(scope[name])
    bindings=value['bindings']
    submission._identifier(bindings['strategy_id'])
    for name in ('mandate_sha256','validation_sha256','native_limits_sha256','strategy_approval_sha256'):
        submission._digest(bindings[name])


def check_budget_group(value, *, reserved):
    group=value['budget_group']
    _fields(group,'group_id strategy_definition_sha256 valid_from_ms valid_until_ms max_grants max_orders '
        'max_total_notional max_cancel_grants max_cancel_orders'+(' exit_reserve' if reserved else ''))
    submission._identifier(group['group_id']);submission._digest(group['strategy_definition_sha256'])
    for name in ('valid_from_ms','valid_until_ms','max_grants','max_orders','max_cancel_grants','max_cancel_orders'):
        submission._integer(group[name])
    require(0<group['valid_until_ms']-group['valid_from_ms']<=7*86400000 and
        group['valid_from_ms']<=value['valid_from_ms']<value['valid_until_ms']<=group['valid_until_ms'],
        'ISSUER_GROUP_TIME')
    for name in ('max_grants','max_cancel_grants'):require(0<group[name]<=4096,'ISSUER_GROUP_COUNT')
    for name in ('max_orders','max_cancel_orders'):require(0<group[name]<=50000,'ISSUER_GROUP_COUNT')
    amount(group['max_total_notional'])
    if reserved:
        reserve=group['exit_reserve'];_fields(reserve,'grants orders notional')
        for name in ('grants','orders'):
            submission._integer(reserve[name])
            require(0<reserve[name]<group['max_'+name],'ISSUER_EXIT_RESERVE_LIMIT')
        require(amount(reserve['notional'])<amount(group['max_total_notional']),
            'ISSUER_EXIT_RESERVE_LIMIT')

def group_binding_fields(value):
    return {**{name:value[name] for name in ('budget_group','account_ref_sha256','deployment_sha256',
        'registry_sha256','executor_release_commit','authority_id','key_id','scope')},
        'bindings':{k:v for k,v in value['bindings'].items() if k!='execution_constraints_sha256'}}


def match_current_key(policy,registry,now_ms):
    submission.validate_registry(registry);submission._integer(now_ms)
    require(policy['valid_from_ms']<=now_ms<policy['valid_until_ms'],'ISSUER_POLICY_EXPIRED')
    require(policy['deployment_sha256']==registry['deployment_sha256'] and
            policy['registry_sha256']==submission.registry_sha256(registry),'ISSUER_POLICY_REGISTRY')
    keys=[k for k in registry['keys'] if (k['authority_id'],k['key_id'])==(policy['authority_id'],policy['key_id'])]
    require(len(keys)==1 and keys[0]['status']=='ACTIVE','ISSUER_KEY_UNAVAILABLE')
    key=keys[0]
    require(key['valid_from_ms']<=now_ms<key['valid_until_ms'],'ISSUER_KEY_EXPIRED')
    return key


def match_historical_binding(policy,registry):
    """Identity checks for read-only recovery; no active key or send authority."""
    submission.validate_registry(registry)
    require(policy['deployment_sha256']==registry['deployment_sha256'] and
        policy['registry_sha256']==submission.registry_sha256(registry),'ISSUER_POLICY_REGISTRY')
    keys=[k for k in registry['keys'] if (k['authority_id'],k['key_id'])==(policy['authority_id'],policy['key_id'])]
    require(len(keys)==1,'ISSUER_KEY_UNAVAILABLE')


