"""Synergistic factor-set selection (tests-first).

The best SET is not the greedy top-N of single-factor IR: a second copy of the best
factor adds nothing. Forward-select by INCREMENTAL IR vs the running selected set
(orthogonalize residual), so redundant clones are rejected and only factors carrying
NEW information are kept. This is the de-redundancy layer the GP demo motivated (its top
trees were all low-volatility clones).
"""
import pytest

from services.research.factor_set import incremental_ir, select_synergistic_set

N = 80
A = [0.01 * ((i % 5) - 2) + 0.002 for i in range(N)]          # oscillating, +mean
B = [a + 1e-6 for a in A]                                     # ~identical to A (redundant)
C = [0.01 * ((i % 7) - 3) + 0.002 for i in range(N)]          # different period -> low corr with A
RET = {"A": A, "B": B, "C": C}


def test_incremental_ir_drops_for_redundant_factor():
    ir_b = incremental_ir(B, {"A": tuple(A)}, min_overlap=20)   # near-duplicate -> ~0 novel variance
    ir_c = incremental_ir(C, {"A": tuple(A)}, min_overlap=20)   # orthogonal-ish -> retains effective IR
    assert ir_c is not None and ir_b is not None
    assert ir_c > ir_b                             # the clone's effective incremental IR collapses
    assert abs(ir_b) < 0.1                          # ~0: nothing new vs A


def test_select_rejects_redundant_keeps_synergistic():
    out = select_synergistic_set(RET, max_k=3, min_incremental_ir=0.5, min_overlap=20)
    picked = [s["id"] for s in out["selected"]]
    assert "C" in picked                            # orthogonal info is kept
    # A and B are near-clones: exactly ONE is kept, the OTHER is dropped as redundant
    assert len([x for x in picked if x in ("A", "B")]) == 1
    twin_dropped = ({"A", "B"} - set(picked)).pop()
    assert twin_dropped in out["dropped_redundant"]
    assert len(picked) == 2 and len(picked) == len(set(picked))


def test_select_is_deterministic():
    a = select_synergistic_set(RET, max_k=3, min_incremental_ir=0.5, min_overlap=20)
    b = select_synergistic_set(RET, max_k=3, min_incremental_ir=0.5, min_overlap=20)
    assert a == b


def test_select_fail_closed_on_thin_or_empty():
    assert select_synergistic_set({}, max_k=3, min_overlap=20)["selected"] == []
    # a too-short series cannot be assessed -> not selected, never fabricated
    out = select_synergistic_set({"x": [0.01, 0.02]}, max_k=3, min_overlap=20)
    assert all(s["id"] != "x" or s["incremental_ir"] is not None for s in out["selected"])


def test_max_k_caps_selection():
    out = select_synergistic_set(RET, max_k=1, min_incremental_ir=0.0, min_overlap=20)
    assert len(out["selected"]) == 1
