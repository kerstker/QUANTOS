"""Research domain entities (blueprint §6.3).

Factors, alphas, strategies, experiments, backtest request/result, and the
validation result that gates promotion. Experiment/ValidationResult carry a
trial_count so multiple-testing correction (Deflated Sharpe / PBO) can account
for the total number of trials (CT-2 / plan P1-2).
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class AlphaState(str, Enum):
    DRAFT = "DRAFT"
    TESTING = "TESTING"
    VALIDATED = "VALIDATED"
    PAPER_TRADING = "PAPER_TRADING"
    APPROVED = "APPROVED"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


class ValidationVerdict(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    REQUIRES_HUMAN_REVIEW = "REQUIRES_HUMAN_REVIEW"


class Factor(BaseModel):
    model_config = ConfigDict(frozen=True)
    factor_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    definition: str = ""
    category: str = ""


class Alpha(BaseModel):
    model_config = ConfigDict(frozen=True)
    alpha_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    factor_ids: tuple[str, ...] = ()
    state: AlphaState = AlphaState.DRAFT
    reproducibility_hash: str = ""


class Strategy(BaseModel):
    model_config = ConfigDict(frozen=True)
    strategy_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    alpha_ids: tuple[str, ...] = ()
    approved: bool = False


class Experiment(BaseModel):
    model_config = ConfigDict(frozen=True)
    experiment_id: str = Field(min_length=1)
    strategy_id: str = ""
    config_hash: str = ""
    created_at: int = Field(default=0, ge=0)
    trial_count: int = Field(default=1, ge=1)


class BacktestRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    request_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    start_date: str
    end_date: str
    dataset_version: str = ""
    cost_model: str = "default"


class BacktestResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    request_id: str = Field(min_length=1)
    sharpe: float = 0.0
    total_return: float = 0.0
    max_drawdown: float = Field(default=0.0, ge=0)
    turnover: float = Field(default=0.0, ge=0)
    net_of_cost: bool = True
    reproducibility_hash: str = ""


class ValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    subject_id: str = Field(min_length=1)
    verdict: ValidationVerdict
    deflated_sharpe: float | None = None
    pbo: float | None = Field(default=None, ge=0, le=1)
    trial_count: int = Field(default=1, ge=1)
    reasons: tuple[str, ...] = ()

    @property
    def promotable(self) -> bool:
        return self.verdict in (ValidationVerdict.PASS, ValidationVerdict.WARN)


class ResearchReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    report_id: str = Field(min_length=1)
    subject_id: str = ""
    summary: str = ""
    created_at: int = Field(default=0, ge=0)
