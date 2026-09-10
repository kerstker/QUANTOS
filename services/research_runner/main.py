"""Research-runner FastAPI adapter — the only mutating research-console surface.

POST /run {kind, params} validates against the research-only whitelist (RUN_KINDS) and enqueues an async
job that writes results to the run_registry. It connects to NO broker, places NO orders, stores NO
credentials, flips NO flags, and drives NO lifecycle promotion — the whitelist contains only research
entrypoints (machine-enforced by tests/governance/test_research_runner_boundary.py). The read-only
console_api serves progress/results from the same registry; this service never serves a GET that runs
anything. Wall-clock is taken only here, at the service/HTTP adapter boundary.

**本机共享密钥认证(2026-07-06,安全审查 punch-list):** 此前两个 mutating 端点(/run、/cancel)
零认证——同机任意进程都能触发研究作业或撤销别人的任务。研究作业本身不碰订单/credential(见上),
但仍是真实计算资源(CPU/时间,尤其 factory_orthogonal 这类演化搜索),/cancel 还能撤销他人合法
任务。镜像 `tools/live_ops_api.py` 的本机共享密钥模式(`key/research_runner_token.txt`,首次启动
生成,gitignored,只检查、不外泄)——同样不是多用户身份认证(那是 Phase 14 范畴),威胁模型是
"同机其它进程"。`/health` 是静态元数据,不需要认证。"""
from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import logging
import os
import secrets
import time
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from quantos.runtime_paths import runtime_state_root
from services.monitoring.audit_store import DurableAuditLog
from services.execution.backtest_authority import BacktestIntegrityBlocked
from services.research.run_registry.contract import (
    RUNNER_RESTART_INTERRUPTED,
    InvocationConflictError,
)
from services.research.run_registry.registry import RunRegistry
from services.research.run_registry.store import ResearchRunStore
from .run_kinds import RUN_KINDS
from .run_kinds import LEGACY_QUARANTINED_RUN_KINDS, enforce_run_kind_authority
from .runner import JobQueue
from .operator_tag import decode_operator_tag

_STATE_ROOT = runtime_state_root()
_RUNS_DB = str(_STATE_ROOT / "data/cache/research_runs.db")
_AUDIT_DB = str(_STATE_ROOT / "data/cache/governance_audit.db")
_TOKEN_FILE = str(_STATE_ROOT / "key/research_runner_token.txt")
_LOG = logging.getLogger(__name__)


def _load_or_create_token(path: str = _TOKEN_FILE) -> str:
    require_existing = os.getenv(
        "QUANTOS_REQUIRE_EXISTING_RUNNER_TOKEN", "false"
    ).strip().lower() == "true"
    if os.path.exists(path):
        if require_existing and os.path.islink(path):
            raise RuntimeError("research runner token must not be a symlink")
        if require_existing and not os.path.isfile(path):
            raise RuntimeError("research runner token must be a regular file")
        if require_existing and os.name == "posix" and os.stat(path).st_mode & 0o077:
            raise RuntimeError("research runner token permissions must be 0600")
        with open(path, encoding="utf-8") as handle:
            tok = handle.read().strip()
        if tok:
            return tok
    if require_existing:
        raise RuntimeError("pre-provisioned research runner token is required")
    tok = secrets.token_urlsafe(32)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(tok)
    return tok


_RUNNER_TOKEN = _load_or_create_token()

_extra_cors_origins = tuple(
    origin.strip()
    for origin in os.getenv("QUANTOS_RUNNER_CORS_ORIGINS", "").split(",")
    if origin.strip()
)


def require_runner_token(x_runner_token: Optional[str] = Header(default=None)) -> None:
    if x_runner_token is None or not secrets.compare_digest(x_runner_token, _RUNNER_TOKEN):
        raise HTTPException(401, "missing or invalid X-Runner-Token")


def require_operator(
    x_operator: list[str] | None = Header(default=None),
    x_operator_encoding: list[str] | None = Header(default=None),
) -> str:
    values = x_operator or []
    encodings = x_operator_encoding or []
    if len(values) > 1 or len(encodings) > 1:
        raise HTTPException(400, 'duplicate operator headers are not allowed')
    try:
        return decode_operator_tag(values[0] if values else None,
                                   encodings[0] if encodings else None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _reconcile_interrupted_runs_on_startup() -> tuple[tuple[str, str], ...]:
    """Close jobs whose only executor disappeared with the prior process.

    Registry mutation is atomic.  Governance audit is deliberately best-effort, matching the trigger
    path: an audit-store outage must not leave the UI polling a known-abandoned invocation forever.
    The run's durable log and exact error code are committed in the authoritative registry transaction.
    """
    now = int(time.time())
    reconciled = _registry.reconcile_interrupted_runner_runs(finished_ts=now)
    for run_id, previous_status in reconciled:
        try:
            _audit.append(
                event_id=f"runner-restart-interrupted.{run_id}",
                actor="research-runner",
                action="research.run.restart_interrupted",
                timestamp=now,
                target=run_id,
                metadata={
                    "previous_status": previous_status,
                    "status": "error",
                    "error": RUNNER_RESTART_INTERRUPTED,
                },
            )
        except Exception:
            _LOG.exception("failed to audit interrupted research run %s", run_id)
    if reconciled:
        _LOG.warning(
            "reconciled %d interrupted research runner invocation(s)",
            len(reconciled),
        )
    return reconciled


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    _reconcile_interrupted_runs_on_startup()
    task=None
    if os.getenv('QUANTOS_FACTORY_CAMPAIGNS_ENABLED','false')=='true':
        async def campaigns():
            from .factory_campaign import campaign_tick
            while True:
                try:
                    status=await asyncio.to_thread(campaign_tick,_STATE_ROOT,_queue,_registry,now=time.time_ns()//1000000)
                    from tools.cache_io import atomic_write_json
                    from tools.btc_factory_research import seal
                    atomic_write_json(str(_STATE_ROOT/'data/cache/ai_factory/campaign-worker.json'),seal({**status,'observed_at_ms':time.time_ns()//1000000,'authoritative':False}))
                except Exception:_LOG.exception('factory campaign scheduling failed')
                await asyncio.sleep(30)
        task=asyncio.create_task(campaigns())
    try:yield
    finally:
        if task:
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass


app = FastAPI(title="QUANTOS Research Runner", version="0.1.0", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3100", "http://localhost:3100", *_extra_cors_origins],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# A run temporarily redirects process-global stdout into its durable registry
# log.  Keep the resident queue serial so two threads can never cross-wire that
# process-global stream; independent systemd workers still run concurrently.
_registry = RunRegistry(ResearchRunStore(_RUNS_DB))
_queue = JobQueue(_registry, max_workers=1)
_audit = DurableAuditLog(_AUDIT_DB)


class RunRequest(BaseModel):
    kind: str
    params: dict = {}


@app.get("/health")
async def health() -> dict:
    return {
        "mode": "RESEARCH_RUNNER",
        "live_disallowed": True,
        "kinds": sorted(set(RUN_KINDS) - LEGACY_QUARANTINED_RUN_KINDS),
        "quarantined_kinds": {
            kind: "INVALIDATED_PERP_LEDGER_V1"
            for kind in sorted(LEGACY_QUARANTINED_RUN_KINDS)
        },
        "kind_policies": {
            kind: {
                "purpose": spec.provenance_policy.purpose.value,
                "evidence_authority": spec.provenance_policy.evidence_authority,
            }
            for kind, spec in sorted(RUN_KINDS.items())
        },
    }


@app.post("/run", dependencies=[Depends(require_runner_token)])
async def run(
    req: RunRequest,
    operator: str = Depends(require_operator),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    # Rank #18: the only mutating surface must record WHO triggered each run. X-Operator is a
    # SELF-ASSERTED, UNAUTHENTICATED accountability tag (NOT RBAC/authn — that needs a Phase-14
    # identity layer). Required so no run is anonymous; persisted on the run row + the governance
    # audit chain.
    try:
        enforce_run_kind_authority(req.kind)
    except BacktestIntegrityBlocked as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    now = int(time.time())
    try:
        run_id = _queue.submit(
            req.kind,
            req.params,
            now=now,
            operator=operator,
            invocation_id=idempotency_key,
        )
    except KeyError:
        raise HTTPException(status_code=400, detail=f"unknown research kind: {req.kind!r}")
    except InvocationConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    policy = RUN_KINDS[req.kind].provenance_policy
    # Best-effort trigger record on the durable governance chain. The authoritative accountability is
    # the operator persisted on the run row. Each canonical invocation has one durable identity (a retry
    # resolves the same row), so an audit database hiccup must not alias or fail the persisted run.
    try:
        _audit.append(
            event_id=f"run.{run_id}",
            actor=operator,
            action="research.run.triggered",
            timestamp=now,
            target=run_id,
            metadata={"kind": req.kind, "purpose": policy.purpose.value},
        )
    except Exception:
        pass
    return {
        "run_id": run_id,
        "status": "queued",
        "kind": req.kind,
        "operator": operator,
        "purpose": policy.purpose.value,
        "selection_eligible": False,
    }


class CancelRequest(BaseModel):
    run_id: str


@app.post("/cancel", dependencies=[Depends(require_runner_token)])
async def cancel(req: CancelRequest, operator: str = Depends(require_operator)) -> dict:
    # 2026-07-05 安全审查 MEDIUM 修复:/cancel 此前不要求 X-Operator,与 /run 的问责要求
    # 不对称——任何人可以枚举 /api/v1/console/research-runs(公开只读)拿到 run_id 后匿名
    # 撤销别人的任务,连事后追责都做不到。同 /run 一样,这是自报的问责标签,不是身份认证
    # (那是 Phase 14 的范畴)。
    # soft cancel: mark the run cancelled and drop its result (a running tool is not force-killed)
    cancelled = _queue.cancel(req.run_id)
    try:
        _audit.append(event_id=f"cancel.{req.run_id}.{int(time.time())}", actor=operator,
                     action="research.run.cancelled", timestamp=int(time.time()),
                     target=req.run_id, metadata={"cancelled": str(cancelled)})
    except Exception:
        pass
    return {"run_id": req.run_id, "cancelled": cancelled, "operator": operator}


class FactoryRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    workspace: Literal['current'] = 'current'
    action: str
    attempt: int
    mode: str = 'baseline'
    budget: int = 3
    objective: str = 'BTC 小时级因果研究'
    search_plan: Optional[dict] = None
    research_protocol: Optional[str] = None


class ProfitabilityInputRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    workspace: Literal['current'] = 'current'
    attempt: int = Field(ge=1,le=1000)
    kind: str
    content: str = Field(max_length=4000000)


@app.post('/profitability-input',dependencies=[Depends(require_runner_token)])
async def profitability_input(req: ProfitabilityInputRequest,operator: str=Depends(require_operator),
                              idempotency_key: Optional[str]=Header(default=None,alias='Idempotency-Key')):
    from services.research.ai_factory_store import FactoryStore
    from services.governance.json_integrity import load_json_without_duplicate_keys
    try:
        if len(req.content.encode('utf-8'))>4000000:raise ValueError('research input exceeds 4 MB')
        data=load_json_without_duplicate_keys(req.content.encode('utf-8'))
        with FactoryStore(_STATE_ROOT/'data/cache/ai_factory/factory.db') as store:
            receipt=store.add_profitability_input(req.attempt,req.kind,data,operator)
        from services.research.profitability_inputs import KINDS
        run_id=_queue.submit('profitability_input',{'attempt':req.attempt,'input_kind':KINDS.index(req.kind),'revision':receipt['revision']},now=int(time.time()),operator=operator,
                             invocation_id=idempotency_key or f"profitability-input:{req.attempt}:{req.kind}:{receipt['input_hash']}")
        return {'input':receipt,'run_id':run_id,'economic_eligible':False}
    except (ValueError,TypeError,KeyError,ArithmeticError) as exc:
        raise HTTPException(400,f'invalid research input: {type(exc).__name__}: {exc}') from exc


@app.post('/factory', dependencies=[Depends(require_runner_token)])
async def factory_control(req: FactoryRequest, operator: str = Depends(require_operator),
                          idempotency_key: Optional[str] = Header(default=None,alias='Idempotency-Key')):
    from services.research.ai_factory import validate_config
    from services.research.ai_factory_store import FactoryStore
    if not 1<=req.attempt<=1000:raise HTTPException(400,'invalid attempt')
    if req.action not in ('create','pause','resume','cancel','observe','stop_observe','refresh_observe','assess','verify','compare','profitability','forward_cost','information','portfolio','sleeves','sleeve_compare','explain','portfolio_observe','portfolio_refresh','portfolio_stop','sleeve_observe','sleeve_refresh','sleeve_stop'):raise HTTPException(400,'unknown factory action')
    try:
        config=validate_config({'attempt':req.attempt,'mode':req.mode,'budget':req.budget,'objective':req.objective,**({'search_plan':req.search_plan} if req.search_plan is not None else {}),**({'research_protocol':req.research_protocol} if req.research_protocol is not None else {})})
        with FactoryStore(_STATE_ROOT/'data/cache/ai_factory/factory.db') as store:
            if req.action in ('observe','stop_observe','refresh_observe','assess','verify','compare','profitability','forward_cost','information','portfolio','sleeves','sleeve_compare','explain','portfolio_observe','portfolio_refresh','portfolio_stop','sleeve_observe','sleeve_refresh','sleeve_stop'):
                row=store.get(req.attempt)
                if row['state']!='COMPLETED':raise ValueError('completed research required')
                kind={'explain':'ai_factory_verdict','portfolio_observe':'ai_factory_portfolio_forward','portfolio_refresh':'ai_factory_portfolio_forward','portfolio_stop':'ai_factory_portfolio_forward','sleeve_observe':'ai_factory_sleeves_forward','sleeve_refresh':'ai_factory_sleeves_forward','sleeve_stop':'ai_factory_sleeves_forward','portfolio':'ai_factory_portfolio','sleeves':'ai_factory_sleeves','sleeve_compare':'ai_factory_sleeves_comparison','information':'profitability_information','forward_cost':'profitability_forward','profitability':'profitability_study','assess':'ai_factory_assessment','verify':'ai_factory_verification','compare':'ai_factory_comparison'}.get(req.action,'ai_factory_forward')
                params={'attempt':req.attempt} if req.action in ('assess','verify','compare','profitability','forward_cost','information','portfolio','sleeves','sleeve_compare','explain') else {'attempt':req.attempt,'action':{'observe':1,'stop_observe':2,'refresh_observe':0,'portfolio_observe':1,'portfolio_refresh':0,'portfolio_stop':2,'sleeve_observe':1,'sleeve_refresh':0,'sleeve_stop':2}[req.action]}
                run_id=_queue.submit(kind,params,
                    now=int(time.time()),operator=operator,invocation_id=idempotency_key)
                with store.c:store.event(req.attempt,'OPERATOR_ACTION',{'operator':operator,'action':req.action,'run_id':run_id})
                return {'session':row,'run_id':run_id}
            if req.action=='create':store.create(config)
            elif req.action=='resume' and store.get(req.attempt)['state']=='COMPLETED':pass
            else:store.control(req.attempt,req.action)
            if req.action in ('create','resume'):
                row=store.get(req.attempt)
                if row['state']=='CANCELLED':return {'session':row,'run_id':None}
                run_id=_queue.submit('ai_factory_pipeline',{'attempt':req.attempt},now=int(time.time()),operator=operator,
                    invocation_id=idempotency_key or f"factory-{req.attempt}-{req.action}-{row['updated']}")
            else:run_id=None
            with store.c:store.event(req.attempt,'OPERATOR_ACTION',{'operator':operator,'action':req.action,'run_id':run_id})
            return {'session':store.get(req.attempt),'run_id':run_id}
    except (ValueError,InvocationConflictError) as exc:raise HTTPException(409,str(exc)) from exc


class FactoryCampaignRequest(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    workspace: Literal['current'] = 'current'
    action:str
    campaign_id:int
    objective:str='BTC 小时级成本后机制研究'
    mode:str='ai'
    budget:int=3
    max_cycles:int=3
    interval_hours:int=24
    source_ids:list[str]=['buildalpha','aqr','rd_agent']
    trigger:str='schedule'
    champion_attempt:Optional[int]=None
    research_protocol:Optional[str]=None


@app.post('/factory-campaign',dependencies=[Depends(require_runner_token)])
async def factory_campaign_control(req:FactoryCampaignRequest,operator:str=Depends(require_operator)):
    from services.research.ai_factory_store import FactoryStore
    if not 1<=req.campaign_id<=1000:raise HTTPException(400,'invalid campaign id')
    try:
        with FactoryStore(_STATE_ROOT/'data/cache/ai_factory/factory.db') as store:
            if req.action=='create':
                config=req.model_dump(exclude={'action','workspace'})
                if config['research_protocol'] is None:config.pop('research_protocol')
                store.create_campaign(config,operator=operator,now=time.time_ns()//1000000)
            elif req.action in ('pause','resume','stop'):store.control_campaign(req.campaign_id,req.action,operator=operator)
            else:raise ValueError('unknown campaign action')
            return {**store.campaign_snapshot(),'scheduler_enabled':os.getenv('QUANTOS_FACTORY_CAMPAIGNS_ENABLED','false')=='true'}
    except (ValueError,TypeError) as exc:raise HTTPException(409,str(exc)) from exc
