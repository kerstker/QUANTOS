"""Pre-trade check battery (Phase 8). One pure check_*(intent, ctx, limits) ->
tuple[RejectionReason, ...] per gate (empty = pass). CHECK_REGISTRY order IS the
canonical reason ordering and the coverage manifest. Every check is fail-closed:
missing/non-finite/unknown inputs => the breaching reason, never a silent pass.
"""
from __future__ import annotations

import math

from quantos.contracts.rejection import RejectionReason as R
from quantos.contracts.trading import OrderSide, OrderType

from .kill_switch import KillSwitch
from quantos.contracts.killswitch import KillSwitchScope


def _finite_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _finite_pos(x) -> bool:
    return _finite_num(x) and x > 0


def _is_exposure_reducing(intent, ctx) -> bool:
    """这单是否**纯降低**本标的敞口(证明自**真实当前仓位**,非可伪造的调用方标志)——镜像
    live/execution/emergency.validate_flatten_order。True 仅当:持有非零仓位、方向与之相反、且量 ≤ |仓位|
    (只朝零缩、绝不穿越零点翻到反向)。pos=0/未知(调用方未填 positions)-> False = **fail-closed**:
    证明不了减仓就保留检查,故对每个既有空仓 caller(dry-run 全部)字节等价。

    只有 ADD-side 市场风险检查(liquidity/volatility)据此放行——降敞口的单永远应能过(退出/去风险
    是安全方向)。**绝不**用于 kill-switch/net_exposure/结构性权限检查(见 checks.py 各处 + 设计文档)。"""
    try:
        pos = ctx.account.position_qty(intent.instrument_id)   # 带符号,真实持仓;0=无仓/未知
        delta = ctx.signed_delta(intent)                        # +q BUY / -q SELL
    except Exception:
        return False                                            # fail-closed
    if pos == 0 or delta == 0:
        return False                                            # 无仓=纯开 / 无变化 -> 不豁免
    if (pos > 0) == (delta > 0):
        return False                                            # 同向 -> 加仓 -> 不豁免
    return abs(delta) <= abs(pos)                                # 反向且 ≤|仓位| -> 只朝零缩、不穿越


# --- Stage A: hard gates (safety / permission / structural) ------------------

def check_kill_switch(intent, ctx, limits):
    ks = KillSwitch(ctx.kill_switch)
    sv = {KillSwitchScope.INSTRUMENT: intent.instrument_id,
          KillSwitchScope.ACCOUNT: ctx.account.account_id}.get(ctx.kill_switch.scope, "")
    return (R.KILL_SWITCH_ACTIVE,) if ks.is_active(scope_value=sv) else ()


def check_live_trading_disabled(intent, ctx, limits):
    sc = ctx.safety_config
    if sc.live_trading_enabled or sc.real_broker_enabled or sc.allow_order_submission:
        return (R.LIVE_TRADING_DISABLED,)
    return ()


def check_ai_direct_trading(intent, ctx, limits):
    # the forbidden config flag is the binding control (live direct origination is
    # out of scope at L2 — live is disabled); never allow AI-direct trading.
    return (R.AI_DIRECT_TRADING_FORBIDDEN,) if ctx.safety_config.allow_ai_direct_trading else ()


def check_strategy_approved(intent, ctx, limits):
    s = ctx.strategy
    return () if (s.approved and s.approval_id) else (R.STRATEGY_NOT_APPROVED,)


def check_instrument_allowed(intent, ctx, limits):
    if not ctx.market.known or intent.instrument_id not in limits.allowed_instruments:
        return (R.INSTRUMENT_NOT_ALLOWED,)
    return ()


def check_account_allowed(intent, ctx, limits):
    aid = ctx.account.account_id
    return () if (aid and aid in limits.allowed_accounts) else (R.ACCOUNT_NOT_ALLOWED,)


def check_quantity(intent, ctx, limits):
    q = intent.quantity
    return () if (isinstance(q, int) and not isinstance(q, bool) and q > 0) else (R.INVALID_QUANTITY,)


def check_price_sanity(intent, ctx, limits):
    if intent.order_type == OrderType.LIMIT and intent.limit_price is None:
        return (R.INVALID_PRICE,)
    ref = ctx.market.reference_price
    # a reference price is REQUIRED to sanity-check ANY order (market OR limit);
    # without it we cannot bound the price -> fail closed (review P0). This is the
    # same "None = unknown -> reject" pattern used for liquidity/volatility.
    if not _finite_pos(ref):
        return (R.INVALID_PRICE,)
    p = ctx.effective_price(intent)
    if not _finite_pos(p):
        return (R.INVALID_PRICE,)
    if intent.limit_price is not None and abs(intent.limit_price - ref) / ref > limits.max_price_deviation_pct:
        return (R.INVALID_PRICE,)
    return ()


def check_duplicate(intent, ctx, limits):
    return (R.DUPLICATE_ORDER,) if ctx.fingerprint(intent) in ctx.recent_order_fingerprints else ()


def check_market_session(intent, ctx, limits):
    if not ctx.market.market_open:
        return (R.MARKET_CLOSED,)
    if ctx.now - ctx.market.price_as_of > ctx.max_staleness:
        return (R.MARKET_CLOSED,)
    return ()


# --- Stage B: limit checks (post-trade, signed) ------------------------------

def _new_gross(intent, ctx):
    price = ctx.effective_price(intent)
    if not _finite_pos(price):
        return None
    cur_mv = ctx.account.position_value(intent.instrument_id)
    proj_mv = ctx.projected_position(intent) * price
    return ctx.account.gross_exposure - abs(cur_mv) + abs(proj_mv)


def check_cash(intent, ctx, limits):
    if intent.side != OrderSide.BUY:
        return ()
    notional = ctx.order_notional(intent)
    if not _finite_num(notional) or not _finite_num(ctx.account.cash):
        return (R.INSUFFICIENT_CASH,)
    if ctx.account.cash < notional * (1.0 + limits.cash_buffer_pct):
        return (R.INSUFFICIENT_CASH,)
    return ()


def check_position_limit(intent, ctx, limits):
    if abs(ctx.projected_position(intent)) > limits.position_cap(intent.instrument_id):
        return (R.POSITION_LIMIT_EXCEEDED,)
    return ()


def check_order_size(intent, ctx, limits):
    notional = ctx.order_notional(intent)
    if (
        not _finite_num(notional)
        or not _finite_num(intent.quantity)
        or notional > limits.max_order_notional
        or intent.quantity > limits.fat_finger_qty
    ):
        return (R.ORDER_SIZE_EXCEEDED,)
    return ()


def check_concentration(intent, ctx, limits):
    eq = ctx.account.equity
    price = ctx.effective_price(intent)
    if not _finite_pos(eq) or not _finite_pos(price):
        return (R.CONCENTRATION_LIMIT_EXCEEDED,)
    proj_mv = abs(ctx.projected_position(intent) * price)
    return (R.CONCENTRATION_LIMIT_EXCEEDED,) if proj_mv / eq > limits.max_single_name_pct else ()


def check_gross_exposure(intent, ctx, limits):
    g = _new_gross(intent, ctx)
    if not _finite_num(g) or g > limits.max_gross_exposure:
        return (R.POSITION_LIMIT_EXCEEDED,)
    return ()


def check_net_exposure(intent, ctx, limits):
    price = ctx.effective_price(intent)
    if not _finite_pos(price) or not _finite_num(ctx.account.net_exposure):
        return (R.POSITION_LIMIT_EXCEEDED,)
    cur_mv = ctx.account.position_value(intent.instrument_id)
    proj_mv = ctx.projected_position(intent) * price
    new_net = ctx.account.net_exposure - cur_mv + proj_mv
    return (R.POSITION_LIMIT_EXCEEDED,) if (not _finite_num(new_net) or abs(new_net) > limits.max_net_exposure) else ()


def check_leverage(intent, ctx, limits):
    eq = ctx.account.equity
    g = _new_gross(intent, ctx)
    if not _finite_pos(eq) or not _finite_num(g) or g / eq > limits.max_leverage:
        return (R.POSITION_LIMIT_EXCEEDED,)
    return ()


def check_daily_loss(intent, ctx, limits):
    pnl = ctx.account.day_pnl
    if not _finite_num(pnl):
        return (R.DAILY_LOSS_LIMIT_EXCEEDED,)
    return (R.DAILY_LOSS_LIMIT_EXCEEDED,) if pnl <= -limits.daily_loss_limit else ()


def check_drawdown(intent, ctx, limits):
    peak = ctx.account.peak_equity
    eq = ctx.account.equity
    if not _finite_pos(peak) or not _finite_num(eq):
        return (R.DRAWDOWN_LIMIT_EXCEEDED,)
    return (R.DRAWDOWN_LIMIT_EXCEEDED,) if (peak - eq) / peak > limits.max_drawdown else ()


def check_turnover(intent, ctx, limits):
    eq = ctx.account.equity
    notional = ctx.order_notional(intent)
    if not _finite_pos(eq) or not _finite_num(notional) or not _finite_num(ctx.plan_turnover):
        return (R.TURNOVER_LIMIT_EXCEEDED,)
    if (notional / eq) + ctx.plan_turnover > limits.max_turnover:
        return (R.TURNOVER_LIMIT_EXCEEDED,)
    return ()


def check_rate_limit(intent, ctx, limits):
    # 单位不变量(审计 P1-5):ctx.now / ctx.recent_timestamps / limits.rate_window_ms **必须同一时间单位**
    # (live 路径 = 毫秒;dry-run 路径 recent_timestamps 恒空、此检查 no-op)。三者同基故纯减法比较正确。
    cutoff = ctx.now - limits.rate_window_ms
    recent = sum(1 for t in ctx.recent_timestamps if t >= cutoff)
    return (R.TURNOVER_LIMIT_EXCEEDED,) if recent >= limits.max_orders_per_window else ()


_CANCELED_ORDER_STATUSES = frozenset({"CANCELED", "CANCELLED", "EXPIRED"})
_KNOWN_ORDER_STATUSES = frozenset({
    "PENDING_NEW", "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "CANCELLED",
    "REJECTED", "EXPIRED", "PREVIEW_NOT_SUBMITTED",
})


def check_cancel_rate(intent, ctx, limits):
    if _is_exposure_reducing(intent, ctx):
        return ()
    statuses = tuple(str(status).upper() for status in ctx.recent_order_statuses)
    if len(statuses) < limits.min_cancel_rate_samples:
        return ()
    if any(status not in _KNOWN_ORDER_STATUSES for status in statuses):
        return (R.TURNOVER_LIMIT_EXCEEDED,)
    canceled = sum(1 for status in statuses if status in _CANCELED_ORDER_STATUSES)
    return (
        (R.TURNOVER_LIMIT_EXCEEDED,)
        if canceled / len(statuses) > limits.max_cancel_rate
        else ()
    )


def check_liquidity(intent, ctx, limits):
    # reduce-only 豁免:证明为纯降敞口的单永远应能过(退出/去风险),不被流动性 fail-closed 挡——见
    # _is_exposure_reducing。无仓/加仓/翻仓不豁免,故对既有空仓 caller 字节等价。
    if _is_exposure_reducing(intent, ctx):
        return ()
    vol = ctx.market.est_daily_volume
    if vol is None or not math.isfinite(vol) or vol <= 0:
        return (R.LIQUIDITY_LIMIT_EXCEEDED,)
    return (R.LIQUIDITY_LIMIT_EXCEEDED,) if intent.quantity / vol > limits.max_participation_rate else ()


def check_volatility(intent, ctx, limits):
    if _is_exposure_reducing(intent, ctx):                      # 同上:减仓不被波动 fail-closed 挡
        return ()
    iv = ctx.market.instrument_vol
    if iv is None or not math.isfinite(iv) or iv > limits.max_instrument_vol:
        return (R.VOLATILITY_LIMIT_EXCEEDED,)
    return ()


# Registry order = canonical reason ordering + coverage manifest.
CHECK_REGISTRY = (
    ("kill_switch", "A", check_kill_switch),
    ("live_trading_disabled", "A", check_live_trading_disabled),
    ("ai_direct_trading", "A", check_ai_direct_trading),
    ("strategy_approved", "A", check_strategy_approved),
    ("instrument_allowed", "A", check_instrument_allowed),
    ("account_allowed", "A", check_account_allowed),
    ("quantity", "A", check_quantity),
    ("price_sanity", "A", check_price_sanity),
    ("duplicate", "A", check_duplicate),
    ("market_session", "A", check_market_session),
    ("cash", "B", check_cash),
    ("position_limit", "B", check_position_limit),
    ("order_size", "B", check_order_size),
    ("concentration", "B", check_concentration),
    ("gross_exposure", "B", check_gross_exposure),
    ("net_exposure", "B", check_net_exposure),
    ("leverage", "B", check_leverage),
    ("daily_loss", "B", check_daily_loss),
    ("drawdown", "B", check_drawdown),
    ("turnover", "B", check_turnover),
    ("rate_limit", "B", check_rate_limit),
    ("cancel_rate", "B", check_cancel_rate),
    ("liquidity", "B", check_liquidity),
    ("volatility", "B", check_volatility),
)
