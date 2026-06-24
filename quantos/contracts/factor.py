"""Caller-supplied multi-factor risk model (frozen contract).

Total covariance is Sigma = B*Sf*B^T + diag(Delta), where B = loadings
(alpha x factor), Sf = factor_cov, Delta = specific_var. The model is supplied
(not estimated) — there is no real data at L2 to estimate it from. Validated
fail-closed: dimensions, symmetry, non-negative specific variance, all finite.
"""
from __future__ import annotations

import math
from typing import Sequence

from pydantic import BaseModel, ConfigDict, model_validator


class FactorRiskModel(BaseModel):
    model_config = ConfigDict(frozen=True)
    factor_ids: tuple[str, ...]
    loadings: dict[str, tuple[float, ...]]
    factor_cov: tuple[tuple[float, ...], ...]
    specific_var: dict[str, float]

    @model_validator(mode="after")
    def _check(self) -> "FactorRiskModel":
        k = len(self.factor_ids)
        if k == 0 or len(set(self.factor_ids)) != k:
            raise ValueError("factor_ids must be non-empty and unique")
        if len(self.factor_cov) != k or any(len(row) != k for row in self.factor_cov):
            raise ValueError("factor_cov must be k x k")
        for i in range(k):
            for j in range(k):
                v = self.factor_cov[i][j]
                if not math.isfinite(v):
                    raise ValueError("factor_cov has non-finite entry")
                if abs(v - self.factor_cov[j][i]) > 1e-12:
                    raise ValueError("factor_cov must be symmetric")
        for aid, row in self.loadings.items():
            if len(row) != k:
                raise ValueError(f"loading row for {aid} must have length {k}")
            if any(not math.isfinite(x) for x in row):
                raise ValueError(f"loading row for {aid} has non-finite entry")
        for aid, sv in self.specific_var.items():
            if not math.isfinite(sv) or sv < 0:
                raise ValueError(f"specific_var[{aid}] must be finite and >= 0")
        return self

    def aligned(self, alpha_ids: Sequence[str]) -> tuple[list[list[float]], list[float]]:
        B, spec = [], []
        for aid in alpha_ids:
            if aid not in self.loadings or aid not in self.specific_var:
                raise ValueError(f"factor model has no entry for alpha {aid}")
            B.append(list(self.loadings[aid]))
            spec.append(float(self.specific_var[aid]))
        return B, spec
