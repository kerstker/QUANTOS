"""Family-level multiple testing (727 §7.3).

The doc's complaint is precise: these five are NOT five independent discoveries —

    rank(global_ls)  zscore(global_ls)  winsorized_rank(global_ls)
    mean_7d(global_ls)  mean_14d(global_ls)

they are one economic idea wearing five transforms. Flat BH over them spends the
FDR budget as if five independent bets had been placed, so the family's best
member clears a bar that was never actually five-fold wide.

`multiple_testing.py` already owns the flat lenses (BH, BHY, Bonferroni, Holm,
DSR, effective_trials) and `cluster.py` already owns complete-linkage correlation
clustering. This module adds ONLY the hierarchy the flat lenses cannot express:

  * assign_families()   — economic family declaration first, correlation cluster
                          as the fallback (never the other way round: a declared
                          economic family must not be split by a noisy sample
                          correlation).
  * simes_pvalue()      — one valid family-level p-value from its members.
  * hierarchical_fdr()  — Benjamini-Bogomolov: select families at level q, then
                          test within each SELECTED family at a level scaled by
                          the selected fraction, which is what keeps the overall
                          FDR at q instead of inflating it per family.
  * marginal_ir()       — the candidate's residual IR after projecting out the
                          incumbent basket (redundancy shows up as a collapse).

Pure stdlib, deterministic, no I/O. Diagnostic/accounting only: nothing here
promotes an alpha or moves a lifecycle state.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from quantos.analytics.cluster import cluster_alphas
from .multiple_testing import benjamini_hochberg

# Below this fraction of unexplained variance (1 - R^2) the incumbent basket
# explains the candidate to floating-point precision, so the appraisal ratio is
# numerically undefined rather than small. See marginal_ir().
_MIN_UNEXPLAINED_VARIANCE_RATIO = 1e-12


@dataclass(frozen=True)
class FamilyAssignment:
    """Which family each candidate belongs to, and how that was decided."""

    family_of: Mapping[str, str]
    basis: Mapping[str, str]  # alpha_id -> DECLARED | CORRELATION | SINGLETON

    def members(self, family_id: str) -> tuple[str, ...]:
        return tuple(sorted(a for a, f in self.family_of.items() if f == family_id))

    @property
    def family_ids(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.family_of.values())))


@dataclass(frozen=True)
class HierarchicalFdrResult:
    """Outcome of the two-stage family FDR.

    `survivors` is the honest answer to "which alphas are discoveries"; the other
    fields exist so a reviewer can see WHY, which §8.3/§22.11 require.
    """

    survivors: frozenset[str]
    selected_families: frozenset[str]
    family_pvalues: Mapping[str, float]
    within_family_level: Mapping[str, float]
    n_families: int
    reasons: Mapping[str, str]


def assign_families(
    *,
    return_series: Mapping[str, Sequence[float]],
    declared_families: Optional[Mapping[str, str]] = None,
    rho_cut: float = 0.70,
    min_overlap: int = 60,
) -> FamilyAssignment:
    """Group candidates into economic families.

    Precedence is deliberate. A DECLARED family (same economic mechanism, same
    input dataset) wins over sample correlation, because a family whose members
    happen to be weakly correlated in this sample is still one bet on one
    mechanism — splitting it would silently restore the inflation this module
    exists to remove. Only candidates with no declaration fall back to
    correlation clustering, and a candidate that clusters alone is a SINGLETON.
    """
    declared = dict(declared_families or {})
    family_of: dict[str, str] = {}
    basis: dict[str, str] = {}

    undeclared: dict[str, tuple[float, ...]] = {}
    for alpha_id in sorted(return_series):
        label = declared.get(alpha_id)
        if isinstance(label, str) and label.strip():
            family_of[alpha_id] = f"declared:{label.strip()}"
            basis[alpha_id] = "DECLARED"
        else:
            undeclared[alpha_id] = tuple(float(v) for v in return_series[alpha_id])

    if undeclared:
        for cluster in cluster_alphas(undeclared, rho_cut=rho_cut, min_overlap=min_overlap):
            singleton = len(cluster.member_ids) == 1
            for alpha_id in cluster.member_ids:
                family_of[alpha_id] = f"corr:{cluster.cluster_id}"
                basis[alpha_id] = "SINGLETON" if singleton else "CORRELATION"

    return FamilyAssignment(family_of=family_of, basis=basis)


def simes_pvalue(pvalues: Sequence[float]) -> Optional[float]:
    """Simes combination: min_k ( m/k * p_(k) ), clipped to 1.

    Valid under positive dependence, which is exactly the regime inside a family
    (five transforms of one signal are strongly positively dependent). Using the
    plain minimum instead would be anti-conservative; using Bonferroni would
    throw away the family's joint evidence. None when no p-value is present.
    """
    present = sorted(float(p) for p in pvalues if p is not None)
    m = len(present)
    if m == 0:
        return None
    return min(1.0, min((m / k) * p for k, p in enumerate(present, start=1)))


def hierarchical_fdr(
    *,
    pvalues: Mapping[str, Optional[float]],
    families: FamilyAssignment,
    q: float = 0.10,
) -> HierarchicalFdrResult:
    """Two-stage family FDR (Benjamini-Bogomolov).

    Stage 1 selects families using each family's Simes p-value at level q.
    Stage 2 tests inside each SELECTED family at level q * (R / n_families),
    where R is the number of selected families. That scaling is the whole point:
    without it, running BH at full q inside every selected family would let the
    overall FDR drift above q once several families survive.

    A family whose members have no usable p-value is NOT selected and is
    reported as NO_PVALUE — never silently treated as a pass (§9.2).
    """
    if not 0.0 < q < 1.0:
        raise ValueError("q must be in (0, 1)")

    family_ids = families.family_ids
    reasons: dict[str, str] = {}
    family_p: dict[str, float] = {}

    for family_id in family_ids:
        members = families.members(family_id)
        member_p = [pvalues.get(a) for a in members]
        combined = simes_pvalue([p for p in member_p if p is not None])
        if combined is None:
            for alpha_id in members:
                reasons[alpha_id] = "NO_PVALUE"
            continue
        family_p[family_id] = combined

    n_families = len(family_ids)
    testable = sorted(family_p)
    if not testable:
        return HierarchicalFdrResult(
            survivors=frozenset(),
            selected_families=frozenset(),
            family_pvalues=family_p,
            within_family_level={},
            n_families=n_families,
            reasons=reasons,
        )

    # Stage 1 — BH across families. m is the number of families with a p-value;
    # families without one are already accounted for as NO_PVALUE above.
    selected_idx = benjamini_hochberg([family_p[f] for f in testable], q=q)
    selected = {testable[i] for i in selected_idx}
    for family_id in testable:
        if family_id not in selected:
            for alpha_id in families.members(family_id):
                reasons[alpha_id] = "FAMILY_NOT_SELECTED"

    # Stage 2 — within each selected family, at the scaled level.
    scaled = q * (len(selected) / n_families) if n_families else 0.0
    survivors: set[str] = set()
    within_level: dict[str, float] = {}
    for family_id in sorted(selected):
        within_level[family_id] = scaled
        members = families.members(family_id)
        member_ids = [a for a in members if pvalues.get(a) is not None]
        if not member_ids:
            for alpha_id in members:
                reasons.setdefault(alpha_id, "NO_PVALUE")
            continue
        rejected = benjamini_hochberg(
            [pvalues[a] for a in member_ids], q=scaled
        )
        kept = {member_ids[i] for i in rejected}
        survivors |= kept
        for alpha_id in members:
            if alpha_id in kept:
                reasons[alpha_id] = "SURVIVED_WITHIN_FAMILY"
            else:
                reasons.setdefault(alpha_id, "REJECTED_WITHIN_FAMILY")

    return HierarchicalFdrResult(
        survivors=frozenset(survivors),
        selected_families=frozenset(selected),
        family_pvalues=family_p,
        within_family_level=within_level,
        n_families=n_families,
        reasons=reasons,
    )


def family_adjusted_trial_count(
    *,
    families: FamilyAssignment,
    mean_abs_corr_within: Mapping[str, float],
) -> float:
    """Trial count that charges a family for its members WITHOUT double counting.

    Flat trial counting says five transforms of one signal are five trials;
    counting the family as one says they are one. Neither is right. Each family
    contributes its own effective-trial count (from `effective_trials`, i.e.
    shrunk by the members' mean |corr|), and the families sum. A family of five
    perfectly correlated members contributes ~1; five uncorrelated singletons
    contribute 5.

    Requires a within-family mean |corr| per family. A family with no supplied
    correlation is charged its FULL member count — the conservative direction
    (it can only make deflation stricter, never gift a discount).
    """
    from .effective_trials import effective_trials

    total = 0.0
    for family_id in families.family_ids:
        n = len(families.members(family_id))
        rho = mean_abs_corr_within.get(family_id)
        if rho is None:
            total += float(n)
        else:
            total += effective_trials(n, float(rho))
    return total


def marginal_ir(
    *,
    candidate: Sequence[float],
    incumbents: Mapping[str, Sequence[float]],
) -> Optional[float]:
    """Annualization-free marginal IR of the candidate against the incumbent
    basket (727 §7.3 "marginal IR", §10.3 comparison metric).

    Definition: fit `candidate = a + B . incumbents + e` by OLS, then return
    `a / sd(e)` — the appraisal ratio. The INTERCEPT is the marginal alpha; the
    residual mean is identically zero once an intercept is fitted, so a
    mean-based ratio would collapse to ~0 for every input and measure nothing.

    Redundancy is visible here in a way a standalone Sharpe hides: a candidate
    that is a repackaging of an incumbent keeps a healthy standalone Sharpe and
    loses its marginal IR, because the rescale is fully absorbed by B.

    Returns None (never a fabricated number) when the sample is too short, the
    residual has no variance, or the normal equations are singular — the caller
    must route that to review rather than read it as "no marginal edge".
    """
    y = [float(v) for v in candidate]
    n = len(y)
    if n < 3:
        return None

    columns: list[list[float]] = [[1.0] * n]
    for name in sorted(incumbents):
        series = [float(v) for v in incumbents[name]]
        if len(series) < n:
            return None
        columns.append(series[-n:])

    k = len(columns)
    if n <= k:
        return None

    # Normal equations (X'X) b = X'y with Gaussian elimination + partial pivoting.
    xtx = [[sum(ci[t] * cj[t] for t in range(n)) for cj in columns] for ci in columns]
    xty = [sum(ci[t] * y[t] for t in range(n)) for ci in columns]

    aug = [row[:] + [xty[i]] for i, row in enumerate(xtx)]
    for col in range(k):
        pivot_row = max(range(col, k), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot_row][col]) < 1e-12:
            return None  # singular: collinear incumbents, no honest projection
        aug[col], aug[pivot_row] = aug[pivot_row], aug[col]
        pivot = aug[col][col]
        for r in range(k):
            if r == col:
                continue
            factor = aug[r][col] / pivot
            if factor == 0.0:
                continue
            for c in range(col, k + 1):
                aug[r][c] -= factor * aug[col][c]
    beta = [aug[i][k] / aug[i][i] for i in range(k)]

    residual = [
        y[t] - sum(beta[i] * columns[i][t] for i in range(k)) for t in range(n)
    ]
    sd = statistics.pstdev(residual)
    if sd <= 0 or not math.isfinite(sd):
        return None

    # A near-perfect fit is total redundancy, and both the intercept and the
    # residual sd collapse to floating-point noise. Their RATIO does not: it
    # lands on an arbitrary O(1) value that reads like a real appraisal ratio.
    # An absolute `sd > 0` guard does not catch this, so compare the unexplained
    # variance against the candidate's own scale (1 - R^2) and refuse to report.
    candidate_sd = statistics.pstdev(y)
    if candidate_sd <= 0 or not math.isfinite(candidate_sd):
        return None
    if (sd / candidate_sd) ** 2 < _MIN_UNEXPLAINED_VARIANCE_RATIO:
        return None

    intercept = beta[0]  # columns[0] is the constant term
    if not math.isfinite(intercept):
        return None
    return intercept / sd
