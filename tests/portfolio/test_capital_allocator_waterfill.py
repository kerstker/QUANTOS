"""Rank #10b — bounded iterative waterfill in the capital allocator.

The cap redistribution is now an explicit bounded loop (clip each round -> a cap can never be
breached; converges in <= n+1 rounds or fails closed). These pin the tightly-binding convergence,
the infeasible fail-closed, cap-respect, and determinism at the allocator's public surface."""
import pytest

from services.portfolio.capital_allocator import allocate_capital, CapitalAllocatorConfig


def _series(mean: float, amp: float = 0.001, n: int = 40) -> tuple[float, ...]:
    return tuple(mean + (amp if i % 2 == 0 else -amp) for i in range(n))


def _weights(plan):
    return {a.book_id: a.target_weight for a in plan.allocations}


def test_tightly_binding_caps_converge_to_budget_within_caps():
    # two books capped hard, one uncapped backup must absorb the rest — a multi-book cascade
    plan = allocate_capital(
        {"strong": _series(0.006), "middle": _series(0.004), "backup": _series(0.002)},
        total_aum=100.0, capacity_by_book={"strong": 20.0, "middle": 25.0},
        config=CapitalAllocatorConfig(max_weight=1.0, mean_shrink_z=0.0))
    w = _weights(plan)
    assert plan.status == "ALLOCATED"
    assert w["strong"] <= 0.20 + 1e-10 and w["middle"] <= 0.25 + 1e-10
    assert w["backup"] >= 0.55 - 1e-10                       # absorbs the residual budget
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-8)


def test_caps_summing_below_budget_fail_closed():
    plan = allocate_capital(
        {"a": _series(0.003), "b": _series(0.002)},
        total_aum=100.0, capacity_by_book={"a": 30.0, "b": 30.0},   # 0.30+0.30 < 1.0
        config=CapitalAllocatorConfig(max_weight=1.0, mean_shrink_z=0.0))
    assert plan.status == "BLOCKED"
    assert plan.reasons == ("infeasible_capacity_caps",)


def test_no_cap_breach_after_waterfill():
    plan = allocate_capital(
        {"a": _series(0.005), "b": _series(0.004), "c": _series(0.003), "d": _series(0.002)},
        total_aum=100.0, capacity_by_book={"a": 15.0, "b": 15.0, "c": 15.0},
        config=CapitalAllocatorConfig(max_weight=1.0, mean_shrink_z=0.0))
    w = _weights(plan)
    assert plan.status == "ALLOCATED"
    assert w["a"] <= 0.15 + 1e-10 and w["b"] <= 0.15 + 1e-10 and w["c"] <= 0.15 + 1e-10
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-8)


def test_waterfill_is_deterministic():
    kw = dict(total_aum=100.0, capacity_by_book={"strong": 20.0, "middle": 25.0},
              config=CapitalAllocatorConfig(max_weight=1.0, mean_shrink_z=0.0))
    books = {"strong": _series(0.006), "middle": _series(0.004), "backup": _series(0.002)}
    assert allocate_capital(books, **kw) == allocate_capital(books, **kw)


# ---- Rank #10c — CVaR penalty pass-through to the optimizer (tail-aware sizing) ----

# steady and crashy are built to share the SAME mean (0.01) and SAME variance (0.0016) but a very
# different left tail — so the mean-variance term treats them alike and any tilt is purely CVaR's doing
# (isolating 10c's effect; a naive crashy would be zeroed by variance+mean before CVaR could act).
def _steady():
    return tuple(0.05 if i % 2 == 0 else -0.03 for i in range(40))         # symmetric ±0.04 about 0.01


def _crashy():
    return tuple(-0.1643 if i in (10, 30) else 0.01917 for i in range(40))  # 2 deep crashes, fat left tail


def test_cvar_penalty_shifts_allocation_off_the_fat_tail_book():
    base = allocate_capital({"steady": _steady(), "crashy": _crashy()},
                            config=CapitalAllocatorConfig(max_weight=1.0, mean_shrink_z=0.0))
    tail = allocate_capital({"steady": _steady(), "crashy": _crashy()},
                            config=CapitalAllocatorConfig(max_weight=1.0, mean_shrink_z=0.0,
                                                          cvar_penalty=8.0, cvar_alpha=0.9))
    wb = _weights(base)
    wt = _weights(tail)
    assert base.status == "ALLOCATED" and tail.status == "ALLOCATED"
    assert wt["crashy"] < wb["crashy"] - 1e-3          # tail penalty sizes down the crash-prone book
    assert sum(wt.values()) == pytest.approx(1.0, abs=1e-8)


def test_zero_cvar_penalty_is_the_default_and_unchanged():
    a = allocate_capital({"steady": _steady(), "crashy": _crashy()},
                         config=CapitalAllocatorConfig(max_weight=1.0, mean_shrink_z=0.0))
    b = allocate_capital({"steady": _steady(), "crashy": _crashy()},
                         config=CapitalAllocatorConfig(max_weight=1.0, mean_shrink_z=0.0, cvar_penalty=0.0))
    assert a == b


def test_invalid_cvar_config_blocks():
    for bad in (CapitalAllocatorConfig(cvar_penalty=-1.0), CapitalAllocatorConfig(cvar_alpha=1.5)):
        plan = allocate_capital({"a": _steady(), "b": _crashy()}, config=bad)
        assert plan.status == "BLOCKED"
        assert plan.reasons[0].startswith("invalid_cvar")
