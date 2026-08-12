"""QUANTOS 数据渠道目录（纯值、无 IO、无网络）。

数据抓取器位于 ``tools/``，研究核心只消费经过 PIT/质量门的结果。本目录把
“有哪些真实渠道、能提供哪些字段、历史深度到哪里、能服务哪些 Alpha”固定成
机器可读契约，避免把一个实时快照误标成历史数据，或把诊断数据误标成可交易
纸面数据。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DataChannelSpec:
    channel_id: str
    provider: str
    venue: str
    dataset_id: str
    cadence: str
    temporal_resolution: str
    fields: tuple[str, ...]
    artifact_path: str
    availability: str
    alpha_families: tuple[str, ...]
    freshness_sla_hours: int
    endpoint_class: str
    notes: str

    def as_dict(self) -> dict:
        """稳定、JSON 可序列化的公开表示。"""
        return asdict(self)


# 这是数据覆盖目录，不是晋级目录。availability 的含义：
# REAL_HISTORY = 有连续历史；REAL_RECENT = 真实但历史窗口有限；
# REAL_DIAGNOSTIC = 有真实诊断证据但尚未接入可交易截面；
# REAL_SNAPSHOT = 只积累从观测时刻开始的同步快照；
# BLOCKED_MISSING = 当前没有合格本地资产；PUBLIC_STREAM_REQUIRED = REST 端点
# 需要额外权限或公开流，当前安全边界不使用凭证。
DATA_CHANNELS: tuple[DataChannelSpec, ...] = (
    DataChannelSpec(
        "binance_usdm_price", "Binance Data Vision", "binance_usdm",
        "perp_mark_prices", "daily", "1d",
        ("open", "high", "low", "close", "volume"),
        "data/cache/crypto_perp_mark_prices.json", "REAL_HISTORY",
        ("trend", "mean_reversion", "liquidity_microstructure", "spot_perp_basis"),
        72, "public_archive", "USD-M 永续标记价日线；PIT 日收盘口径。",
    ),
    DataChannelSpec(
        "binance_usdm_funding", "Binance", "binance_usdm",
        "funding", "8h->daily", "1d",
        ("funding_rate", "funding_daily_sum"),
        "data/cache/crypto_funding_rates.json", "REAL_HISTORY",
        ("funding_basis", "spot_perp_basis"), 72, "public_rest",
        "8 小时费率聚合为已结算 UTC 日累计；当前缺口必须 BLOCK，不能用 premiumIndex 回填历史。",
    ),
    DataChannelSpec(
        "binance_usdm_oi_metrics", "Binance Data Vision", "binance_usdm",
        "oi_metrics", "5m->daily", "1d",
        ("oi", "oi_value", "top_ls", "global_ls", "taker_ls"),
        "data/cache/crypto_oi_metrics.json", "REAL_HISTORY",
        ("crowd_reversal", "liquidity_microstructure"), 120, "public_archive",
        "取每日最后一个 5 分钟指标；不把实时 OI 冒充历史 OI。",
    ),
    DataChannelSpec(
        "bybit_linear_positioning", "Bybit", "bybit_linear",
        "bybit_positioning_metrics", "daily", "1d",
        ("open_interest", "buy_ratio", "sell_ratio", "long_short_ratio"),
        "data/cache/bybit_positioning_metrics.json", "REAL_HISTORY",
        ("crowd_reversal", "cross_exchange", "event_regime"),
        72, "public_rest",
        "Official public linear-perpetual OI and broad-account long/short history; "
        "cursor-paginated, source-timestamped, and restricted to completed UTC days. "
        "It is a research input, never account or Testnet admission evidence.",
    ),
    DataChannelSpec(
        "coinmetrics_community_onchain", "Coin Metrics Community", "onchain",
        "coinmetrics_onchain", "daily", "1d",
        ("active_addresses", "transaction_count", "mvrv"),
        "data/cache/coinmetrics_onchain.json", "REAL_HISTORY",
        ("onchain_activity", "onchain_value", "event_regime"),
        72, "public_rest_noncommercial",
        "Keyless Community API history for nine native crypto networks and the "
        "three fields exposed by the current Community entitlement. CC BY-NC "
        "license: L2 non-commercial research only; commercial deployment requires "
        "separate entitlement review and cannot inherit this channel automatically. "
        "The current nine-asset breadth is below the formal alpha gate minimum of ten, "
        "so this channel is diagnostic unless the Community entitlement widens.",
    ),
    DataChannelSpec(
        "defillama_free_chain_tvl", "DefiLlama Free API", "onchain",
        "defillama_chain_tvl", "daily", "1d",
        ("chain_tvl_usd", "chain_tvl_growth", "chain_tvl_acceleration"),
        "data/cache/defillama_chain_tvl.json", "REAL_DIAGNOSTIC",
        ("onchain_activity", "onchain_value", "event_regime"),
        72, "public_rest",
        "Official keyless Free API chain TVL history with a pinned 19-chain-to-asset "
        "mapping. Public research input only; deployment entitlement is a separate "
        "human review and this channel never grants Testnet admission. Historical "
        "values are retrospective/revisable with no vintages, so only forward "
        "snapshots can become PIT evidence.",
    ),
    DataChannelSpec(
        "defillama_global_stablecoin_supply", "DefiLlama", "onchain",
        "global_stablecoin_supply", "daily", "1d",
        ("usd_stablecoin_supply", "supply_growth", "supply_acceleration"),
        "data/cache/defillama_stablecoin_supply.json", "REAL_DIAGNOSTIC",
        ("liquidity_impulse", "event_regime"),
        48, "public_rest",
        "Keyless global totalCirculatingUSD.peggedUSD history. Historical rows are "
        "retrospective and revisable without vintages, so they are association-only; "
        "a separate daily bitemporal forward-vintage store starts honest PIT accumulation.",
    ),
    DataChannelSpec(
        "hyperliquid_public_perp", "Hyperliquid", "hyperliquid_perp",
        "hyperliquid_public_market", "1h->daily + snapshot", "1h/1d/snapshot",
        (
            "funding_rate", "premium", "mark_price", "oracle_price",
            "open_interest", "day_notional_volume", "predicted_funding",
        ),
        "data/cache/hyperliquid_public_market.json", "REAL_HISTORY",
        (
            "funding_basis", "cross_exchange", "crowd_reversal",
            "liquidity_microstructure", "event_regime",
        ),
        24, "public_rest",
        "Official keyless POST /info market data. Historical hourly funding is "
        "retained only as complete 24-settlement UTC days; current public asset "
        "contexts and Binance/Bybit/Hyperliquid predicted funding are snapshots. "
        "No wallet, account, signature, credential or order surface is used.",
    ),
    DataChannelSpec(
        "cftc_tff_crypto_positioning", "U.S. CFTC", "cme_futures",
        "traders_in_financial_futures_futures_only", "weekly", "1w",
        (
            "open_interest_contracts", "leveraged_money_net_pct",
            "asset_manager_net_pct", "dealer_net_pct",
            "other_reportable_net_pct", "nonreportable_net_pct",
        ),
        "data/cache/cftc_tff_crypto.json", "REAL_HISTORY",
        ("institutional_positioning", "crowd_reversal", "event_regime"),
        24 * 8, "public_rest",
        "Official keyless CFTC TFF Futures-Only history for the canonical CME BTC "
        "and cash-settled ETH contracts. Report dates and research knowledge dates "
        "are separate: rows wait seven calendar days and enter only on a strictly "
        "later close. Micro/discontinued duplicate contracts are excluded.",
    ),
    DataChannelSpec(
        "binance_usdm_book_depth_archive", "Binance Data Vision", "binance_usdm",
        "binance_usdm_book_depth_curve_hourly", "daily", "30s->1h",
        (
            "bid_depth_curve_p10", "ask_depth_curve_p10",
            "bid_notional_curve_p10", "ask_notional_curve_p10",
            "implicit_mid_price_median", "bounded_quality_quarantine",
        ),
        "data/cache/binance_book_depth_archive.db", "REAL_DIAGNOSTIC",
        ("liquidity_microstructure", "cross_exchange", "event_regime"),
        72, "public_archive",
        "Checksum-verified USD-M completed-day bookDepth curves for the 14-symbol "
        "research universe. Historical exchange events are retrospective and "
        "become known only at actual fetch time; formal_forward_credit is false. "
        "At most 0.1% isolated implicit-cross snapshots may be quarantined with "
        "exact timestamps and a degraded verdict. Independent mark-price alignment "
        "is currently UNASSESSED, so this channel remains diagnostic and cannot "
        "grant Alpha, Shadow, Testnet, or order admission.",
    ),
    DataChannelSpec(
        "binance_usdm_realtime_complete", "Binance", "binance_usdm",
        "binance_realtime_market", "5m", "1m/5m/snapshot",
        (
            "mark_price", "index_price", "basis_bps", "funding_rate",
            "mark_1m_ohlc", "index_1m_ohlc", "premium_1m_ohlc",
            "funding_rate_cap", "funding_rate_floor",
            "funding_interval_hours",
            "bid", "bid_qty", "ask", "ask_qty", "spread_bps",
            "open", "high", "low", "close", "volume",
            "contract_status", "tick_size", "step_size", "min_qty",
            "max_qty", "min_notional", "rolling_24h_quote_volume",
            "rolling_24h_trade_count",
            "oi", "oi_value", "global_ls", "top_ls", "taker_ls",
        ),
        "data/cache/binance_realtime_market.db", "REAL_RECENT",
        (
            "funding_basis", "crowd_reversal", "liquidity_microstructure",
            "spot_perp_basis", "event_regime",
        ),
        1, "public_rest",
        "Complete public USD-M snapshots for the 14-ticker forward universe; "
        "append-only, hash-chained, source-timestamped, and never account-backed.",
    ),
    DataChannelSpec(
        "binance_usdm_websocket_stream", "Binance", "binance_usdm",
        "binance_realtime_stream", "continuous->5s", "100ms/500ms/1s/1m",
        (
            "best_bid_ask", "depth20", "mark_price", "index_price",
            "aggregate_trades", "kline_1m", "liquidation_orders",
            "rpi_depth_updates", "rpi_sequence_continuity",
            "contract_status_updates", "notional_bracket_updates",
        ),
        "data/cache/binance_realtime_stream.db", "REAL_RECENT",
        (
            "liquidity_microstructure", "event_regime", "crowd_reversal",
            "funding_basis",
        ),
        1, "public_websocket",
        "Routed Binance public and market WebSocket streams, including "
        "RPI-inclusive depth deltas and contract-info changes, reduced to "
        "hash-chained five-second snapshots; REST remains the recovery baseline "
        "and RPI data is diagnostic-only for paper fills.",
    ),
    DataChannelSpec(
        "binance_usdm_systemic_risk", "Binance", "binance_usdm",
        "binance_systemic_risk", "30m", "30m/1h/snapshot",
        (
            "adl_risk", "basis", "basis_rate", "annualized_basis_rate",
            "index_constituent_price", "index_constituent_weight",
            "index_dispersion_bps", "insurance_asset",
            "insurance_margin_balance",
        ),
        "data/cache/binance_systemic_risk.db", "REAL_RECENT",
        (
            "funding_basis", "liquidity_microstructure", "event_regime",
            "crowd_reversal",
        ),
        2, "public_rest",
        "Official 30-minute ADL ratings, incremental 30-day hourly perpetual "
        "basis history, index constituents, and insurance-fund balances; "
        "append-only, hash-chained, public-only, and diagnostic for L2 paper.",
    ),
    DataChannelSpec(
        "binance_usdm_rpi_depth", "Binance", "binance_usdm",
        "binance_rpi_depth", "1h", "1000 levels/snapshot",
        (
            "rpi_bid_price", "rpi_bid_quantity", "rpi_ask_price",
            "rpi_ask_quantity", "spread_bps", "bid_notional",
            "ask_notional", "notional_imbalance",
        ),
        "data/cache/binance_rpi_depth.db", "REAL_RECENT",
        ("liquidity_microstructure", "event_regime"),
        2, "public_rest",
        "Complete official 1000-level RPI-inclusive snapshots sampled hourly; "
        "separate from the five-minute execution book because each symbol costs "
        "IP weight 20 and the payload is intentionally large.",
    ),
    DataChannelSpec(
        "binance_spot_klines", "Binance", "binance_spot",
        "spot_klines", "daily", "1d",
        ("open", "high", "low", "close", "volume"),
        "data/cache/crypto_binance.db", "REAL_HISTORY",
        ("spot_perp_basis", "trend", "mean_reversion"), 72, "public_rest",
        "现货 PIT 日线，用于现货—永续价差和跨市场校验。",
    ),
    DataChannelSpec(
        "binance_liquidations", "Binance", "binance_usdm",
        "liquidations", "event->daily", "event/day",
        ("liquidation_notional", "long_liquidation_notional",
         "short_liquidation_notional", "order_count", "largest_notional"),
        "data/cache/crypto_liquidations.json", "PUBLIC_STREAM_REQUIRED",
        ("crowd_reversal", "event_regime"), 24, "public_rest",
        "REST 强平端点在本环境返回权限/请求错误；不读取交易凭证，需未来接入公开 forceOrder 流后再积累近期样本。",
    ),
    DataChannelSpec(
        "cross_exchange_book", "Binance + Bybit", "multi_venue",
        "cross_exchange_snapshots", "on-demand", "snapshot",
        ("bid", "ask", "mid", "spread_bps", "last_price",
         "funding_rate", "open_interest"),
        "data/cache/cross_exchange_snapshot.json", "REAL_SNAPSHOT",
        ("cross_exchange", "liquidity_microstructure"), 1, "public_rest",
        "同步抓取的公开盘口/资金费/OI；knowledge_time=observed_at，积累不足前只能做实时诊断。",
    ),
    DataChannelSpec(
        "deribit_term_structure", "Deribit", "deribit",
        "deribit_futures", "daily", "1d",
        ("close", "basis", "term_structure"),
        "data/cache/deribit_vol.db", "REAL_DIAGNOSTIC",
        ("term_structure", "spot_perp_basis", "event_regime"), 72, "public_rest",
        "有永续/到期期货历史和期限结构研究证据；尚未接入主截面纸面执行簿。",
    ),
    DataChannelSpec(
        "deribit_options_vol", "Deribit", "deribit",
        "options_iv", "snapshot->daily", "1d",
        ("mark_iv", "delta", "skew", "open_interest", "volume"),
        "data/cache/deribit_options_skew.json", "REAL_DIAGNOSTIC",
        ("volatility_risk_premium", "event_regime"), 72, "public_rest",
        "真实期权 book/skew 快照；历史从首次采集开始累积，不回填未观测日期。",
    ),
    DataChannelSpec(
        "fred_market_regime", "FRED", "macro",
        "macro_fred", "daily", "1d",
        ("VIX", "DGS10", "T10Y2Y", "HY_OAS", "DXY", "DFII10"),
        "data/cache/macro_fred.db", "REAL_HISTORY",
        ("event_regime",), 72, "public_csv",
        "市场型 EOD 序列；用于回顾性 regime 分桶，不等同于事件 surprise。",
    ),
    DataChannelSpec(
        "alfred_vintage", "ALFRED", "macro",
        "macro_alfred", "release/vintage", "event/vintage",
        ("CPI", "UNRATE", "GDP", "realtime_start"),
        "data/cache/macro_alfred.db", "REAL_DIAGNOSTIC",
        ("event_regime",), 24 * 30, "keyed_public_api",
        "需要用户自己的 FRED_API_KEY；realtime_start 是知识时间，缺 key 不回退到修订后序列。",
    ),
    DataChannelSpec(
        "mexc_spot_btc", "MEXC", "mexc_spot",
        "mexc_btc_klines", "daily", "1d",
        ("open", "high", "low", "close", "volume"),
        "data/cache/crypto_mexc.db", "REAL_HISTORY",
        ("cross_exchange", "spot_perp_basis"), 72, "public_rest",
        "当前为 BTC 单标的现货校验渠道；不外推为全宇宙跨所历史。",
    ),
)


def channel_specs() -> tuple[DataChannelSpec, ...]:
    """按固定目录顺序返回渠道。"""
    return DATA_CHANNELS


def spec_by_channel(channel_id: str) -> DataChannelSpec:
    for spec in DATA_CHANNELS:
        if spec.channel_id == channel_id:
            return spec
    raise KeyError(channel_id)


def channels_for_family(family_id: str) -> tuple[DataChannelSpec, ...]:
    return tuple(spec for spec in DATA_CHANNELS if family_id in spec.alpha_families)


__all__ = ["DataChannelSpec", "DATA_CHANNELS", "channel_specs",
           "spec_by_channel", "channels_for_family"]
