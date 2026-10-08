"""Static fail-closed policy for the minimal L2 scheduler profile.

Only the modules in :data:`MVP_SAFE_MODULES` may be launched.  Hard-quarantined
workers retain their invalidated-authority classification, while the separate
MVP-disabled set records workers that are intentionally outside the minimal
public-data and control-plane runtime even when their own implementation can
fail closed.

The task-to-module maps are the canonical scheduler vocabulary.  Keeping the
maps here lets launchers, renderers, and watchdogs consume one closed policy
without deriving authority from mutable runtime configuration.
"""
from __future__ import annotations


MVP_EXPECTED_TASKS: dict[str, str] = {
    "QUANTOS_btc_observation_schedule": "btc_observation_schedule",
    "QUANTOS_ashare_alpha_search": "ashare_alpha_search_worker",
    "QUANTOS_ashare_forward_observation": "ashare_forward_observation",
    "QUANTOS_artifact_admission_seal": "artifact_admission_seal",
    "QUANTOS_binance_book_depth_forward_refresh": (
        "binance_book_depth_forward_refresh"
    ),
    "QUANTOS_binance_realtime_drift_alerts": "binance_realtime_drift_alerts",
    "QUANTOS_binance_realtime_features": "binance_realtime_features",
    "QUANTOS_binance_realtime_market": "binance_realtime_market",
    "QUANTOS_binance_realtime_stream": "binance_realtime_stream",
    "QUANTOS_binance_rpi_depth": "binance_rpi_depth",
    "QUANTOS_binance_systemic_risk": "binance_systemic_risk",
    "QUANTOS_bybit_realtime_stream": "bybit_realtime_stream",
    "QUANTOS_cftc_tff_forward_refresh": "cftc_tff_forward_refresh",
    "QUANTOS_cross_venue_liquidation_health": "cross_venue_liquidation_health",
    "QUANTOS_defillama_stablecoin_forward_refresh": (
        "defillama_stablecoin_forward_refresh"
    ),
    "QUANTOS_defillama_tvl_forward_refresh": "defillama_tvl_forward_refresh",
    "QUANTOS_domain_event_ops": "domain_event_ops",
    "QUANTOS_domain_event_seal": "domain_event_seal",
    "QUANTOS_external_heartbeat": "external_heartbeat",
    "QUANTOS_hyperliquid_public_forward_refresh": (
        "hyperliquid_public_forward_refresh"
    ),
    "QUANTOS_backup": "l2_backup",
    "QUANTOS_mexc_market_refresh": "mexc_klines_fetch",
    "QUANTOS_okx_borrow_forward_refresh": "okx_borrow_forward_refresh",
    "QUANTOS_raw_archive_audit": "raw_archive_audit",
    "QUANTOS_scheduler_watchdog": "l2_scheduler_watchdog",
    "QUANTOS_wikimedia_attention_forward_refresh": (
        "wikimedia_attention_forward_refresh"
    ),
}
MVP_SAFE_MODULES = frozenset(MVP_EXPECTED_TASKS.values())


HARD_QUARANTINED_TASKS: dict[str, str] = {
    "QUANTOS_alpha_lifecycle_cockpit": "alpha_lifecycle_cockpit",
    "QUANTOS_alpha_maintenance": "alpha_maintenance_sweep",
    "QUANTOS_alpha_paper_cycle": "alpha_paper_cycle",
    "QUANTOS_alpha_production_workflow": "alpha_production_workflow",
    "QUANTOS_candidate_dry_run": "dry_run_candidate_multi",
    "QUANTOS_candidate_portfolio_evidence": "candidate_portfolio_evidence",
    "QUANTOS_factory_loop": "crypto_factory_loop",
    "QUANTOS_ic_panel_refresh": "ic_panel_research",
    "QUANTOS_paper_alpha_evaluation": "paper_alpha_evaluation",
    "QUANTOS_paper_realtime": "paper_realtime",
    "QUANTOS_paper_realtime_10k": "paper_realtime_10k",
    "QUANTOS_paper_research_risk_bundle": "paper_research_risk_bundle",
    "QUANTOS_register_alpha_catalog": "register_alpha_catalog",
}
HARD_QUARANTINED_MODULES = frozenset(HARD_QUARANTINED_TASKS.values())


MVP_DISABLED_TASKS: dict[str, str] = {
    "QUANTOS_forward_track": "forward_track_advance",
    "QUANTOS_gonogo_report": "gonogo_report",
    "QUANTOS_hourly_alpha_advance": "hourly_alpha_advance",
    "QUANTOS_mft_l2_runtime": "mft_l2_runtime",
    "QUANTOS_paper_health": "paper_health",
    "QUANTOS_paper_multivenue_sor": "paper_multivenue_sor",
    "QUANTOS_paper_operational_evidence": "paper_operational_evidence",
    "QUANTOS_paper_order_preview": "paper_order_preview",
    "QUANTOS_paper_shadow_auto": "paper_shadow_auto",
    "QUANTOS_paper_shadow_realtime": "paper_shadow_realtime",
    "QUANTOS_realtime_alpha_discovery": "realtime_alpha_discovery",
    "QUANTOS_realtime_alpha_execution_paper": "realtime_alpha_execution_paper",
    "QUANTOS_realtime_alpha_formal_shadow": "realtime_alpha_formal_shadow",
    "QUANTOS_realtime_alpha_forward_shadow": "realtime_alpha_forward_shadow",
    "QUANTOS_rpi_low_frequency_research": "rpi_low_frequency_research",
}
MVP_DISABLED_MODULES = frozenset(MVP_DISABLED_TASKS.values())


MVP_RESIDENT_MODULES = frozenset(
    {"binance_realtime_stream", "bybit_realtime_stream"}
)
MVP_CONDITIONAL_MODULES = frozenset(
    {
        "artifact_admission_seal",
        "domain_event_seal",
        "external_heartbeat",
        "l2_backup",
    }
)


def _validate_static_policy() -> None:
    task_groups = (
        frozenset(MVP_EXPECTED_TASKS),
        frozenset(HARD_QUARANTINED_TASKS),
        frozenset(MVP_DISABLED_TASKS),
    )
    groups = (
        MVP_SAFE_MODULES,
        HARD_QUARANTINED_MODULES,
        MVP_DISABLED_MODULES,
    )
    if tuple(map(len, task_groups)) != (26, 13, 15):
        raise RuntimeError("L2 schedule policy task cardinality is invalid")
    if tuple(map(len, groups)) != (26, 13, 15):
        raise RuntimeError("L2 schedule policy closed-set cardinality is invalid")
    if frozenset(MVP_EXPECTED_TASKS.values()) != MVP_SAFE_MODULES:
        raise RuntimeError("L2 MVP expected tasks do not equal the safe module set")
    if frozenset(HARD_QUARANTINED_TASKS.values()) != HARD_QUARANTINED_MODULES:
        raise RuntimeError("L2 hard-quarantined task map is inconsistent")
    if frozenset(MVP_DISABLED_TASKS.values()) != MVP_DISABLED_MODULES:
        raise RuntimeError("L2 MVP-disabled task map is inconsistent")
    pairs = ((0, 1), (0, 2), (1, 2))
    if any(task_groups[left] & task_groups[right] for left, right in pairs):
        raise RuntimeError("L2 schedule policy task classes overlap")
    if any(groups[left] & groups[right] for left, right in pairs):
        raise RuntimeError("L2 schedule policy module classes overlap")
    if not MVP_RESIDENT_MODULES <= MVP_SAFE_MODULES:
        raise RuntimeError("L2 resident workers must be MVP-safe")
    if not MVP_CONDITIONAL_MODULES <= MVP_SAFE_MODULES:
        raise RuntimeError("L2 conditional workers must be MVP-safe")
    if not {"hourly_alpha_advance", "mft_l2_runtime"} <= MVP_DISABLED_MODULES:
        raise RuntimeError("L2 hourly/MFT workers must remain MVP-disabled")


_validate_static_policy()


__all__ = [
    "HARD_QUARANTINED_MODULES",
    "HARD_QUARANTINED_TASKS",
    "MVP_CONDITIONAL_MODULES",
    "MVP_DISABLED_MODULES",
    "MVP_DISABLED_TASKS",
    "MVP_EXPECTED_TASKS",
    "MVP_RESIDENT_MODULES",
    "MVP_SAFE_MODULES",
]
