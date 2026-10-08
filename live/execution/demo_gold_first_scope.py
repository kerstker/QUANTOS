"""Explicit first Gold declarations and a non-executing operator contract.

These pure checks authenticate neither an operator nor protected custody. The
installer independently reads the original BTC book, issuer prefix and source.
No functional policy, task, grant or account baseline is constructed here.
"""
from copy import deepcopy
from dataclasses import asdict
import re

from live.execution.demo_batch_risk import _digest, _fields, _json_value
from live.execution.demo_issuer_policy import require, amount
from services.research.demo_market_inputs import VOLATILITY_POLICY
from services.risk.pre_trade.config import RiskLimits
from services.validation import demo_submission as submission

REQUEST_SCHEMA = 'GOLD_DEMO_FIRST_GOLD_SCOPE_REQUEST_V1'
APPROVAL_SCHEMA = 'DEMO_GOLD_FIRST_SCOPE_OPERATOR_V1'
BINDING_SCHEMA = 'DEMO_GOLD_FIRST_SCOPE_BINDING_V1'
PURPOSE = 'FIRST_GOLD_SCOPE_ONLY'
REQUEST_FIELDS = ('schema status purpose source_commit account_ref_sha256 registry_sha256 current_policy_sha256 '
    'current_profile_sha256 original_source_commit original_setup_sha256 original_setup original_issuer_history '
    'original_cycle_sha256 original_spec_sha256 reviewed_at_ms valid_until_ms original_cycle_review runtime_sha256 '
    'runtime strategy_definition_sha256 definition duration_seconds installation_allowance_seconds proposed_budget '
    'target_declarations target_declarations_sha256 stop_image_sha256 pilot_max_loss_fraction '
    'new_independent_mandate_required exact_decision_approval_required pilot_installation_verified '
    'approval_granted budget_enrolled submission_enabled economic_authority request_sha256')


def target_declarations(*, account_ref, market_source_sha256, wallet):
    """New finite Gold terms shown in this confirmation, never inherited BTC units."""
    submission._digest(account_ref);submission._digest(market_source_sha256)
    require(type(wallet) is str and amount(wallet)>0, 'FIRST_SCOPE_TARGET_WALLET')
    limits = RiskLimits(allowed_instruments=frozenset({'XAUUSDT'}), allowed_accounts=frozenset({account_ref}),
        default_max_position_qty=.01, max_position_qty=(('XAUUSDT', .01),), fat_finger_qty=.01,
        max_order_notional=250, max_gross_exposure=250, max_net_exposure=250, max_price_deviation_pct=.01,
        max_leverage=.1, max_single_name_pct=.1, daily_loss_limit=10, max_drawdown=.01,
        max_turnover=.3, max_orders_per_window=3, rate_window_ms=60000, max_cancel_rate=.8,
        min_cancel_rate_samples=20, max_participation_rate=.001, max_instrument_vol=.8, cash_buffer_pct=.9)
    return dict(schema='DEMO_GOLD_FIRST_SCOPE_DECLARATIONS_V1', symbol='XAUUSDT', grant_ttl_ms=30000,
        budget=dict(max_grants=3, max_orders=3, max_total_notional='750', max_cancel_grants=3, max_cancel_orders=3),
        scope=dict(symbol='XAUUSDT', sides=['BUY', 'SELL'], time_in_force=['IOC'], allow_opening=True,
            max_quantity_per_order='0.01', max_notional_per_order='250', max_notional_per_grant='250'),
        native_limits=_json_value(asdict(limits)),
        mandate_limits=dict(venue='testnet', exchange='binance', allowed_instruments=['XAUUSDT'],
            max_single_order_notional=250, max_gross_notional=250, max_net_notional=250,
            per_instrument_max_notional=[['XAUUSDT', 250]]),
        market_source_sha256=market_source_sha256, volatility_policy=VOLATILITY_POLICY,
        initial_day_pnl='0', initial_peak_equity=wallet, minimum_plan_turnover=0)


def validate_request(request):
    """Strict declared bytes; original evidence replay is a separate required step."""
    from live.execution.demo_gold_first_scope_history import validate_anchor
    _fields(request, REQUEST_FIELDS)
    require(request['schema']==REQUEST_SCHEMA and request['purpose']==PURPOSE
        and request['status']=='INDEPENDENT_SCOPE_ENROLLMENT_REQUIRED', 'FIRST_SCOPE_REQUEST_SCHEMA')
    for name in ('source_commit', 'original_source_commit'):
        require(type(request[name]) is str and re.fullmatch('[a-f0-9]{40}', request[name]), 'FIRST_SCOPE_SOURCE')
    for name in REQUEST_FIELDS.split():
        if name.endswith('_sha256'):submission._digest(request[name])
    for name in ('reviewed_at_ms', 'valid_until_ms', 'duration_seconds', 'installation_allowance_seconds'):
        submission._integer(request[name])
    require(0<request['reviewed_at_ms']<request['valid_until_ms']<=request['reviewed_at_ms']+30000
        and request['duration_seconds']==86400 and request['installation_allowance_seconds']==300
        and request['pilot_max_loss_fraction']=='0.05', 'FIRST_SCOPE_REQUEST_WINDOW')
    require(request['new_independent_mandate_required'] is True
        and request['exact_decision_approval_required'] is True
        and all(request[name] is False for name in ('pilot_installation_verified', 'approval_granted',
            'budget_enrolled', 'submission_enabled', 'economic_authority')), 'FIRST_SCOPE_REQUEST_AUTHORITY')
    validate_anchor(request['original_issuer_history'])
    original=request['original_setup'];_fields(original, 'spec result')
    require(_digest(original)==request['original_setup_sha256']
        and _digest(original['spec'])==request['original_spec_sha256']
        and _digest(original['result'])==request['original_cycle_sha256']
        and original['spec']['source_commit']==original['result']['source_commit']==request['original_source_commit'],
        'FIRST_SCOPE_ORIGINAL_SETUP_BINDING')
    review=request['original_cycle_review'];target=request['target_declarations']
    expected=target_declarations(account_ref=request['account_ref_sha256'],
        market_source_sha256=target['market_source_sha256'], wallet=review['sealed_usdt_wallet'])
    require(_digest(target)==_digest(expected)==request['target_declarations_sha256']
        and _digest(request['proposed_budget'])==_digest(expected['budget']), 'FIRST_SCOPE_FINITE_DECLARATIONS')
    require(_digest(request['runtime'])==request['runtime_sha256']
        and request['definition']['definition_hash']==request['strategy_definition_sha256'],
        'FIRST_SCOPE_RESEARCH_BINDING')
    require(_digest({k:v for k,v in request.items() if k!='request_sha256'})==request['request_sha256'],
        'FIRST_SCOPE_REQUEST_DIGEST')
    submission._canonical(request)
    return request


def validate_approval(approval, *, request, now_ms):
    validate_request(request);submission._integer(now_ms)
    _fields(approval, 'schema purpose request_sha256 original_cycle_sha256 operator_id approved_at_ms '
        'duration_seconds installation_allowance_seconds execution_approval_granted economic_authority')
    require(approval['schema']==APPROVAL_SCHEMA and approval['purpose']==PURPOSE
        and approval['request_sha256']==request['request_sha256']
        and approval['original_cycle_sha256']==request['original_cycle_sha256'], 'FIRST_SCOPE_OPERATOR_BINDING')
    name=approval['operator_id']
    require(type(name) is str and re.fullmatch('[A-Za-z0-9_.@-]{1,96}', name)
        and name.upper() not in ('AI', 'AUTO', 'SYSTEM', 'AGENT', 'CODEX'), 'FIRST_SCOPE_OPERATOR_IDENTITY')
    for field in ('approved_at_ms', 'duration_seconds', 'installation_allowance_seconds'):
        submission._integer(approval[field])
    require(request['reviewed_at_ms']<=approval['approved_at_ms']<=now_ms<request['valid_until_ms']
        and approval['duration_seconds']==86400 and approval['installation_allowance_seconds']==300
        and approval['execution_approval_granted'] is False and approval['economic_authority'] is False,
        'FIRST_SCOPE_OPERATOR_WINDOW_OR_PERMISSION')
    return approval


def declared_profile(request, approval):
    """Material construction after confirmation; callers establish its authenticity."""
    terms=request['target_declarations'];identity=_digest(approval);at=approval['approved_at_ms']
    sid='gold-first-scope-'+identity[:16]
    mandate=dict(deepcopy(terms['mandate_limits']), mandate_id='gold-first-scope-'+identity[:16],
        approved_by=[approval['operator_id']], issued_at=at, expires_at=at+(86400+300)*1000)
    return dict(schema='DEMO_REQUEST_PROFILE_V1', environment='BINANCE_USDM_DEMO', symbol='XAUUSDT',
        market_source_sha256=terms['market_source_sha256'], volatility_policy=terms['volatility_policy'],
        native_limits=deepcopy(terms['native_limits']), mandate=mandate,
        strategy=dict(strategy_id=sid, approved=True, approval_id=identity),
        attestations={sid:dict(strategy_id=sid, kind='human_override', attested_by=approval['operator_id'],
            attested_at=at, reason='Independent finite first Gold Scope only; no exact order approval, '
                'profitability or mainnet authority. Confirmation '+identity, validation_result_hash='')},
        initial_day_pnl=terms['initial_day_pnl'], initial_peak_equity=terms['initial_peak_equity'],
        minimum_plan_turnover=terms['minimum_plan_turnover'])
