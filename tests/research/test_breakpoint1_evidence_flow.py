"""断点1 (方案A) end-to-end, deterministic (no network): the search/driver layer computes a REAL leakage_ok
(via detect_leakage on the factor's own compute) and ResearchAgentLoop.run forwards it to the evaluator's
gate. Proves the promotion gate now receives a real leakage verdict instead of the fail-closed None — and
that supplying evidence NEVER grants approval (auto_approved=0). The evaluator's consumption of leakage_ok /
its regime self-compute are covered by the evaluator's own suites; here we prove the SOURCE→gate wiring."""
from quantos.contracts.data import Bar
from quantos.contracts.alpha import AlphaDefinition
from services.alpha.catalog import AlphaCatalog
from services.alpha.config import AlphaFactoryConfig
from services.alpha.evaluator import AlphaEvaluator
from services.alpha.panel_backtest import PanelPeriod
from services.intelligence.agent_loop import ResearchAgentLoop
from services.research.leakage_evidence import signal_leakage_ok


def _bars(closes, inst):
    return [Bar(instrument_id=inst, event_time=i, knowledge_time=i,
                open=float(c), high=float(c) + 1.0, low=max(0.1, float(c) - 1.0), close=float(c), volume=1.0)
            for i, c in enumerate(closes)]


def _periods(n=10):
    out = []
    for k in range(n):
        sig = (1.0, -1.0) if k % 2 == 0 else (-1.0, 1.0)
        fwd = (0.02, -0.01) if k % 3 else (-0.015, 0.018)
        out.append(PanelPeriod(instruments=("BTC", "ETH"), signals=sig, forward_returns=fwd))
    return out


def _defn():
    return AlphaDefinition(alpha_id="t_mom", name="mom", factor_ids=("close",),
                           params={"operator": "momentum", "window": "3"})


class _SpyEvaluator(AlphaEvaluator):
    """Records the gate-evidence kwargs the loop forwards, then delegates to the REAL evaluate."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.calls = []

    def evaluate(self, alpha_id, periods, **kw):
        self.calls.append(dict(kw))
        return super().evaluate(alpha_id, periods, **kw)


def _run(evidence):
    catalog = AlphaCatalog()
    spy = _SpyEvaluator(catalog, AlphaFactoryConfig(compute_regime_evidence=True))
    loop = ResearchAgentLoop(catalog=catalog, config=AlphaFactoryConfig(compute_regime_evidence=True),
                             evaluator=spy)
    loop.run([(_defn(), _periods())], timestamp=1000, evidence=evidence)
    return spy, catalog


def test_loop_forwards_the_computed_leakage_ok_to_the_gate():
    bars = _bars([10, 11, 12, 11, 13, 14, 13, 15, 16, 15], "BTC")
    leak_ok = signal_leakage_ok("momentum", 3, bars, target_times=[5, 6, 7])
    assert leak_ok is True                                       # computed by the detector, not hard-coded
    spy, _ = _run({"t_mom": {"leakage_ok": leak_ok}})
    assert spy.calls and spy.calls[0]["leakage_ok"] is True      # the SOURCE'd verdict reached the gate
    assert spy.calls[0]["pbo"] is not None or "pbo" in spy.calls[0]   # PBO self-computed in the loop is forwarded too


def test_loop_without_evidence_forwards_none_fail_closed():
    spy, _ = _run(None)                                          # no evidence supplied
    assert spy.calls and spy.calls[0]["leakage_ok"] is None      # fail-closed: absent source => NOT ASSESSED


def test_evidence_never_grants_approval():
    bars = _bars([10, 11, 12, 11, 13, 14, 13, 15, 16, 15], "BTC")
    _, catalog = _run({"t_mom": {"leakage_ok": signal_leakage_ok("momentum", 3, bars, [5, 6, 7])}})
    assert catalog.state("t_mom").value != "APPROVED"            # containment: never auto-approved
