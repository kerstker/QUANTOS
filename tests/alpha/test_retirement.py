"""Phase 6 step 11 — retirement: decay/regime/redundancy/capacity triggers, routed
through the (audited) lifecycle manager; survivors stay queryable."""
from quantos.contracts.alpha import (
    Actor, ActorKind, AlphaDefinition, DecayReport,
)
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.alpha.config import AlphaFactoryConfig
from services.alpha.lifecycle import AlphaLifecycleManager
from services.alpha.retirement import AlphaRetirementSystem, assess_retirement

AGENT = Actor(actor_id="agent", kind=ActorKind.AGENT)
CFG = AlphaFactoryConfig(min_capacity_notional=1_000_000.0)


def test_decay_triggers_retirement_decision():
    d = DecayReport(alpha_id="a1", decayed=True, reasons=("IC_RETENTION_BELOW_MIN",))
    assert assess_retirement("a1", config=CFG, decay=d).retire is True


def test_regime_break_is_fast_path():
    dec = assess_retirement("a1", config=CFG, regime_profitable_frac=0.2)
    assert dec.retire and dec.reasons[0] == "REGIME_BREAK"


def test_redundancy_and_capacity_triggers():
    assert assess_retirement("a1", config=CFG, max_corr_to_approved=0.9).retire is True
    assert assess_retirement("a1", config=CFG, capacity_notional=10_000.0).retire is True


def test_healthy_alpha_is_not_retired():
    d = DecayReport(alpha_id="a1", decayed=False)
    dec = assess_retirement("a1", config=CFG, decay=d, regime_profitable_frac=0.9,
                            max_corr_to_approved=0.2, capacity_notional=5_000_000.0)
    assert dec.retire is False and dec.reasons == ()


def test_retirement_routes_through_audited_manager():
    cat = AlphaCatalog()
    cat.register(AlphaDefinition(alpha_id="a1", name="A", factor_ids=("f1",)))
    cat._set_state("a1", AlphaState.APPROVED)              # simulate an approved alpha
    mgr = AlphaLifecycleManager(cat, CFG)
    sys = AlphaRetirementSystem(mgr, CFG)
    decision, tr = sys.retire_if_needed("a1", AGENT, timestamp=5,
                                        decay=DecayReport(alpha_id="a1", decayed=True))
    assert decision.retire and tr is not None
    assert cat.state("a1") == AlphaState.RETIRED
    assert len(mgr.audit) == 1 and mgr.audit_chain_valid()
    # survivorship-free: the retired alpha is still in the catalog
    assert "a1" in cat.all_ids()


def test_no_retirement_no_transition():
    cat = AlphaCatalog()
    cat.register(AlphaDefinition(alpha_id="a1", name="A", factor_ids=("f1",)))
    cat._set_state("a1", AlphaState.APPROVED)
    mgr = AlphaLifecycleManager(cat, CFG)
    sys = AlphaRetirementSystem(mgr, CFG)
    decision, tr = sys.retire_if_needed("a1", AGENT, timestamp=5,
                                        decay=DecayReport(alpha_id="a1", decayed=False))
    assert decision.retire is False and tr is None
    assert cat.state("a1") == AlphaState.APPROVED
