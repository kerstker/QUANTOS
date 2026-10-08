"""Review a deployment-owned Demo handoff against an installed finite policy.

Pure checks over declared inputs. The runtime establishes custody and a private
root-owned handoff; this module cannot authenticate a caller, approve a human
mandate, sign, or replace the executor's fresh original-store checks.
"""
from decimal import Decimal
import json

from live.execution.demo_batch_risk import _digest, _fields, screen
from live.execution.demo_session_inputs import _limits, _mandate
from live.execution.demo_pre_submit import _require_origin, _venue_rules, _venue_account_cap, _strategy_exit_capacity
from live.execution import demo_position_context
from live.execution import demo_gold_pilot
from live.execution.demo_issuer_common import (IssuerRejected as IssuerRejected, require, amount, check_common_fields,
    check_budget_group, group_binding_fields, match_current_key, match_historical_binding)
from live.governance.validation_attestation import ValidationAttestation, require_validation_attestation
from quantos.contracts.killswitch import KillSwitchState
from services.execution.perpetual_ledger_v2 import ledger_decimal_context
from services.risk.pre_trade.context import StrategyApproval
from services.risk.pre_trade.kill_switch import KillSwitch
from services.validation import demo_submission as submission, demo_cancellation as cancellation
from services.validation import demo_strategy_constraints


GOLD_POLICY_SCHEMA='DEMO_GOLD_ISSUER_POLICY_V1'
GOLD_STRATEGY_POLICY_SCHEMA='DEMO_GOLD_STRATEGY_ISSUER_POLICY_V1'
GOLD_STRATEGY_BUDGET_POLICY_SCHEMA='DEMO_GOLD_STRATEGY_BUDGET_POLICY_V1'
GOLD_STRATEGY_BUDGET_POLICY_SCHEMA_V2='DEMO_GOLD_STRATEGY_BUDGET_POLICY_V2'
GOLD_BUDGET_POLICY_SCHEMAS=(GOLD_STRATEGY_BUDGET_POLICY_SCHEMA,GOLD_STRATEGY_BUDGET_POLICY_SCHEMA_V2)
GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA='DEMO_GOLD_STRATEGY_EXIT_BUDGET_POLICY_V1'
GOLD_GROUP_POLICY_SCHEMAS=(*GOLD_BUDGET_POLICY_SCHEMAS,GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA)
GOLD_STRATEGY_POLICY_SCHEMAS=(GOLD_STRATEGY_POLICY_SCHEMA,*GOLD_GROUP_POLICY_SCHEMAS)


def validate_policy(policy):
    grouped=type(policy) is dict and policy.get('schema') in GOLD_GROUP_POLICY_SCHEMAS
    reserved=grouped and policy['schema']==GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA
    first_scope=grouped and policy['schema']==GOLD_STRATEGY_BUDGET_POLICY_SCHEMA_V2
    _fields(policy,'schema environment policy_id deployment_sha256 registry_sha256 executor_release_commit '
        'account_ref_sha256 authority_id key_id valid_from_ms valid_until_ms grant_ttl_ms max_grants '
        'max_orders max_total_notional max_cancel_grants max_cancel_orders scope bindings'+(' budget_group' if grouped else '')+
        (' original_scope' if first_scope else ''))
    require(policy['schema'] in ('DEMO_ISSUER_POLICY_V1',GOLD_POLICY_SCHEMA,*GOLD_STRATEGY_POLICY_SCHEMAS) and policy['environment']=='BINANCE_USDM_DEMO','ISSUER_POLICY_SCOPE')
    _fields(policy['bindings'],'strategy_id mandate_sha256 validation_sha256 native_limits_sha256 strategy_approval_sha256'+
        (' execution_constraints_sha256' if policy['schema'] in GOLD_STRATEGY_POLICY_SCHEMAS else ''))
    check_common_fields(policy,identifier_field='policy_id',gold_only=policy['schema'] in (GOLD_POLICY_SCHEMA,*GOLD_STRATEGY_POLICY_SCHEMAS))
    bindings=policy['bindings'];_fields(bindings,'strategy_id mandate_sha256 validation_sha256 native_limits_sha256 strategy_approval_sha256'+
        (' execution_constraints_sha256' if policy['schema'] in GOLD_STRATEGY_POLICY_SCHEMAS else ''))
    submission._identifier(bindings['strategy_id'])
    for name in set(bindings)-{'strategy_id'}:submission._digest(bindings[name])
    if grouped:check_budget_group(policy,reserved=reserved)
    if first_scope:
        from live.execution.demo_gold_exact_scope import validate
        validate(policy)
    submission._canonical(policy)
    return policy


def submission_batch_schema(policy):
    validate_policy(policy)
    return submission.GOLD_BATCH_SCHEMA if policy['schema'] in (GOLD_POLICY_SCHEMA,*GOLD_STRATEGY_POLICY_SCHEMAS) else submission.LEGACY_BATCH_SCHEMA


def strategy_constraints_check(value, *, policy, orders, now_ms):
    required=policy['schema'] in GOLD_STRATEGY_POLICY_SCHEMAS
    require(required==(value is not None),'ISSUER_STRATEGY_CONSTRAINTS_SCOPE')
    if required:
        demo_strategy_constraints.validate(value,orders=orders,now_ms=now_ms)
        require(_digest(value)==policy['bindings']['execution_constraints_sha256'],
                'ISSUER_STRATEGY_CONSTRAINTS_BINDING')
        if policy['schema'] in GOLD_GROUP_POLICY_SCHEMAS:
            require(value['strategy_definition_sha256']==policy['budget_group']['strategy_definition_sha256'],
                    'ISSUER_GROUP_STRATEGY_BINDING')


def budget_group_binding(policy):
    """Immutable shared scope; exact decision IDs and per-policy limits vary."""
    from live.execution.demo_issuer_group_continuity import CANCEL_VIEW_SCHEMA,validate_cancel_view
    if policy['schema']==CANCEL_VIEW_SCHEMA:validate_cancel_view(policy)
    elif policy['schema'] not in GOLD_GROUP_POLICY_SCHEMAS:return None
    return _digest(group_binding_fields(policy))


def current_key(policy,registry,now_ms):
    validate_policy(policy)
    if policy['schema']==GOLD_STRATEGY_BUDGET_POLICY_SCHEMA_V2:
        from live.execution.demo_gold_exact_scope import current_binding
        current_binding(policy,registry,now_ms)
    return match_current_key(policy,registry,now_ms)


def historical_policy_binding(policy,registry):
    """Identity checks for read-only recovery; no active key or send authority."""
    validate_policy(policy)
    if policy['schema']==GOLD_STRATEGY_BUDGET_POLICY_SCHEMA_V2:
        from live.execution.demo_gold_exact_scope import historical_binding
        historical_binding(policy,registry)
    return match_historical_binding(policy,registry)


def handoff_proposal(handoff,purpose):
    _fields(handoff,'schema proposal_sha256 proposal_path proposal status submission_enabled')
    prefix='DEMO_EXECUTION_SESSION' if purpose=='submission' else 'DEMO_CANCELLATION_SESSION'
    require(handoff['schema']==prefix+'_PROPOSAL_V1' and handoff['status']=='WAITING_INDEPENDENT_GRANT' and
            handoff['submission_enabled'] is False,'ISSUER_HANDOFF_SCOPE')
    submission._digest(handoff['proposal_sha256'])
    # A path printed by the executor is metadata, never a file opened by issuer.
    require(type(handoff['proposal_path']) is str and 0<len(handoff['proposal_path'])<=4096,'ISSUER_HANDOFF_PATH')
    require(_digest(handoff['proposal'])==handoff['proposal_sha256'],'ISSUER_HANDOFF_DIGEST')
    return handoff['proposal']


def common_context(context,policy):
    for name in ('deployment_sha256','registry_sha256','account_ref_sha256'):
        require(context[name]==policy[name],'ISSUER_CONTEXT_POLICY')


def _risk_replay(proposal,policy,now_ms):
    risk=proposal['risk'];binding=risk['input_snapshot'];context=proposal['context']
    schema=submission_batch_schema(policy)
    expected_risk='DEMO_GOLD_EXACT_BATCH_RISK_V1' if schema==submission.GOLD_BATCH_SCHEMA else 'DEMO_EXACT_BATCH_RISK_V1'
    require(risk['schema']==expected_risk and risk['status']=='RISK_CHECKS_PASSED' and
            not risk['reasons'] and all(risk[k] is False for k in ('source_verified','submission_enabled','economic_authority')),'ISSUER_RISK_REJECTED')
    require(_digest(binding)==risk['risk_sha256']==context['risk_sha256'],'ISSUER_RISK_DIGEST')
    declared=binding['context'];at=declared['now']
    submission._integer(at)
    require(0<=now_ms-at<=30000 and binding['orders']==proposal['orders'],'ISSUER_RISK_TIME_OR_ORDERS')
    pinned=policy['bindings']
    require(_digest(binding['native_limits'])==pinned['native_limits_sha256'] and
            _digest(binding['strategy'])==pinned['strategy_approval_sha256'],'ISSUER_RISK_POLICY')
    strategy=StrategyApproval.model_validate(binding['strategy'],strict=True)
    require(strategy.strategy_id==proposal['strategy_id']==pinned['strategy_id'] and strategy.approved,'ISSUER_STRATEGY_SCOPE')
    stop=KillSwitchState.model_validate_json(json.dumps(binding['kill_switch']),strict=True)
    replay=screen(proposal['orders'],schema=schema,account=binding['account'],market=binding['market'],
        native_limits=_limits(binding['native_limits']),strategy=strategy,kill_switch=KillSwitch(stop),now_ms=at,
        **{name:declared[name] for name in ('recent_order_fingerprints','recent_timestamps','recent_order_statuses','plan_turnover')})
    for name in ('status','risk_sha256','input_snapshot','checks','subset_checks','reasons','batch_turnover_fraction'):
        require(replay[name]==risk[name],'ISSUER_RISK_REPLAY_MISMATCH')
    # The original state remains owned by executor; its final check reads it
    # again. Here its supplied bytes, origin and market must be exactly bound.
    require(_digest(proposal['state'])==context['recovery_sha256'],'ISSUER_RECOVERY_DIGEST')
    demo_gold_pilot.require_opening_allowed(proposal['orders'],proposal['state'].get('pilot_control'))
    origin=proposal['origin'];position=proposal['state'].get('position_context')
    require(_digest(origin)==context['origin_sha256'] and origin['account_ref_sha256']==context['account_ref_sha256'],'ISSUER_ORIGIN_DIGEST')
    require(_digest([r for r in origin['reads'] if r['kind'] in ('mark','book','rules','fees','brackets')])==context['market_sha256'],'ISSUER_MARKET_DIGEST')
    report=_require_origin(origin,now_ms=now_ms,position_context=position)
    _venue_rules(proposal['orders'],origin);_venue_account_cap(proposal['orders'],origin,position)
    data={r['kind']:r['data'] for r in origin['reads']}
    wallet=next(a for a in data['account_after']['assets'] if a['asset']==report['quote_asset'])
    account,market=binding['account'],binding['market']
    require(account['account_ref_sha256']==context['account_ref_sha256'] and account['quote_asset']==report['quote_asset'] and
            Decimal(account['cash'])==Decimal(wallet['availableBalance']) and Decimal(account['equity'])==Decimal(wallet['marginBalance']),
            'ISSUER_ACCOUNT_BINDING')
    if position is None:
        require(account['positions']==[],'ISSUER_POSITION_BINDING')
    else:
        expected=demo_position_context.validate_origin(origin,position,as_of_ms=now_ms)
        require(account['positions']==expected['positions'] and Decimal(account['day_pnl'])==Decimal(expected['day_pnl']),
                'ISSUER_POSITION_BINDING')
        require(Decimal(account['peak_equity'])>=max(Decimal(position['initial_equity']),Decimal(account['equity']),
                Decimal(position['peak_equity_floor'])),'ISSUER_PEAK_EQUITY_BINDING')
    demo_position_context.require_gold_cumulative_loss(proposal['orders'],account,position)
    require(market['symbol']==origin['symbol'] and Decimal(market['reference_price'])==Decimal(data['mark']['markPrice']) and
            market['observed_at_ms']==min(data['mark']['time'],data['book']['time']),'ISSUER_MARKET_BINDING')
    return at,min(origin['observed_at_ms'],market['observed_at_ms'])+30000


@ledger_decimal_context()
def review_submission(handoff,*,policy,registry,now_ms):
    return _review_submission(handoff,policy=policy,registry=registry,now_ms=now_ms,delegated=False)


@ledger_decimal_context()
def _review_submission(handoff,*,policy,registry,now_ms,delegated):
    key=current_key(policy,registry,now_ms);p=handoff_proposal(handoff,'submission')
    _fields(p,'schema context orders origin risk mandate validation state strategy_id submission_enabled economic_authority'+
        (' execution_constraints' if policy['schema'] in GOLD_STRATEGY_POLICY_SCHEMAS else '')+
        (' delegated_admission' if delegated else ''))
    constraints=demo_strategy_constraints.check_proposal(p,now_ms=now_ms)
    strategy_constraints_check(constraints,policy=policy,orders=p['orders'],now_ms=now_ms)
    expected='DEMO_GOLD_DELEGATED_PRE_SUBMISSION_PROPOSAL_V1' if delegated else 'DEMO_PRE_SUBMISSION_PROPOSAL_V1'
    require(p['schema']==expected and p['submission_enabled'] is False and p['economic_authority'] is False,'ISSUER_PROPOSAL_SCOPE')
    schema=submission_batch_schema(policy)
    context=submission.validate_context(p['context']);orders=submission.validate_orders(p['orders'],schema=schema);common_context(context,policy)
    require(context['batch_sha256']==submission.batch_sha256(orders,schema=schema),'ISSUER_BATCH_DIGEST')
    for name in ('mandate','validation'):
        require(_digest(p[name])==context[name+'_sha256']==policy['bindings'][name+'_sha256'],'ISSUER_APPROVED_BINDING')
    mandate=_mandate(p['mandate'])
    require(mandate.venue=='testnet' and mandate.exchange=='binance' and mandate.approved_by and
            all(name.upper() not in ('AUTO','AI','SYSTEM') for name in mandate.approved_by) and
            mandate.issued_at<=now_ms<mandate.expires_at,'ISSUER_MANDATE_SCOPE')
    require(set(p['validation'])=={p['strategy_id']},'ISSUER_VALIDATION_SCOPE')
    attestations={sid:ValidationAttestation(**value) for sid,value in p['validation'].items()}
    require(not require_validation_attestation(p['strategy_id'],attestations) and
            attestations[p['strategy_id']].attested_at<=now_ms,'ISSUER_VALIDATION_SCOPE')
    _,fresh_until=_risk_replay(p,policy,now_ms)
    if constraints is not None:
        binding=p['risk']['input_snapshot']
        _strategy_exit_capacity(orders,p['origin'],binding['market'],binding['native_limits']['max_participation_rate'])
    scope=policy['scope'];total=Decimal(0)
    for order in orders:
        require(order['symbol']==scope['symbol'] and order['side'] in scope['sides'] and
                order['timeInForce'] in scope['time_in_force'] and (scope['allow_opening'] or order['reduceOnly']),'ISSUER_ORDER_SCOPE')
        quantity,price=amount(order['quantity']),amount(order['price']);notional=quantity*price
        require(quantity<=amount(scope['max_quantity_per_order']) and notional<=amount(scope['max_notional_per_order']),'ISSUER_ORDER_BUDGET')
        total+=notional
    require(total<=amount(scope['max_notional_per_grant']),'ISSUER_GRANT_BUDGET')
    expiry=min(now_ms+policy['grant_ttl_ms'],policy['valid_until_ms'],key['valid_until_ms'],mandate.expires_at,fresh_until)
    if constraints is not None:expiry=min(expiry,constraints['valid_until_ms'])
    require(expiry>now_ms,'ISSUER_GRANT_EXPIRED')
    return {'purpose':'submission','proposal_sha256':handoff['proposal_sha256'],'context':context,
        'orders':orders,'commands':[],'issued_at_ms':now_ms,'expires_at_ms':expiry,'notional':format(total,'f')}


def check_delegated_snapshot(proposal):
    """Cross-bind an admitted proof to the original risk proposal snapshot.

    Pure comparison only; callers must independently verify ENABLE, replay the
    proof and apply current native risk/mandate checks. The executor proposal
    adds its verified position and pilot control to the four original journals.
    """
    proof = proposal["delegated_admission"]
    state = proposal["state"]
    original = proof["execution"]["state"]
    _fields(original, "reservations recovery oms wire")
    require(
        proposal["origin"] == proof["origin"]
        and all(state.get(name) == original[name] for name in original)
        and state.get("position_context") == proof["execution"]["context"]
        and state.get("pilot_control") == proof["result"]["decision"]["pilot_control"],
        "ISSUER_DELEGATED_SNAPSHOT",
    )


@ledger_decimal_context()
def review_delegated_submission(handoff, *, policy, registry, public_material, now_ms):
    """Independently replay enabled proof, then run every original exact gate.

    Installed base policy and public ENABLE material must come from the issuer's
    own custody loader. A controller-supplied effective policy is never an input.
    No executor archive path is opened by this reviewer.
    """
    from live.execution.demo_gold_delegated_admission import admit

    p = handoff_proposal(handoff, "submission")
    require(p.get("schema") == "DEMO_GOLD_DELEGATED_PRE_SUBMISSION_PROPOSAL_V1", "ISSUER_DELEGATED_PROPOSAL_REQUIRED")
    from live.execution.demo_gold_future_template import SCHEMA as FUTURE_SCHEMA,derive_exact_policy,template_group_binding
    future=policy.get('schema')==FUTURE_SCHEMA
    current_delegated_key(policy,registry,now_ms)
    require(future or policy['schema']==GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA,'ISSUER_DELEGATED_EXIT_BUDGET_REQUIRED')
    proof = p.get("delegated_admission")
    checked = admit(
        delegation=public_material["delegation"],
        enable=public_material,
        base_policy=policy,
        registry=registry,
        proof=proof,
        now_ms=now_ms,
    )
    identity = _digest(proof)
    effective = checked["effective_policy"]
    if future:
        expected=derive_exact_policy(policy,proof_sha256=identity,constraints=checked['constraints'],orders=checked['orders'],now_ms=now_ms)
        binding=template_group_binding(policy)
    else:
        expected=json.loads(json.dumps(policy,allow_nan=False))
        expected['policy_id']='gold-delegated-'+identity
        expected['bindings']['execution_constraints_sha256']=_digest(checked['constraints'])
        binding=budget_group_binding(policy)
    require(
        effective == expected
        and budget_group_binding(effective) == binding == checked["group_binding_sha256"],
        "ISSUER_DELEGATED_POLICY_DERIVATION",
    )
    require(
        checked["proof_sha256"] == identity
        and p["context"].get("delegated_admission_sha256") == identity
        and checked["constraints"] == p.get("execution_constraints")
        and checked["orders"] == p.get("orders")
        and checked["history_anchor"] == public_material["issuer_anchor"],
        "ISSUER_DELEGATED_PROPOSAL_BINDING",
    )
    check_delegated_snapshot(p)
    plan = _review_submission(handoff, policy=effective, registry=registry, now_ms=now_ms, delegated=True)
    until = submission._integer(checked["valid_until_ms"])
    plan["expires_at_ms"] = min(plan["expires_at_ms"], until)
    require(plan["expires_at_ms"] > now_ms, "ISSUER_DELEGATED_EXPIRED")
    compact = dict(
        schema="DEMO_GOLD_DELEGATED_ISSUER_RESERVATION_V2" if future else "DEMO_GOLD_DELEGATED_ISSUER_RESERVATION_V1",
        issuer_anchor=checked["history_anchor"],
        enable_issuer_anchor=public_material["original_binding"]["issuer_current"],
        delegated_admission_sha256=identity,
        enable_sha256=_digest(public_material),
        delegation_sha256=_digest(public_material["delegation"]),
        base_policy_sha256=_digest(policy),
    )
    if future:compact['continuity_event_sha256']=public_material['continuity_event']['record_hash']
    return dict(plan=plan, effective_policy=effective, delegation=compact)


def review_cancellation(handoff,*,policy,registry,history,now_ms):
    key=current_key(policy,registry,now_ms);p=handoff_proposal(handoff,'cancellation')
    _fields(p,'schema context commands query_record_hashes new_order_submission_enabled economic_authority')
    require(p['schema']=='DEMO_CANCEL_PROPOSAL_V1' and p['new_order_submission_enabled'] is False and
            p['economic_authority'] is False,'ISSUER_CANCEL_SCOPE')
    schema=cancellation_batch_schema(policy)
    context=cancellation.validate_context(p['context']);commands=cancellation.validate_commands(p['commands'],schema=schema)
    common_context(context,policy)
    require(context['cancel_batch_sha256']==cancellation.batch_sha256(commands,schema=schema),'ISSUER_CANCEL_BATCH')
    queries=p['query_record_hashes']
    require(type(queries) is list and len(queries)==len(commands) and len(set(queries))==len(queries),'ISSUER_CANCEL_QUERY_BINDING')
    for digest in queries:submission._digest(digest)
    signed={r['proposal_sha256'] for r in history if r['kind']=='SIGNED'}
    originals=[r['payload']['plan'] for r in history if r['kind']=='RESERVED' and r['proposal_sha256'] in signed and
        r['payload']['plan']['purpose']=='submission' and
        r['payload']['plan']['context']['batch_sha256']==context['original_batch_sha256'] and
        r['payload']['policy']['account_ref_sha256']==policy['account_ref_sha256'] and
        r['payload']['policy']['deployment_sha256']==policy['deployment_sha256']]
    require(len(originals)==1,'ISSUER_CANCEL_ORIGINAL_NOT_ISSUED')
    original={o['newClientOrderId']:o for o in originals[0]['orders']}
    for command in commands:
        order=original.get(command['origClientOrderId'])
        require(order is not None and order['symbol']==command['symbol']==policy['scope']['symbol'],'ISSUER_CANCEL_ORIGINAL_SCOPE')
    expiry=min(now_ms+policy['grant_ttl_ms'],policy['valid_until_ms'],key['valid_until_ms'])
    require(expiry>now_ms,'ISSUER_GRANT_EXPIRED')
    # Query freshness/venue IDs come from the deployment-owned executor pipe;
    # original executor checks them again immediately before its single DELETE.
    return {'purpose':'cancellation','proposal_sha256':handoff['proposal_sha256'],'context':context,
        'orders':[],'commands':commands,'issued_at_ms':now_ms,'expires_at_ms':expiry,'notional':'0'}


def cancellation_batch_schema(policy):
    from live.execution.demo_issuer_group_continuity import CANCEL_VIEW_SCHEMA,validate_cancel_view
    if policy['schema']==CANCEL_VIEW_SCHEMA:
        validate_cancel_view(policy)
        return cancellation.GOLD_BATCH_SCHEMA
    return cancellation.GOLD_BATCH_SCHEMA if submission_batch_schema(policy)==submission.GOLD_BATCH_SCHEMA else cancellation.LEGACY_BATCH_SCHEMA


def current_cancellation_key(policy,registry,now_ms):
    from live.execution.demo_issuer_group_continuity import CANCEL_VIEW_SCHEMA,validate_cancel_view
    if policy.get('schema')==CANCEL_VIEW_SCHEMA:
        validate_cancel_view(policy)
        return match_current_key(policy,registry,now_ms)
    return current_key(policy,registry,now_ms)


def review_template_cancellation(handoff,*,template,registry,history,now_ms):
    from live.execution.demo_issuer_group_continuity import derive_cancel_view,_cancel_exact
    from live.execution.demo_gold_future_template import current_template_key
    current_template_key(template,registry,now_ms)
    proposal=handoff_proposal(handoff,'cancellation')
    context=cancellation.validate_context(proposal['context'])
    signed={r['proposal_sha256']:r for r in history if r['kind']=='SIGNED'}
    originals=[r for r in history if r['kind']=='RESERVED' and r['proposal_sha256'] in signed
        and r['payload']['plan']['purpose']=='submission'
        and r['payload']['plan']['context']['batch_sha256']==context['original_batch_sha256']
        and r['payload']['policy']['account_ref_sha256']==template['account_ref_sha256']]
    require(len(originals)==1,'ISSUER_CANCEL_ORIGINAL_NOT_ISSUED')
    original=originals[0]
    checked=derive_cancel_view(template,original_reserved=original,original_signed=signed[original['proposal_sha256']],
        rows=history,registry=registry,now_ms=now_ms)
    plan=review_cancellation(handoff,policy=_cancel_exact(checked['policy']),registry=registry,history=history,now_ms=now_ms)
    return dict(plan=plan,**checked)


def current_delegated_key(policy,registry,now_ms):
    """Only delegated callers may select a nonexecuting installed template."""
    from live.execution.demo_gold_future_template import SCHEMA,current_template_key
    if policy.get('schema')==SCHEMA:return current_template_key(policy,registry,now_ms)
    return current_key(policy,registry,now_ms)
