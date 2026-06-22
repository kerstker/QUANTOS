# =============================================================================
# QUARANTINED - NON-FUNCTIONAL MODULE  (Phase 0.5)
# -----------------------------------------------------------------------------
# This module does NOT import or run. Two defects (documented in the Phase 0
# audit and docs/QUANTOS_DEVELOPMENT_PROGRAM_PLAN.md):
#   1. Imports `.domain.models` / `.domain.validators`, which do not exist
#      (the pre_trade_risk/domain/ directory is empty) -> ModuleNotFoundError.
#   2. `background.add_task(_run)` references an undefined name; the coroutine
#      is `_exec` (defined below) -> NameError on /evaluate.
# Do NOT wire this into any runtime. It is preserved (not deleted) for the
# real rebuild in Phase 8 (Risk Layer). It is excluded from test collection
# via pyproject.toml [tool.pytest.ini_options] (norecursedirs / --ignore).
# Marker (asserted by tests): QUANTOS_QUARANTINED_NON_FUNCTIONAL
# =============================================================================
"""Pre-Trade Risk Gate - FastAPI (Phase 2B). QUARANTINED / NON-FUNCTIONAL.

Risk gating only. No OMS/EMS/broker. See quarantine banner above.
"""
import os, json, asyncio, logging, time, hashlib
from contextlib import asynccontextmanager
from typing import Dict, List, Optional
from fastapi import FastAPI, Query, Body, BackgroundTasks
from fastapi.responses import JSONResponse
from aiokafka import AIOKafkaProducer
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, Counter, Histogram

from .domain.models import *
from .domain.validators import PreTradeRiskDecisionEngine, StrategySpecValidator, TradingIntentValidator, LineageValidator, LimitValidator, CapitalValidator, InstrumentValidator, PortfolioStateValidator, MarketSnapshotValidator, KillSwitchValidator

logger = logging.getLogger(__name__)
KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

engine = PreTradeRiskDecisionEngine()
runs: Dict[str, PreTradeRiskRun] = {}; decisions: Dict[str, PreTradeRiskDecision] = {}
idem_store: Dict[str, dict] = {}
producer: Optional[AIOKafkaProducer] = None

async def _pub(et: str, rid: str, extra: dict = None):
    if not producer: return
    e = {"event_id": str(hash(f"{rid}:{et}:{time.time()}")), "event_type": et,
         "event_version": "1.0.0", "timestamp": int(time.time()*1e6),
         "source": "pre-trade-risk", "domain": "trading", "criticality": "C0",
         "payload": {"run_id": rid, **(extra or {})}}
    await producer.send_and_wait(et, key=rid, value=e)

def _d(obj):
    if obj is None: return None
    if hasattr(obj, '__dataclass_fields__'):
        r = {}; [r.update({f: _d(getattr(obj, f))}) for f in obj.__dataclass_fields__]
        return r
    if hasattr(obj, 'value'): return obj.value
    if isinstance(obj, (list,tuple)): return [_d(x) for x in obj][:50]
    if isinstance(obj, dict): return {str(k): _d(v) for k, v in list(obj.items())[:50]}
    return obj

def _idem_check(key: str) -> Optional[dict]:
    if not key: return None
    c = idem_store.get(key)
    if c: r = dict(c["result"]); r["_idempotent"] = True; r["_retrieved_from"] = c["entity_id"]; return r
    return None

def _idem_store(key: str, etype: str, eid: str, result: dict):
    if key: idem_store[key] = {"entity_type": etype, "entity_id": eid, "result": result}

@asynccontextmanager
async def lifespan(app: FastAPI):
    global producer
    producer = AIOKafkaProducer(bootstrap_servers=KAFKA_BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v).encode('utf-8'), compression_type='zstd')
    await producer.start(); yield; await producer.stop()

app = FastAPI(title="QUANTOS Pre-Trade Risk Gate", version="2.0.0", lifespan=lifespan)

@app.get("/health")
async def health(): return {"status": "healthy", "service": "S20-pre-trade-risk"}

@app.post("/api/v1/pre-trade-risk/evaluate")
async def evaluate(background: BackgroundTasks, body: Dict = Body(...)):
    key = body.get("idempotency_key",""); c = _idem_check(key)
    if c: return c
    config = PreTradeRiskConfig(**body.get("config",{}))
    run = PreTradeRiskRun(intent_id=body.get("intent_id",""), strategy_spec_id=body.get("strategy_spec_id",""), config=config)
    runs[run.run_id] = run
    await _pub("trading.risk.pretrade.evaluation.started", run.run_id)

    async def _exec():
        d = engine.evaluate(run, body.get("intent",{}), body.get("spec",{}),
                             body.get("lineage"), body.get("kill_switch"),
                             PortfolioStateSnapshot(**body.get("portfolio_state",{})) if body.get("portfolio_state") else None,
                             MarketDataSnapshot(**body.get("market_snapshot",{})) if body.get("market_snapshot") else None)
        decisions[run.intent_id] = d
        et = "trading.risk.pretrade.approved" if d.decision==RiskDecision.APPROVED else \
             "trading.risk.pretrade.approved_with_limits" if d.decision==RiskDecision.APPROVED_WITH_LIMITS else \
             "trading.risk.pretrade.manual_review_required" if d.decision==RiskDecision.NEEDS_MANUAL_REVIEW else \
             "trading.risk.pretrade.rejected"
        await _pub(et, run.run_id, {"decision": d.decision.value, "reasons": d.rejection_reasons})
        r = _d(run); r["decision"] = _d(d); _idem_store(key, "risk_eval", run.run_id, r)

    background.add_task(_run)
    return {"status": "QUEUED", "run_id": run.run_id}

@app.get("/api/v1/pre-trade-risk/runs")
async def list_runs(): return [_d(r) for r in list(runs.values())[-20:]]
@app.get("/api/v1/pre-trade-risk/runs/{run_id}")
async def get_run(run_id: str):
    r = runs.get(run_id); return _d(r) if r else JSONResponse({"error":"Not found"},404)
@app.get("/api/v1/pre-trade-risk/decision/{intent_id}")
async def get_decision(intent_id: str):
    d = decisions.get(intent_id); return _d(d) if d else JSONResponse({"error":"Not found"},404)
@app.get("/api/v1/pre-trade-risk/report/{run_id}")
async def get_report(run_id: str):
    r = runs.get(run_id); return _d(r) if r else JSONResponse({"error":"Not found"},404)

# Individual validators
@app.post("/api/v1/pre-trade-risk/validate/strategy-spec")
async def validate_spec(body: Dict = Body(...)):
    return _d(StrategySpecValidator().validate(body.get("spec"), PreTradeRiskConfig(**body.get("config",{}))))
@app.post("/api/v1/pre-trade-risk/validate/intent")
async def validate_intent(body: Dict = Body(...)):
    return _d(TradingIntentValidator().validate(body.get("intent"), PreTradeRiskConfig(**body.get("config",{}))))
@app.post("/api/v1/pre-trade-risk/validate/lineage")
async def validate_lineage(body: Dict = Body(...)):
    return _d(LineageValidator().validate(body.get("lineage"), PreTradeRiskConfig(**body.get("config",{}))))
@app.post("/api/v1/pre-trade-risk/validate/limits")
async def validate_limits(body: Dict = Body(...)):
    return _d(LimitValidator().validate(body.get("intent",{}), body.get("spec_limits",{}), PreTradeRiskConfig(**body.get("config",{}))))
@app.post("/api/v1/pre-trade-risk/validate/capital")
async def validate_capital(body: Dict = Body(...)):
    return _d(CapitalValidator().validate(body.get("intent",{}), PreTradeRiskConfig(**body.get("config",{}))))
@app.post("/api/v1/pre-trade-risk/validate/instruments")
async def validate_instruments(body: Dict = Body(...)):
    return _d(InstrumentValidator().validate(body.get("intent",{}), PreTradeRiskConfig(**body.get("config",{}))))
@app.post("/api/v1/pre-trade-risk/validate/portfolio-state")
async def validate_portfolio(body: Dict = Body(...)):
    return _d(PortfolioStateValidator().validate(PortfolioStateSnapshot(**body.get("state",{})), body.get("intent",{}), PreTradeRiskConfig(**body.get("config",{}))))
@app.post("/api/v1/pre-trade-risk/validate/market-snapshot")
async def validate_market(body: Dict = Body(...)):
    return _d(MarketSnapshotValidator().validate(MarketDataSnapshot(**body.get("snapshot",{})), PreTradeRiskConfig(**body.get("config",{}))))
@app.post("/api/v1/pre-trade-risk/validate/kill-switch")
async def validate_kill_switch(body: Dict = Body(...)):
    return _d(KillSwitchValidator().validate(body.get("kill_switch"), PreTradeRiskConfig(**body.get("config",{}))))

@app.get("/metrics")
async def metrics():
    return JSONResponse(content=generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)

if __name__ == "__main__":
    import uvicorn; uvicorn.run(app, host="0.0.0.0", port=8300)
