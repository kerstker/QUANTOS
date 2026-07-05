"""交易台 ops API(2026-07-05,LiveDesk 前端的后端)。端口 8440。

**为什么在 tools/ 而不是 services/:** console_api(services/)受"services/ 不得 import live/"
单向边界管辖,只能读快照;交易台要实时查两所行情/仓位,网络 + live/ import 都是 tools/ 的
正当领地(驱动器/取数器同在此)。

**操作面的治理边界(tests/governance/test_live_ops_boundary.py 机器钉死):**
1. 本模块**绝不直接下单**——不调用任何下单/撤单/批量提交入口(字面量扫描钉死);POST /run
   只以固定 argv 子进程运行 `python -m tools.live_track_advance`,与人工敲 CLI 完全同一条
   mandate → gate → 确认 → 记账链,没有任何旁路;
2. venue 参数白名单(_VENUES 键),submit 是布尔——不存在自由字符串到达命令行;
3. 响应永不包含密钥值(凭证只在进程内构造适配器用);
4. CORS 仅本机 3100(与 console_api 同款)。

**本机共享密钥认证(2026-07-05,机构审计 P1):** 此前全部端点零认证——同一台机器上任意
进程(包括恶意进程)都能打这个端口触发真实提交。全部端点现在要求请求头 `X-Ops-Token` 匹配
`key/live_ops_token.txt`(首次启动自动生成,gitignored,与其它密钥文件同一目录/同一"只
检查、不外泄"处理方式)。这不是多用户认证(那是 Phase 14 的范畴)——威胁模型是"同机其它
进程",共享密钥文件本身要求与读取 mandate/ledger 文件同等的本机访问权限,把"零门槛"提高
到"至少要能读 key/ 目录"。

实时口径诚实标注:dashboard 每次被轮询都现场查交易所(REST),"实时"= 轮询间隔粒度
(前端默认 8s),不是 websocket 推送——周度调仓策略不需要毫秒级,这是刻意的简单。
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from live.execution.position_ledger import LiveLedger
from live.execution.tca import tca_summary
from live.governance.mandate_store import MandateLoadError, load_mandate
from live.governance.review_queue import ReviewQueue
from tools.live_snapshot import binance_position_rows, bybit_position_rows
from tools.live_track_advance import _VENUES, _load_credentials, _venue_paths

_REVIEW_QUEUE_PATH = os.path.join("data", "cache", "live_review_queue.db")

_TOKEN_FILE = os.path.join("key", "live_ops_token.txt")


def _load_or_create_token(path: str = _TOKEN_FILE) -> str:
    """本机共享密钥:首次启动生成、之后复用同一个值(不重启就变,否则前端每次都要重配)。
    只检查、绝不打印/记录这个值本身——与全库密钥处理的既有边界一致。"""
    if os.path.exists(path):
        tok = open(path, encoding="utf-8").read().strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(32)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(tok)
    return tok


_OPS_TOKEN = _load_or_create_token()


def require_ops_token(x_ops_token: Optional[str] = Header(default=None)) -> None:
    if x_ops_token is None or not secrets.compare_digest(x_ops_token, _OPS_TOKEN):
        raise HTTPException(401, "missing or invalid X-Ops-Token")


app = FastAPI(title="QUANTOS Live Ops", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3100", "http://127.0.0.1:3100"],
                   allow_methods=["GET", "POST"], allow_headers=["*"])

_RUN_LOCK = threading.Lock()
_RUN_STATE = {"running": False, "venue": None, "submit": None, "started_at": None,
              "finished_at": None, "returncode": None, "tail": ""}


def _venue_adapter(venue: str):
    cfg = _VENUES[venue]
    api_key, api_secret = _load_credentials(cfg, venue)
    return cfg["adapter"](api_key=api_key, api_secret=api_secret, base_url=cfg["base_url"])


def _equity_curve(suffix: str, max_points: int = 500) -> list:
    """风险哨兵落的权益点(append-only jsonl),取尾部 N 点给前端画净值曲线。"""
    path = os.path.join("data", "cache", f"live_equity{suffix}.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out[-max_points:]


def _venue_dashboard(venue: str) -> dict:
    cfg = _VENUES[venue]
    paths = _venue_paths(cfg["suffix"])
    out: dict = {"venue": venue, "configured": os.path.exists(cfg["key_file"])
                 or bool(os.environ.get(cfg["env"][0]))}
    if not out["configured"]:
        return out
    try:
        v = _venue_adapter(venue)
        acct = v.account()
        aum, equity = cfg["balances"](acct)
        if venue == "binance":
            positions = binance_position_rows(acct)
        else:
            _status, raw_pos = v._get("/v5/position/list",
                                      {"category": "linear", "settleCoin": "USDT"}, signed=True)
            positions = bybit_position_rows(raw_pos)
        gross = sum(abs(p["notional"]) for p in positions)
        net = sum(p["notional"] for p in positions)
        out.update({
            "ok": True, "equity": equity, "available": aum,
            "positions": sorted(positions, key=lambda p: -abs(p["notional"])),
            "gross_notional": gross, "net_notional": net,
            "upnl_total": sum(p["upnl"] for p in positions),
        })
    except Exception as exc:
        out.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return out

    try:
        ledger = LiveLedger(paths["ledger"])
        out["recent_orders"] = ledger.orders()[-25:][::-1]
        out["recent_fills"] = ledger.fills()[-25:][::-1]
        out["tca"] = tca_summary(ledger.tca_records())
    except Exception as exc:
        out["ledger_error"] = f"{type(exc).__name__}: {exc}"

    out["equity_curve"] = _equity_curve(cfg["suffix"])   # 风险哨兵心跳积累的真实权益历史

    try:
        m = load_mandate(cfg["mandate_file"])
        out["mandate"] = {"mandate_id": m.mandate_id, "exchange": m.exchange,
                          "max_gross_notional": m.max_gross_notional,
                          "max_single_order_notional": m.max_single_order_notional,
                          "max_net_notional": m.max_net_notional, "expires_at": m.expires_at}
    except MandateLoadError:
        out["mandate"] = None                       # 未签署 -> 永远人工审查档,前端如实展示

    if os.path.exists(paths["status"]):
        try:
            s = json.load(open(paths["status"], encoding="utf-8"))
            out["last_run"] = {k: s.get(k) for k in
                               ("ran_at", "mode", "tier", "hard_stop", "n_submitted",
                                "n_pending_review", "n_submission_errors", "deploy_aum",
                                "parity", "alerts")}
        except (json.JSONDecodeError, OSError):
            pass
    return out


@app.get("/api/v1/liveops/dashboard", dependencies=[Depends(require_ops_token)])
async def dashboard() -> dict:
    return {
        "source": "LIVE_OPS_REALTIME",
        "generated_at": int(time.time() * 1000),
        "note": "实时=REST 轮询粒度(周度策略不需要毫秒级);操作只经驱动器 CLI 同一条治理链",
        "venues": {name: _venue_dashboard(name) for name in _VENUES},
        "run_state": dict(_RUN_STATE),
    }


class RunRequest(BaseModel):
    venue: str
    submit: bool = False
    refresh: bool = False


def _build_run_cmd(venue: str, submit: bool, refresh: bool) -> list:
    """固定 argv(绝无 shell/自由字符串):驱动器 CLI 与人工执行完全同一入口。"""
    cmd = [sys.executable, "-m", "tools.live_track_advance", "--venue", venue]
    if submit:
        cmd.append("--submit")
    if not refresh:
        cmd.append("--no-refresh")
    return cmd


def _run_driver(venue: str, submit: bool, refresh: bool) -> None:
    cmd = _build_run_cmd(venue, submit, refresh)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                              cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _RUN_STATE.update({"returncode": proc.returncode,
                           "tail": (proc.stdout + proc.stderr)[-2000:]})
    except Exception as exc:
        _RUN_STATE.update({"returncode": -1, "tail": f"{type(exc).__name__}: {exc}"})
    finally:
        _RUN_STATE.update({"running": False, "finished_at": int(time.time() * 1000)})
        _RUN_LOCK.release()


@app.post("/api/v1/liveops/run", dependencies=[Depends(require_ops_token)])
async def run(req: RunRequest) -> dict:
    if req.venue not in _VENUES:
        raise HTTPException(400, f"unknown venue {req.venue!r} (allowed: {sorted(_VENUES)})")
    if not _RUN_LOCK.acquire(blocking=False):
        raise HTTPException(409, "a driver run is already in progress (single-flight)")
    _RUN_STATE.update({"running": True, "venue": req.venue, "submit": req.submit,
                       "started_at": int(time.time() * 1000), "finished_at": None,
                       "returncode": None, "tail": ""})
    threading.Thread(target=_run_driver, args=(req.venue, req.submit, req.refresh),
                     daemon=True).start()
    return {"started": True, "venue": req.venue, "submit": req.submit}


class DemoFundsRequest(BaseModel):
    amount: float = 5000.0


@app.post("/api/v1/liveops/demo-funds", dependencies=[Depends(require_ops_token)])
async def demo_funds(req: DemoFundsRequest) -> dict:
    if req.amount <= 0 or req.amount > 1_000_000:
        raise HTTPException(400, "amount out of range")
    cmd = [sys.executable, "-m", "tools.bybit_demo_funds", "--amount", str(req.amount)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                          cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return {"returncode": proc.returncode, "output": (proc.stdout + proc.stderr)[-500:]}


@app.get("/api/v1/liveops/pending-review", dependencies=[Depends(require_ops_token)])
async def pending_review() -> dict:
    """NEEDS_HUMAN_REVIEW 队列(2026-07-05):只读展示。**批准/拒绝在这里只记录人工决定,
    不会自动重新提交订单**——见 live/governance/review_queue.py 模块说明的诚实边界。"""
    if not os.path.exists(_REVIEW_QUEUE_PATH):
        return {"pending": []}
    return {"pending": ReviewQueue(_REVIEW_QUEUE_PATH).pending()}


class ReviewDecisionRequest(BaseModel):
    status: str                    # "APPROVED" | "REJECTED"
    reviewer: str
    note: str = ""


@app.post("/api/v1/liveops/pending-review/{row_id}/decide",
          dependencies=[Depends(require_ops_token)])
async def decide_pending_review(row_id: int, req: ReviewDecisionRequest) -> dict:
    if req.status not in ("APPROVED", "REJECTED"):
        raise HTTPException(400, "status must be APPROVED or REJECTED")
    if not req.reviewer.strip():
        raise HTTPException(400, "reviewer name is required (accountability, not auth)")
    if not os.path.exists(_REVIEW_QUEUE_PATH):
        raise HTTPException(404, "no review queue yet")
    ReviewQueue(_REVIEW_QUEUE_PATH).mark_reviewed(
        row_id, status=req.status, reviewer=req.reviewer, at=int(time.time() * 1000),
        note=req.note)
    return {"ok": True, "row_id": row_id, "status": req.status,
           "note": "decision recorded only — it does NOT resubmit the order"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8440)
