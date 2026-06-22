"""Paper Trading 鈥?Complete Domain Models (Phase 1H)

All models serializable. Research simulation only. No live trading.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
from uuid import uuid4
import time, hashlib, json


class PaperLifecycleState(str, Enum):
    VALIDATED = "VALIDATED"; PAPER_RUNNING = "PAPER_RUNNING"
    PAPER_PASSED = "PAPER_PASSED"; PAPER_FAILED = "PAPER_FAILED"
    PAPER_PAUSED = "PAPER_PAUSED"; PRODUCTION_CANDIDATE = "PRODUCTION_CANDIDATE"


class OrderSide(str, Enum): BUY = "BUY"; SELL = "SELL"
class OrderStatus(str, Enum): PENDING = "PENDING"; FILLED = "FILLED"; PARTIAL = "PARTIAL"; CANCELLED = "CANCELLED"
class FillMethod(str, Enum): NEXT_OPEN = "NEXT_OPEN"; NEXT_CLOSE = "NEXT_CLOSE"; VWAP = "VWAP"


@dataclass
class PaperAccount:
    account_id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""; initial_capital: float = 10_000_000.0
    cash: float = 10_000_000.0; positions: Dict[str, int] = field(default_factory=dict)
    unrealized_pnl: float = 0.0; realized_pnl: float = 0.0
    total_commission: float = 0.0; total_slippage: float = 0.0
    total_turnover: float = 0.0; created_at: float = field(default_factory=time.time)

    @property
    def equity(self) -> float:
        """Equity = cash + unrealized_pnl (position gains). Realized_pnl already in cash."""
        return self.cash + self.unrealized_pnl

    @property
    def gross_exposure(self) -> float:
        return abs(sum(v for v in self.positions.values()) * (self.equity / max(self.initial_capital, 1)))

    @property
    def leverage(self) -> float:
        return self.gross_exposure / max(self.equity, 1)


@dataclass
class PaperPosition:
    instrument_id: str; quantity: int = 0
    avg_cost: float = 0.0; market_price: float = 0.0
    market_value: float = 0.0; unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0; weight: float = 0.0


@dataclass
class PaperOrder:
    order_id: str = field(default_factory=lambda: str(uuid4()))
    run_id: str = ""; instrument_id: str = ""
    side: OrderSide = OrderSide.BUY; quantity: int = 0
    target_price: Optional[float] = None; status: OrderStatus = OrderStatus.PENDING
    filled_quantity: int = 0; avg_fill_price: float = 0.0
    commission: float = 0.0; slippage: float = 0.0
    created_at: int = 0


@dataclass
class PaperFill:
    fill_id: str = field(default_factory=lambda: str(uuid4()))
    order_id: str = ""; run_id: str = ""; instrument_id: str = ""
    side: OrderSide = OrderSide.BUY; quantity: int = 0
    price: float = 0.0; fill_method: FillMethod = FillMethod.NEXT_CLOSE
    timestamp: int = 0; commission: float = 0.0; slippage: float = 0.0


@dataclass
class PaperSignal:
    signal_id: str = field(default_factory=lambda: str(uuid4()))
    run_id: str = ""; alpha_id: str = ""; instrument_id: str = ""
    timestamp: int = 0; value: float = 0.0; confidence: float = 0.5


@dataclass
class PaperRun:
    run_id: str = field(default_factory=lambda: str(uuid4()))
    alpha_id: str = ""; account_id: str = ""; status: str = "CREATED"
    start_date: str = ""; end_date: str = ""
    fill_method: FillMethod = FillMethod.NEXT_CLOSE
    signals: List[PaperSignal] = field(default_factory=list)
    orders: List[PaperOrder] = field(default_factory=list)
    fills: List[PaperFill] = field(default_factory=list)
    equity_curve: List[Dict] = field(default_factory=list)
    risk_snapshots: List[Dict] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)
    attribution: Optional["PaperAttribution"] = None
    reproducibility_hash: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass
class PaperAttribution:
    run_id: str = ""; pnl_by_alpha: Dict[str, float] = field(default_factory=dict)
    pnl_by_sector: Dict[str, float] = field(default_factory=dict)
    pnl_by_regime: Dict[str, float] = field(default_factory=dict)
    total_commission: float = 0.0; total_slippage: float = 0.0
    total_impact: float = 0.0; turnover: float = 0.0


@dataclass
class PaperRiskSnapshot:
    run_id: str = ""; timestamp: int = 0
    gross_exposure: float = 0.0; net_exposure: float = 0.0
    leverage: float = 0.0; drawdown: float = 0.0
    volatility: float = 0.0; var_95: Optional[float] = None
    max_position_pct: float = 0.0; turnover_rate: float = 0.0


@dataclass
class PaperLifecycleDecision:
    decision_id: str = field(default_factory=lambda: str(uuid4()))
    alpha_id: str = ""; run_id: str = ""
    current_state: PaperLifecycleState = PaperLifecycleState.VALIDATED
    target_state: PaperLifecycleState = PaperLifecycleState.PAPER_RUNNING
    reason: str = ""; metrics: Dict[str, float] = field(default_factory=dict)
    recommendation: str = ""  # PROMOTE, EXTEND, REJECT
