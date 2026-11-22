"""Regression tests for pre-trade fail-closed guards and validation math fixes."""
from __future__ import annotations

import ast
import importlib
import importlib.util
import math
import pathlib
import statistics
import sys
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_stdlib_module(module_name: str, rel_path: str):
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError:
        alias = f"_audit_isolated_{module_name.replace('.', '_')}"
        spec = importlib.util.spec_from_file_location(alias, _ROOT / rel_path)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[alias] = mod
        spec.loader.exec_module(mod)
        return mod


from services.alpha import (
    metrics as alpha_metrics,
    similarity as alpha_similarity,
    walk_forward as alpha_walk_forward,
)
try:
    from services.alpha import population_optimizer
except ModuleNotFoundError:
    import types as _types
    _fs_stub = _types.ModuleType("services.alpha.factor_set")
    _fs_stub.select_synergistic_set = lambda rets, **kw: {
        "selected": [], "selected_ids": list(rets.keys()), "dropped_redundant": []
    }
    sys.modules["services.alpha.factor_set"] = _fs_stub
    from services.alpha import population_optimizer
from services.validation import cpcv, deflated_sharpe, performance, walk_forward

impact = _load_stdlib_module("services.execution.impact", "services/execution/impact.py")
quantization = _load_stdlib_module("services.execution.quantization", "services/execution/quantization.py")
pf_optimizer = _load_stdlib_module("services.portfolio.optimizer", "services/portfolio/optimizer.py")
pf_risk_model = _load_stdlib_module("services.portfolio.risk_model", "services/portfolio/risk_model.py")


class ValidationMathRegressionTests(unittest.TestCase):
    def test_returns_from_equity_skips_non_positive_and_non_finite_bases(self) -> None:
        # Negative equity must never sign-flip a further loss into a positive return.
        self.assertEqual(performance.returns_from_equity([100.0, 50.0, -10.0, -20.0]), [-0.5, -1.2])
        self.assertEqual(performance.returns_from_equity([100.0, float("nan"), 110.0]), [])

    def test_sharpe_uses_sample_stdev_and_handles_constant_negative_and_nan(self) -> None:
        rets = [0.01, -0.02, 0.03, 0.01]
        expected = (statistics.fmean(rets) / statistics.stdev(rets)) * math.sqrt(252)
        self.assertAlmostEqual(performance.sharpe(rets, 252), expected, places=12)
        self.assertEqual(performance.sharpe([-0.01, -0.01, -0.01]), -math.inf)
        self.assertEqual(performance.sharpe([0.01, 0.01, 0.01]), math.inf)
        self.assertTrue(math.isnan(performance.sharpe([0.01, float("nan"), 0.02])))

    def test_generalization_gap_accepts_periods_per_year_and_rejects_nan(self) -> None:
        series = [0.02, 0.01, 0.02, 0.01] * 10 + [-0.01, -0.02, -0.01, -0.02] * 10
        g252 = performance.generalization_gap(series, periods_per_year=252)
        g365 = performance.generalization_gap(series, periods_per_year=365)
        self.assertIsNotNone(g252)
        self.assertIsNotNone(g365)
        self.assertGreaterEqual(g365, g252)
        corrupted = list(series)
        corrupted[5] = float("nan")
        self.assertIsNone(performance.generalization_gap(corrupted))

    def test_min_track_record_length_guards_invalid_confidence_and_variance(self) -> None:
        with self.assertRaises(ValueError):
            deflated_sharpe.min_track_record_length(0.5, 0.1, confidence=1.0)
        with self.assertRaises(ValueError):
            deflated_sharpe.min_track_record_length(0.5, 0.1, confidence=0.0)
        with self.assertRaises(ValueError):
            deflated_sharpe.min_track_record_length(2.0, 0.1, skew=10.0, kurt=1.5)

    def test_pbo_cscv_mid_rank_consistency(self) -> None:
        genuine = [[3.0, 1.0, 0.5] for _ in range(6)]
        self.assertEqual(deflated_sharpe.pbo_cscv(genuine), 0.0)
        overfit = [
            [5, 0, 0], [0, 5, 0], [0, 0, 5],
            [5, 0, 0], [0, 5, 0], [0, 0, 5],
        ]
        self.assertGreaterEqual(deflated_sharpe.pbo_cscv(overfit), 0.5)
        # Bailey et al. P(logit <= 0): symmetric 2-strategy tie is not strictly above median.
        alternating = [[5.0, 0.0], [0.0, 5.0], [5.0, 0.0], [0.0, 5.0]]
        self.assertGreaterEqual(deflated_sharpe.pbo_cscv(alternating), 0.5)

    def test_walk_forward_embargo_and_validation(self) -> None:
        splits = walk_forward.walk_forward_splits(n=12, train_size=4, test_size=2, embargo=2)
        self.assertEqual(splits, [([0, 1, 2, 3], [6, 7]), ([2, 3, 4, 5], [8, 9]), ([4, 5, 6, 7], [10, 11])])
        for train, test in splits:
            self.assertEqual(min(test) - max(train) - 1, 2)
        with self.assertRaises(ValueError):
            walk_forward.walk_forward_splits(n=12, train_size=4, test_size=2, embargo=-1)
        with self.assertRaises(ValueError):
            walk_forward.walk_forward_splits(n=12, train_size=4, test_size=2, step=0)
        with self.assertRaises(ValueError):
            walk_forward.purged_kfold(n=12, k=3, embargo=-1)

    def test_cpcv_summary_remains_deterministic_with_sample_stdev(self) -> None:
        t = 60
        good = [0.010 + 0.001 * ((i % 3) - 1) for i in range(t)]
        noise = [0.02 * (1 if (i + 1) % 2 == 0 else -1) for i in range(t)]
        summary = cpcv.cpcv_summary({"good": good, "noise": noise}, n_groups=6, k_test=2)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["n_splits"], 15)
        self.assertGreater(summary["frac_positive"], 0.8)
        const = cpcv.cpcv_summary({"const": [0.01] * 60}, n_groups=6, k_test=2)
        self.assertIsNotNone(const)
        self.assertTrue(math.isnan(const["mean_oos_sharpe"]))

    def test_portfolio_cap_weights_and_long_short_rebalance(self) -> None:
        # Excess weight must redistribute equally when uncapped weights start at 0.
        capped = pf_optimizer.cap_weights([1.0, 0.0, 0.0], max_weight=0.5)
        self.assertAlmostEqual(sum(capped), 1.0, places=9)
        self.assertAlmostEqual(capped[0], 0.5, places=9)
        self.assertAlmostEqual(capped[1], 0.25, places=9)
        self.assertAlmostEqual(capped[2], 0.25, places=9)
        with self.assertRaises(ValueError):
            pf_optimizer.cap_weights([0.5, 0.5], max_weight=float("nan"))

        # Long-short book must never divide by tiny net sum (+0.05) and explode leverage.
        prev = {"a": 0.5, "b": -0.5}
        target = {"a": 0.55, "b": -0.50}
        new_w, turn = pf_optimizer.cost_aware_rebalance(prev, target, no_trade_band=0.02)
        self.assertAlmostEqual(new_w["a"], 0.55, places=9)
        self.assertAlmostEqual(new_w["b"], -0.50, places=9)
        self.assertAlmostEqual(turn, 0.05, places=9)

    def test_portfolio_risk_model_guards_nan_and_min_overlap(self) -> None:
        with self.assertRaises(ValueError):
            pf_risk_model.covariance_matrix({"a": [0.01, float("nan"), 0.02]}, min_overlap=2)
        with self.assertRaises(ValueError):
            pf_risk_model.covariance_matrix({"a": [0.01, 0.02]}, min_overlap=1)
        self.assertEqual(pf_risk_model.percent_risk_contributions([1.0], [[-0.01]]), [0.0])

    def test_alpha_metrics_similarity_walk_forward_and_population_guards(self) -> None:
        self.assertEqual(alpha_metrics.clean([0.1, True, False, float("nan"), 0.2]), [0.1, 0.2])
        with self.assertRaises(ValueError):
            alpha_metrics.ic_autocorr([0.1, 0.2, 0.3, 0.4], lag=0)
        self.assertEqual(alpha_similarity.return_similarity([0.1, float("nan"), 0.2], [0.1, 0.2, 0.3]), 0.0)
        self.assertEqual(alpha_similarity.holdings_overlap({"a": float("nan")}, {"a": 1.0}), 0.0)
        self.assertFalse(alpha_walk_forward.walk_forward_oos([0.01] * 20, min_positive_frac=0.0))
        self.assertFalse(alpha_walk_forward.walk_forward_oos([0.01] * 20, min_positive_frac=float("nan")))
        # Wipeout (-1.5 then -1.5) must stay at 100% drawdown instead of flipping equity positive.
        self.assertAlmostEqual(population_optimizer.max_drawdown([-1.5, -1.5]), 1.0, places=9)
        self.assertAlmostEqual(population_optimizer.max_drawdown([0.1, float("nan"), -0.2]), 0.2, places=9)

    def test_execution_quantization_and_impact_guards(self) -> None:
        self.assertAlmostEqual(quantization.round_to_step(-1.2345, 0.001), -1.234, places=9)
        with self.assertRaises(ValueError):
            quantization.round_to_step(float("nan"), 0.01)
        self.assertFalse(quantization.meets_min_notional(float("nan"), 100.0, 0.0))
        self.assertFalse(quantization.meets_min_notional(-1.0, 100.0, 0.0))
        self.assertTrue(quantization.meets_min_notional(0.0, 0.0, 0.0))
        with self.assertRaises(ValueError):
            impact.AlmgrenChrissImpact(temp_coef=-1.0)
        imp = impact.AlmgrenChrissImpact()
        with self.assertRaises(ValueError):
            imp.temporary_bps(float("nan"), 100_000.0)

    def test_drawdown_controller_fail_closed_guards(self) -> None:
        import types
        from unittest import mock

        try:
            ba_mod = _load_stdlib_module(
                "services.execution.backtest_authority",
                "services/execution/backtest_authority.py",
            )
        except FileNotFoundError:
            ba_mod = types.ModuleType("services.execution.backtest_authority")
            ba_mod.LEGACY_INVALIDATION_CODE = "INVALIDATED_PERP_LEDGER_V1"
            ba_mod.is_authoritative_backtest_record = lambda _rec: False
        exec_pkg = types.ModuleType("services.execution")
        exec_pkg.backtest_authority = ba_mod
        with mock.patch.dict(
            sys.modules,
            {
                "services.execution": exec_pkg,
                "services.execution.backtest_authority": ba_mod,
            },
        ):
            dc_spec = importlib.util.spec_from_file_location(
                "_audit_live_drawdown_controller",
                _ROOT / "live/execution/drawdown_controller.py",
            )
            assert dc_spec is not None and dc_spec.loader is not None
            dc = importlib.util.module_from_spec(dc_spec)
            sys.modules["_audit_live_drawdown_controller"] = dc
            try:
                dc_spec.loader.exec_module(dc)
            finally:
                sys.modules.pop("_audit_live_drawdown_controller", None)
        # Critical: NaN/Inf/bool drawdown_pct must fail closed to 0.0 (never 1.0 full leverage).
        for bad in (float("nan"), float("inf"), float("-inf"), True, False):
            reading = dc.drawdown_component(bad)
            self.assertEqual(reading.value, 0.0)
            self.assertEqual(reading.status, "UNKNOWN")
        # Thresholds & ComponentReading reject non-finite / bool inputs.
        with self.assertRaises(ValueError):
            dc.DrawdownThresholds(halt_drawdown=float("inf"))
        with self.assertRaises(ValueError):
            dc.DrawdownThresholds(caution_ceiling=float("nan"))
        with self.assertRaises(ValueError):
            dc.ComponentReading("x", float("nan"), "OK", "bad")
        # Recovery helpers treat corrupted NaN/<=0 observations fail-closed.
        self.assertEqual(
            dc.consecutive_below_caution_count([99.0, float("nan"), 99.0], peak_equity=100.0, caution_drawdown=0.05),
            1,
        )
        self.assertEqual(dc.observations_since_new_low([95.0, 96.0, float("nan")]), 0)
        # Venue health rejects negative/bool total and clamps available <= total.
        vh_bad, inc_bad = dc.venue_health_component(5, -1)
        self.assertEqual(vh_bad.status, "UNKNOWN")
        self.assertFalse(inc_bad)
        vh_over, inc_over = dc.venue_health_component(20, 10)
        self.assertEqual(vh_over.value, 1.0)
        self.assertFalse(inc_over)
        # Future-dated degradation artifact is explicitly flagged as future & UNKNOWN.
        future_payload = {"generated_at_ms": 2_000_000, "reports": []}
        deg = dc.degradation_component_from_artifact(future_payload, "s", "binance", now_ms=1_000_000)
        self.assertEqual(deg.status, "UNKNOWN")
        self.assertIn("future", deg.reason)

    def test_live_pricing_tca_filters_slicer_emergency_and_risk_monitor_guards(self) -> None:
        from dataclasses import dataclass
        from enum import Enum
        import types
        from unittest import mock

        class _OrderSide(str, Enum):
            BUY = "BUY"
            SELL = "SELL"

        @dataclass(frozen=True)
        class _ExchangeFilters:
            instrument_id: str
            step_size: float
            min_qty: float
            min_notional: float
            tick_size: float = 0.0

        @dataclass(frozen=True)
        class _LiveOrderRequest:
            instrument_id: str
            side: _OrderSide
            quantity: float
            client_order_id: str
            order_type: str = "MARKET"
            limit_price: float | None = None
            post_only: bool = False
            reduce_only: bool = False

        @dataclass(frozen=True)
        class _RiskBreach:
            breached: bool
            reasons: tuple[str, ...]
            drawdown_pct: float
            gross_exposure: float
            net_exposure: float

        def _exec_with_shims(rel_path: str, alias: str, extra_modules: dict):
            with mock.patch.dict(sys.modules, extra_modules):
                spec = importlib.util.spec_from_file_location(alias, _ROOT / rel_path)
                assert spec is not None and spec.loader is not None
                mod = importlib.util.module_from_spec(spec)
                sys.modules[alias] = mod
                try:
                    spec.loader.exec_module(mod)
                finally:
                    sys.modules.pop(alias, None)
                return mod

        trading_mod = types.ModuleType("quantos.contracts.trading")
        trading_mod.OrderSide = _OrderSide
        contracts_pkg = types.ModuleType("quantos.contracts")
        contracts_pkg.trading = trading_mod
        live_contracts = types.ModuleType("live.contracts")
        live_contracts.ExchangeFilters = _ExchangeFilters
        live_contracts.LiveOrderRequest = _LiveOrderRequest
        exec_quant_pkg = types.ModuleType("services.execution")
        exec_quant_pkg.quantization = quantization

        base_shims = {
            "quantos.contracts": contracts_pkg,
            "quantos.contracts.trading": trading_mod,
            "live.contracts": live_contracts,
            "services.execution": exec_quant_pkg,
            "services.execution.quantization": quantization,
        }

        filters_mod = _exec_with_shims("live/execution/filters.py", "_audit_live_filters", base_shims)
        pricing_mod = _exec_with_shims(
            "live/execution/pricing.py",
            "_audit_live_pricing",
            {**base_shims, "live.execution.filters": filters_mod},
        )
        tca_mod = _exec_with_shims("live/execution/tca.py", "_audit_live_tca", base_shims)
        slicer_mod = _exec_with_shims(
            "live/execution/slicer.py",
            "_audit_live_slicer",
            {**base_shims, "live.execution.filters": filters_mod},
        )

        # Pricing: BUY sub-tick floor to 0.0 returns None; NaN max_deviation_pct fails closed.
        self.assertIsNone(
            pricing_mod.limit_price_for(_OrderSide.BUY, anchor=0.004, band_pct=0.0, tick_size=0.01)
        )
        self.assertIsNone(
            pricing_mod.limit_price_for(_OrderSide.BUY, anchor=100.0, band_pct=float("nan"), tick_size=0.1)
        )
        anchor, src = pricing_mod.resolve_anchor(
            {"bid": 100.0, "ask": 100.2}, decision_price=100.0, max_deviation_pct=float("nan")
        )
        self.assertEqual(src, "decision_price_fallback")
        self.assertAlmostEqual(anchor, 100.0)
        with self.assertRaises(ValueError):
            pricing_mod.LimitPricing(band_pct=float("nan"))

        # TCA: NaN/Inf/bool rejected in slippage_bps and isolated in tca_summary.
        self.assertIsNone(tca_mod.slippage_bps(_OrderSide.BUY, decision_price=float("nan"), fill_price=100.0))
        self.assertIsNone(tca_mod.slippage_bps(_OrderSide.BUY, decision_price=100.0, fill_price=float("inf")))
        self.assertIsNone(tca_mod.slippage_bps(_OrderSide.BUY, decision_price=True, fill_price=100.0))
        summary = tca_mod.tca_summary([
            {"instrument_id": "BTC", "slippage_bps": 5.0, "fill_price": 100.0, "quantity": 2.0},
            {"instrument_id": "BTC", "slippage_bps": float("nan"), "fill_price": 100.0, "quantity": 2.0},
            {"instrument_id": "ETH", "slippage_bps": 10.0, "fill_price": float("nan"), "quantity": 1.0},
        ])
        self.assertEqual(summary["n_fills"], 3)
        self.assertEqual(summary["n_with_slippage"], 2)
        self.assertAlmostEqual(summary["mean_slippage_bps"], 7.5)
        self.assertAlmostEqual(summary["notional_weighted_slippage_bps"], 5.0)

        # Filters: NaN price or NaN qty dropped instead of bypassing min_notional or crashing.
        btc_filt = _ExchangeFilters(instrument_id="BTC", step_size=0.001, min_qty=0.001, min_notional=10.0)
        adj, dropped = filters_mod.apply_exchange_filters(
            [("BTC", _OrderSide.BUY, 1.0)],
            {"BTC": btc_filt},
            {"BTC": float("nan")},
            strategy_id="s",
            decided_ts=1,
        )
        self.assertEqual(adj, [])
        self.assertEqual(dropped, [("BTC", "no_price")])
        adj2, dropped2 = filters_mod.apply_exchange_filters(
            [("BTC", _OrderSide.BUY, float("nan"))],
            {"BTC": btc_filt},
            {"BTC": 60000.0},
            strategy_id="s",
            decided_ts=1,
        )
        self.assertEqual(adj2, [])
        self.assertEqual(dropped2, [("BTC", "below_min_qty")])

        # Slicer: rejects bool/NaN/Inf parameters.
        req = _LiveOrderRequest(instrument_id="BTC", side=_OrderSide.BUY, quantity=1.0, client_order_id="c1")
        with self.assertRaises(ValueError):
            slicer_mod.plan_slices(req, n_slices=True)
        with self.assertRaises(ValueError):
            slicer_mod.plan_slices(req, max_child_notional=float("nan"), price=60000.0)
        with self.assertRaises(ValueError):
            slicer_mod.plan_slices(req, max_child_notional=1000.0, price=float("inf"))

        # Risk monitor & emergency flatten target guards.
        ks_stub = types.ModuleType("services.risk.pre_trade.kill_switch")
        ks_stub.KillSwitch = object
        ks_rt_stub = types.ModuleType("services.risk.pre_trade.kill_switch_runtime")
        ks_rt_stub.arm_and_record = lambda *a, **k: None
        ks_st_stub = types.ModuleType("services.risk.pre_trade.kill_switch_store")
        ks_st_stub.KillSwitchStateConflict = RuntimeError
        alpha_stub = types.ModuleType("quantos.contracts.alpha")
        alpha_stub.ActorKind = types.SimpleNamespace(SYSTEM="SYSTEM")
        alpha_stub.Actor = lambda **k: types.SimpleNamespace(**k)
        rm_mod = _exec_with_shims(
            "live/execution/risk_monitor.py",
            "_audit_live_risk_monitor",
            {
                **base_shims,
                "quantos.contracts.alpha": alpha_stub,
                "services.risk.pre_trade.kill_switch": ks_stub,
                "services.risk.pre_trade.kill_switch_runtime": ks_rt_stub,
                "services.risk.pre_trade.kill_switch_store": ks_st_stub,
            },
        )
        with self.assertRaises(ValueError):
            rm_mod.compute_flatten_orders({"BTC": 1.0}, {"BTC": 60000.0}, target_gross_exposure=-100.0)
        with self.assertRaises(ValueError):
            rm_mod.compute_flatten_orders({"BTC": 1.0}, {"BTC": 60000.0}, target_gross_exposure=float("nan"))
        # NaN position in one leg must not poison gross and suppress flattening of valid legs.
        orders = rm_mod.compute_flatten_orders(
            {"BTC": 1.0, "BAD": float("nan")},
            {"BTC": 60000.0, "BAD": 100.0},
            target_gross_exposure=30000.0,
        )
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0][0], "BTC")
        self.assertAlmostEqual(orders[0][2], 0.5)

        cfg_stub = types.ModuleType("quantos.contracts.config")
        cfg_stub.SafetyViolation = RuntimeError
        ledger_stub = types.ModuleType("live.execution.position_ledger")
        ledger_stub.LiveLedger = object
        venue_stub = types.ModuleType("live.exchange.base")
        venue_stub.VenueAdapter = object
        pipe_stub = types.ModuleType("live.pipeline")
        pipe_stub.DEFAULT_MAX_CONFIRM_POLLS = 1
        pipe_stub._cancel_and_requery = lambda *a, **k: None
        pipe_stub._confirm_fill = lambda *a, **k: None
        pipe_stub._det_id = lambda *a, **k: "id"
        pipe_stub._record_tca_failsoft = lambda *a, **k: None
        audit_stub = types.ModuleType("services.monitoring.audit_log")
        audit_stub.GlobalAuditLog = object
        emg_mod = _exec_with_shims(
            "live/execution/emergency.py",
            "_audit_live_emergency",
            {
                **base_shims,
                "quantos.contracts.config": cfg_stub,
                "live.execution.filters": filters_mod,
                "live.execution.position_ledger": ledger_stub,
                "live.execution.risk_monitor": rm_mod,
                "live.exchange.base": venue_stub,
                "live.pipeline": pipe_stub,
                "services.monitoring.audit_log": audit_stub,
            },
        )
        breach = _RiskBreach(
            breached=True,
            reasons=("gross_exposure_exceeded:2500>2000",),
            drawdown_pct=0.0,
            gross_exposure=2500.0,
            net_exposure=100.0,
        )
        self.assertIsNone(
            emg_mod.flatten_target_for_breach(breach, max_gross_exposure=float("nan"), max_net_exposure=300.0)
        )
        self.assertIsNone(
            emg_mod.flatten_target_for_breach(breach, max_gross_exposure=-100.0, max_net_exposure=300.0)
        )

    def test_research_risk_engines_and_data_quality_guards(self) -> None:
        from dataclasses import dataclass
        import types
        from unittest import mock

        @dataclass(frozen=True)
        class _Check:
            name: str
            passed: bool
            detail: str = ""

        @dataclass(frozen=True)
        class _Report:
            dataset_id: str
            checks: tuple[_Check, ...]
            version: str = ""
            missing_count: int = 0
            duplicate_count: int = 0
            out_of_order_count: int = 0

            @property
            def passed(self) -> bool:
                return all(c.passed for c in self.checks)

        data_contracts = types.ModuleType("quantos.contracts.data")
        data_contracts.DataQualityCheck = _Check
        data_contracts.DataQualityReport = _Report
        qual_contracts = types.ModuleType("quantos.contracts.quality")
        qual_contracts.check_volume_dispersion = lambda *a, **k: _Check("volume_dispersion", True)

        with mock.patch.dict(
            sys.modules,
            {
                "quantos.contracts.data": data_contracts,
                "quantos.contracts.quality": qual_contracts,
            },
        ):
            q_spec = importlib.util.spec_from_file_location("_audit_data_quality", _ROOT / "services/data/quality.py")
            assert q_spec is not None and q_spec.loader is not None
            q_mod = importlib.util.module_from_spec(q_spec)
            sys.modules["_audit_data_quality"] = q_mod
            q_spec.loader.exec_module(q_mod)
            sys.modules.pop("_audit_data_quality", None)

            pq_spec = importlib.util.spec_from_file_location(
                "_audit_panel_quality", _ROOT / "services/data/panel_quality.py"
            )
            assert pq_spec is not None and pq_spec.loader is not None
            pq_mod = importlib.util.module_from_spec(pq_spec)
            sys.modules["_audit_panel_quality"] = pq_mod
            pq_spec.loader.exec_module(pq_mod)
            sys.modules.pop("_audit_panel_quality", None)

        # OOM regression: 10^12 span with interval 1 must compute exact missing_count in O(|present|) memory!
        rep = q_mod.build_ingestion_report(
            "huge_span",
            {"BTC": [0, 1_000_000_000_000]},
            expected_interval=1,
        )
        self.assertEqual(rep.missing_count, 999_999_999_999)
        self.assertFalse(rep.passed)
        with self.assertRaises(ValueError):
            q_mod.build_ingestion_report("bad", {"BTC": [True, 2]})

        # panel_quality: NaN freshest_days and NaN weight must fail closed.
        pq_nan_stale = pq_mod.gate_decision_inputs(
            weights={"BTC": 1.0},
            prices={"BTC": 60000.0},
            decided_ts=100,
            now=200,
            max_decision_age_ms=1000,
            staleness={
                "prices": {"freshest_days": float("nan")},
                "funding": {"freshest_days": 1},
                "oi_metrics": {"freshest_days": 1},
                "perp_mark_prices": {"freshest_days": 1},
            },
            quality_evidence_current=True,
        )
        self.assertFalse(pq_nan_stale.passed)
        pq_nan_weight = pq_mod.gate_decision_inputs(
            weights={"BTC": float("nan")},
            prices={"BTC": 60000.0},
            decided_ts=100,
            now=200,
            max_decision_age_ms=1000,
            quality_evidence_current=True,
        )
        self.assertFalse(pq_nan_weight.passed)

        # Research risk engines: positive-tail CVaR=0, NaN weight fails Concentration, non-positive equity fails Drawdown.
        models_mod = _load_stdlib_module(
            "services.risk.research_risk.domain.models",
            "services/risk/research_risk/domain/models.py",
        )
        alpha_stub = types.ModuleType("quantos.contracts.alpha")
        alpha_stub.AlphaCluster = object
        quantos_pkg = types.ModuleType("quantos")
        scenario_stub = types.ModuleType("quantos.scenario")
        scenario_stub.CRYPTO_CRISES = ()
        scenario_stub.iso_to_ms = lambda _s: 0
        scenario_stub.max_drawdown_window = lambda *a, **k: (0.0, 0, 0)
        scenario_stub.window_stats = lambda *a, **k: (0.0, 0)
        quantos_pkg.scenario = scenario_stub
        domain_pkg = types.ModuleType("_audit_rr_domain")
        domain_pkg.__path__ = [str(_ROOT / "services/risk/research_risk/domain")]
        with mock.patch.dict(
            sys.modules,
            {
                "quantos": quantos_pkg,
                "quantos.scenario": scenario_stub,
                "quantos.contracts.alpha": alpha_stub,
                "_audit_rr_domain": domain_pkg,
                "_audit_rr_domain.models": models_mod,
            },
        ):
            rm_spec = importlib.util.spec_from_file_location(
                "_audit_rr_domain.risk_modules",
                _ROOT / "services/risk/research_risk/domain/risk_modules.py",
            )
            assert rm_spec is not None and rm_spec.loader is not None
            rrm_mod = importlib.util.module_from_spec(rm_spec)
            sys.modules["_audit_rr_domain.risk_modules"] = rrm_mod
            rm_spec.loader.exec_module(rrm_mod)
            sys.modules.pop("_audit_rr_domain.risk_modules", None)

        cfg = models_mod.RiskConfig(max_portfolio_volatility=10.0, max_cvar_95=0.05, min_effective_number_of_bets=0)
        # All-positive returns must have 0.0 CVaR loss (not abs(+0.10) == 0.10!).
        mkt = rrm_mod.MarketRiskEngine().evaluate([0.10, 0.11, 0.12, 0.13, 0.14], cfg)
        self.assertEqual(mkt.cvar_95, 0.0)
        self.assertTrue(mkt.passed)
        # NaN return fails closed.
        self.assertFalse(rrm_mod.MarketRiskEngine().evaluate([0.01, float("nan"), 0.02], cfg).passed)
        # NaN weight fails Concentration even when min_effective_number_of_bets == 0.
        self.assertFalse(
            rrm_mod.ConcentrationRiskEngine().evaluate({"a": 0.1, "b": float("nan")}, None, cfg).passed
        )
        # Non-positive starting equity fails Drawdown instead of sign-flipping negative drawdown.
        self.assertFalse(rrm_mod.DrawdownRiskEngine().evaluate([-100.0, -200.0], cfg).passed)
        # NaN in alpha_returns fails Correlation.
        self.assertFalse(
            rrm_mod.CorrelationRiskEngine().evaluate({"a": [0.01, float("nan")], "b": [0.01, 0.02]}, cfg).passed
        )

    def test_ai_agent_search_and_reward_hardening(self) -> None:
        import types

        reward_mod = _load_stdlib_module(
            "services.intelligence.reward",
            "services/intelligence/reward.py",
        )
        # NaN/Inf/bool Sharpe must never poison bandit/MCTS rewards with NaN.
        self.assertEqual(reward_mod._clamp01(float("nan")), 0.0)
        self.assertEqual(reward_mod._clamp01(float("inf")), 0.0)
        self.assertEqual(reward_mod._clamp01(True), 0.0)
        self.assertAlmostEqual(reward_mod._clamp01(0.42), 0.42)

        bad_ev = types.SimpleNamespace(deflated_sharpe=float("nan"), net_sharpe=1.5, verdict="PASS")
        self.assertEqual(reward_mod.evaluation_reward(bad_ev), 0.0)
        bad_ev2 = types.SimpleNamespace(deflated_sharpe=0.9, net_sharpe=float("inf"), verdict="PASS")
        self.assertEqual(reward_mod.evaluation_reward(bad_ev2), 0.0)
        good_ev = types.SimpleNamespace(deflated_sharpe=0.8, net_sharpe=1.0, verdict="PASS")
        self.assertAlmostEqual(reward_mod.evaluation_reward(good_ev), 0.9)

        # UCBSearch rejects non-finite/bool c, rewards, batch_size, and corrupt snapshots.
        ucb_mod = _load_stdlib_module(
            "services.intelligence.search_strategy",
            "services/intelligence/search_strategy.py",
        )

        class _DummySpace:
            def hypotheses(self):
                return [("a", 1), ("b", 2)]

            def arms_of(self, h):
                return [("f", h[0]), ("w", h[1])]

        with self.assertRaises(ValueError):
            ucb_mod.UCBSearch(_DummySpace(), c=float("nan"))
        with self.assertRaises(ValueError):
            ucb_mod.UCBSearch(_DummySpace(), c=-0.5)
        with self.assertRaises(ValueError):
            ucb_mod.UCBSearch(_DummySpace(), c=True)

        bandit = ucb_mod.UCBSearch(_DummySpace())
        with self.assertRaises(ValueError):
            bandit.propose(True)
        with self.assertRaises(ValueError):
            bandit.update(("a", 1), float("nan"))
        with self.assertRaises(ValueError):
            bandit.update(("a", 1), True)
        with self.assertRaises(ValueError):
            bandit.restore({
                "version": 1,
                "pulls": [[["f", "a"], 0]],
                "reward_sum": [[["f", "a"], 0.5]],
                "n_evaluated": 1,
                "proposed": [],
            })

        # MCTSAlphaSearch rejects invalid c and boolean iterations, guards NaN values in _uct/_backprop.
        pkg_intel = types.ModuleType("services.intelligence")
        pkg_intel.__path__ = [str(_ROOT / "services/intelligence")]
        sys.modules["services.intelligence"] = pkg_intel
        sys.modules["services.intelligence.reward"] = reward_mod
        try:
            mcts_mod = _load_stdlib_module(
                "services.intelligence.mcts_search",
                "services/intelligence/mcts_search.py",
            )
        finally:
            sys.modules.pop("services.intelligence.mcts_search", None)
            sys.modules.pop("services.intelligence.reward", None)
            sys.modules.pop("services.intelligence", None)

        with self.assertRaises(ValueError):
            mcts_mod.MCTSAlphaSearch(lambda _: [], c=float("nan"))
        with self.assertRaises(ValueError):
            mcts_mod.MCTSAlphaSearch(lambda _: [], c=-1.0)
        searcher = mcts_mod.MCTSAlphaSearch(lambda _: [], inner_loop=object())
        with self.assertRaises(ValueError):
            searcher.run(["g1"], iterations=True, diagnostic_only=True)
        parent = mcts_mod._Node(None, None)
        parent.visits = 5
        child = mcts_mod._Node("g1", parent)
        child.visits = 2
        child.value = float("nan")
        self.assertTrue(math.isfinite(searcher._uct(parent, child)))

    def test_mandate_review_queue_retry_and_envelope_hardening(self) -> None:
        import types

        mandate_mod = _load_stdlib_module("live.governance.mandate", "live/governance/mandate.py")
        OrderSide = mandate_mod.OrderSide
        mk_order = lambda inst="BTCUSDT", side=OrderSide.BUY, qty=0.1, otype="MARKET", lp=None: (
            types.SimpleNamespace(
                instrument_id=inst,
                side=side,
                quantity=qty,
                order_type=otype,
                limit_price=lp,
                client_order_id="cid-1",
            )
        )
        base_mandate = mandate_mod.Mandate(
            mandate_id="m1",
            venue="testnet",
            exchange="binance",
            allowed_instruments=frozenset({"BTCUSDT"}),
            max_single_order_notional=10000.0,
            max_gross_notional=10000.0,
            max_net_notional=10000.0,
            per_instrument_max_notional=(("BTCUSDT", 10000.0),),
            expires_at=2000,
        )
        # Valid order passes AUTO.
        ok = mandate_mod.evaluate_mandate(
            [mk_order()], {"BTCUSDT": 50000.0}, base_mandate,
            venue="testnet", exchange="binance", now=1000,
        )
        self.assertEqual(ok.tier, mandate_mod.AUTO)

        # CRITICAL: NaN per-instrument cap must NOT bypass to AUTO.
        nan_cap_mandate = mandate_mod.Mandate(
            mandate_id="m2",
            venue="testnet",
            exchange="binance",
            allowed_instruments=frozenset({"BTCUSDT"}),
            max_single_order_notional=10000.0,
            max_gross_notional=10000.0,
            max_net_notional=10000.0,
            per_instrument_max_notional=(("BTCUSDT", float("nan")),),
            expires_at=2000,
        )
        res_nan_cap = mandate_mod.evaluate_mandate(
            [mk_order()], {"BTCUSDT": 50000.0}, nan_cap_mandate,
            venue="testnet", exchange="binance", now=1000,
        )
        self.assertEqual(res_nan_cap.tier, mandate_mod.NEEDS_HUMAN_REVIEW)

        # CRITICAL: NaN current_positions must NOT poison projected gross/net and bypass to AUTO.
        res_nan_pos = mandate_mod.evaluate_mandate(
            [mk_order()], {"BTCUSDT": 50000.0}, base_mandate,
            venue="testnet", exchange="binance", now=1000,
            current_positions={"BTCUSDT": float("nan")},
        )
        self.assertEqual(res_nan_pos.tier, mandate_mod.NEEDS_HUMAN_REVIEW)

        # CRITICAL: NaN quantity, bool price, or unknown side must fail closed.
        for bad_order, bad_prices in (
            (mk_order(qty=float("nan")), {"BTCUSDT": 50000.0}),
            (mk_order(qty=True), {"BTCUSDT": 50000.0}),
            (mk_order(side="INVALID"), {"BTCUSDT": 50000.0}),
            (mk_order(), {"BTCUSDT": True}),
        ):
            res = mandate_mod.evaluate_mandate(
                [bad_order], bad_prices, base_mandate,
                venue="testnet", exchange="binance", now=1000,
            )
            self.assertEqual(res.tier, mandate_mod.NEEDS_HUMAN_REVIEW)

        # ReviewQueue hardening: rejects empty reviewer, non-finite qty, double review.
        rq_mod = _load_stdlib_module("live.governance.review_queue", "live/governance/review_queue.py")
        rq = rq_mod.ReviewQueue(":memory:", create=True)
        try:
            with self.assertRaises(ValueError):
                rq.record_batch("testnet", decided_ts=1, orders=[mk_order(qty=float("nan"))], reasons=["r"], recorded_at=1)
            rq.record_batch("testnet", decided_ts=1, orders=[mk_order()], reasons=["cap"], recorded_at=1)
            row_id = rq.pending("testnet")[0]["id"]
            with self.assertRaises(ValueError):
                rq.mark_reviewed(row_id, status="APPROVED", reviewer="   ", at=2)
            rq.mark_reviewed(row_id, status="REJECTED", reviewer="alice", at=2)
            with self.assertRaises(ValueError):
                rq.mark_reviewed(row_id, status="APPROVED", reviewer="bob", at=3)
        finally:
            rq.close()

        # Retry hardening: negative/bool max_retries and NaN/negative base_delay rejected cleanly.
        retry_mod = _load_stdlib_module("live.exchange.retry", "live/exchange/retry.py")
        with self.assertRaises(ValueError):
            retry_mod.call_with_backoff(lambda: 1, is_rate_limited=lambda _: False, max_retries=-1)
        with self.assertRaises(ValueError):
            retry_mod.call_with_backoff(lambda: 1, is_rate_limited=lambda _: False, max_retries=True)
        with self.assertRaises(ValueError):
            retry_mod.call_with_backoff(lambda: 1, is_rate_limited=lambda _: False, base_delay=float("nan"))

        # SignedMandateEnvelope: canonical digest, tamper detection, replay protection, external verifier.
        env_mod = _load_stdlib_module("live.governance.mandate_envelope", "live/governance/mandate_envelope.py")
        payload = env_mod.MandateEnvelopePayload(
            mandate_id="m-env-1",
            strategy_id="btc_momentum_v2",
            strategy_version_hash="abc123def456",
            venue="testnet",
            exchange="binance",
            allowed_instruments=("BTCUSDT",),
            max_single_order_notional=1000.0,
            max_gross_notional=5000.0,
            max_net_notional=3000.0,
            per_instrument_max_notional=(("BTCUSDT", 5000.0),),
            issued_at_ms=1_000,
            expires_at_ms=10_000,
            nonce="unique-nonce-000001",
        )
        env = env_mod.SignedMandateEnvelope(
            payload=payload,
            claimed_payload_sha256=payload.payload_sha256,
            signer_id="human-risk-officer",
            signer_actor_kind="HUMAN",
            key_id="yubikey-01",
            signature_hex="deadbeef0123456789abcdef",
        )
        # Missing verifier always fails closed.
        no_ver = env_mod.verify_mandate_envelope(
            env, now_ms=5_000, expected_venue="testnet", expected_exchange="binance", verifier=None
        )
        self.assertFalse(no_ver.valid)
        self.assertIn("external_signature_verifier_not_integrated", no_ver.reasons)

        # Agent signer rejected even with a passing verifier.
        class _AcceptVerifier:
            def verify(self, *, key_id: str, payload_sha256: str, signature_hex: str) -> bool:
                return key_id == "yubikey-01" and signature_hex == "deadbeef0123456789abcdef"

        agent_env = env_mod.SignedMandateEnvelope(
            payload=payload,
            claimed_payload_sha256=payload.payload_sha256,
            signer_id="agent-01",
            signer_actor_kind="AGENT",
            key_id="yubikey-01",
            signature_hex="deadbeef0123456789abcdef",
        )
        agent_res = env_mod.verify_mandate_envelope(
            agent_env, now_ms=5_000, expected_venue="testnet", expected_exchange="binance",
            verifier=_AcceptVerifier(),
        )
        self.assertFalse(agent_res.valid)

        # Valid human envelope passes once, then replay is blocked by NonceRegistry.
        nonces = env_mod.NonceRegistry()
        first = env_mod.verify_mandate_envelope(
            env, now_ms=5_000, expected_venue="testnet", expected_exchange="binance",
            verifier=_AcceptVerifier(), nonce_registry=nonces, consume_nonce=True,
        )
        self.assertTrue(first.valid)
        replay = env_mod.verify_mandate_envelope(
            env, now_ms=5_001, expected_venue="testnet", expected_exchange="binance",
            verifier=_AcceptVerifier(), nonce_registry=nonces, consume_nonce=True,
        )
        self.assertFalse(replay.valid)
        self.assertIn("nonce_replayed", replay.reasons)

    def test_graduation_orchestrator_end_to_end(self) -> None:
        orch = _load_stdlib_module(
            "services.intelligence.graduation_orchestrator",
            "services/intelligence/graduation_orchestrator.py",
        )
        # 1. Testnet ready bundle -> TESTNET_MANDATE_READY + tightening-only proposal.
        testnet_bundle = orch.StrategyEvidenceBundle(
            strategy_id="btc_hourly_v2",
            strategy_version_hash="sha256:99887766",
            target_venue="testnet",
            exchange="binance",
            allowed_instruments=("BTCUSDT", "ETHUSDT"),
            pit_as_of_verified=True,
            leakage_audit_passed=True,
            panel_freshness_days=0.2,
            ledger_version=orch.V2_PERP_LEDGER,
            includes_fees_funding_slippage=True,
            oos_net_sharpe=1.25,
            deflated_sharpe_prob=0.91,
            cpcv_passed=True,
            research_risk_gates_passed=9,
            research_risk_gates_total=9,
            max_backtest_drawdown=0.08,
            human_reviewer_id="operator-chen",
            pre_trade_risk_gate_active=True,
            kill_switch_ready=True,
            drawdown_controller_active=True,
            exchange_filters_configured=True,
        )
        rep_tn = orch.evaluate_graduation_readiness(testnet_bundle)
        self.assertEqual(rep_tn.verdict, orch.VERDICT_TESTNET_MANDATE_READY)
        self.assertFalse(rep_tn.ai_direct_authority)
        self.assertTrue(rep_tn.requires_human_signature)

        ceiling = orch.SystemRiskCeiling(
            allowed_instruments=("BTCUSDT", "ETHUSDT"),
            max_single_order_notional=1000.0,
            max_gross_notional=5000.0,
            max_net_notional=3000.0,
            max_validity_hours=24,
        )
        proposal = orch.build_human_mandate_proposal(
            rep_tn,
            ceiling,
            requested_instruments=("BTCUSDT",),
            requested_single_order_notional=500.0,
            requested_gross_notional=2000.0,
            requested_net_notional=1000.0,
            validity_hours=12,
            issued_at_ms=1_700_000_000_000,
            nonce="nonce-abcdef-12345678",
        )
        self.assertEqual(proposal["status"], "UNSIGNED_AWAITING_HUMAN_SIGNATURE")
        self.assertFalse(proposal["ai_direct_authority"])
        self.assertTrue(proposal["requires_human_signature"])

        # Attempting to exceed ceiling must raise ValueError.
        with self.assertRaises(ValueError):
            orch.build_human_mandate_proposal(
                rep_tn,
                ceiling,
                requested_instruments=("BTCUSDT",),
                requested_single_order_notional=1500.0,
                requested_gross_notional=2000.0,
                requested_net_notional=1000.0,
                validity_hours=12,
                issued_at_ms=1_700_000_000_000,
                nonce="nonce-abcdef-12345678",
            )

        # 2. Mainnet without external verifier -> BLOCKED_EXTERNAL_VERIFIER_REQUIRED.
        mainnet_bundle = orch.StrategyEvidenceBundle(
            strategy_id="btc_hourly_v2",
            strategy_version_hash="sha256:99887766",
            target_venue="mainnet",
            exchange="binance",
            allowed_instruments=("BTCUSDT",),
            pit_as_of_verified=True,
            leakage_audit_passed=True,
            panel_freshness_days=0.2,
            ledger_version=orch.V2_PERP_LEDGER,
            includes_fees_funding_slippage=True,
            oos_net_sharpe=1.25,
            deflated_sharpe_prob=0.91,
            cpcv_passed=True,
            research_risk_gates_passed=9,
            research_risk_gates_total=9,
            max_backtest_drawdown=0.08,
            soak_hours_completed=120.0,
            observed_fills=45,
            realized_slippage_bps=4.2,
            ledger_reconciliation_drift_usd=0.01,
            human_reviewer_id="operator-chen",
            external_verifier_integrated=False,
            external_signature_verified=False,
            pre_trade_risk_gate_active=True,
            kill_switch_ready=True,
            drawdown_controller_active=True,
            exchange_filters_configured=True,
        )
        rep_mn = orch.evaluate_graduation_readiness(mainnet_bundle)
        self.assertEqual(rep_mn.verdict, orch.VERDICT_BLOCKED_EXTERNAL_VERIFIER_REQUIRED)

        # 3. Legacy INVALIDATED_PERP_LEDGER_V1 always REJECTED.
        legacy_bundle = orch.StrategyEvidenceBundle(
            strategy_id="legacy_v1",
            strategy_version_hash="sha256:112233",
            target_venue="testnet",
            exchange="binance",
            allowed_instruments=("BTCUSDT",),
            pit_as_of_verified=True,
            leakage_audit_passed=True,
            panel_freshness_days=0.2,
            ledger_version=orch.INVALIDATED_LEGACY_LEDGER,
            includes_fees_funding_slippage=True,
            oos_net_sharpe=3.0,
            deflated_sharpe_prob=0.99,
            cpcv_passed=True,
            research_risk_gates_passed=9,
            research_risk_gates_total=9,
            max_backtest_drawdown=0.05,
            human_reviewer_id="operator-chen",
            pre_trade_risk_gate_active=True,
            kill_switch_ready=True,
            drawdown_controller_active=True,
            exchange_filters_configured=True,
        )
        rep_leg = orch.evaluate_graduation_readiness(legacy_bundle)
        self.assertEqual(rep_leg.verdict, orch.VERDICT_REJECTED)
        with self.assertRaises(ValueError):
            orch.build_human_mandate_proposal(
                rep_leg,
                ceiling,
                requested_instruments=("BTCUSDT",),
                requested_single_order_notional=500.0,
                requested_gross_notional=2000.0,
                requested_net_notional=1000.0,
                validity_hours=12,
                issued_at_ms=1_700_000_000_000,
                nonce="nonce-abcdef-12345678",
            )

    def test_modified_backend_modules_parse_cleanly(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[2]
        for rel in (
            "services/risk/pre_trade/context.py",
            "services/risk/pre_trade/checks.py",
            "services/risk/research_risk/domain/risk_modules.py",
            "services/risk/research_risk/domain/gate.py",
            "services/monitoring/audit_store.py",
            "services/research_runner/main.py",
            "services/console/api/main.py",
            "services/validation/performance.py",
            "services/validation/deflated_sharpe.py",
            "services/validation/cpcv.py",
            "services/validation/walk_forward.py",
            "services/portfolio/optimizer.py",
            "services/portfolio/risk_model.py",
            "services/portfolio/construction.py",
            "services/alpha/metrics.py",
            "services/alpha/walk_forward.py",
            "services/alpha/similarity.py",
            "services/alpha/population_optimizer.py",
            "services/execution/impact.py",
            "services/execution/quantization.py",
            "services/data/quality.py",
            "services/data/panel_quality.py",
            "services/intelligence/reward.py",
            "services/intelligence/mcts_search.py",
            "services/intelligence/search_strategy.py",
            "services/intelligence/graduation_orchestrator.py",
            "live/pipeline.py",
            "live/governance/mandate.py",
            "live/governance/review_queue.py",
            "live/governance/mandate_envelope.py",
            "live/exchange/retry.py",
            "live/execution/drawdown_controller.py",
            "live/execution/pricing.py",
            "live/execution/tca.py",
            "live/execution/risk_monitor.py",
            "live/execution/emergency.py",
            "live/execution/filters.py",
            "live/execution/slicer.py",
            "data/stocks/validate_ashare.py",
            "data/stocks/smoke_ashare.py",
        ):
            ast.parse((root / rel).read_text(encoding="utf-8"), filename=rel)


if __name__ == "__main__":
    unittest.main()

