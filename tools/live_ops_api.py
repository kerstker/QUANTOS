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
4. CORS 仅本机 3100(与 console_api 同款);全部端点无认证——**仅限本机使用**,这是单机
   研究工作站的口径,不是多用户部署(那是 Phase 14)。

实时口径诚实标注:dashboard 每次被轮询都现场查交易所(REST),"实时"= 轮询间隔粒度
(前端默认 8s),不是 websocket 推送——周度调仓策略不需要毫秒级,这是刻意的简单。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from live.execution.position_ledger import LiveLedger
from live.execution.tca import tca_summary
from live.governance.mandate_store import MandateLoadError, load_mandate
from tools.live_track_advance import _VENUES, _load_credentials, _venue_paths

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


def _binance_position_rows(raw_account: dict) -> list:
    out = []
    for p in raw_account.get("positions", []):
        try:
            qty = float(p.get("positionAmt", 0) or 0)
        except (TypeError, ValueError):
            continue
        if qty == 0.0 or not p.get("symbol", "").endswith("USDT"):
            continue
        entry = float(p.get("entryPrice", 0) or 0)
        upnl = float(p.get("unrealizedProfit", 0) or 0)
        mark = entry + (upnl / qty if qty else 0.0)
        out.append({"instrument_id": p["symbol"][:-4], "qty": qty, "entry_price": entry,
                    "mark_price": mark, "upnl": upnl, "notional": qty * mark})
    return out


def _bybit_position_rows(venue) -> list:
    status, data = venue._get("/v5/position/list",
                              {"category": "linear", "settleCoin": "USDT"}, signed=True)
    result = (data or {}).get("result") or {}
    out = []
    for p in result.get("list", []) or []:
        try:
            size = float(p.get("size", 0) or 0)
        except (TypeError, ValueError):
            continue
        if size == 0.0 or not p.get("symbol", "").endswith("USDT"):
            continue
        qty = size if p.get("side") == "Buy" else -size
        entry = float(p.get("avgPrice", 0) or 0)
        mark = float(p.get("markPrice", 0) or 0)
        upnl = float(p.get("unrealisedPnl", 0) or 0)
        out.append({"instrument_id": p["symbol"][:-4], "qty": qty, "entry_price": entry,
                    "mark_price": mark, "upnl": upnl, "notional": qty * (mark or entry)})
    return out


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
            positions = _binance_position_rows(acct)
        else:
            positions = _bybit_position_rows(v)
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


@app.get("/api/v1/liveops/dashboard")
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


@app.post("/api/v1/liveops/run")
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


@app.post("/api/v1/liveops/demo-funds")
async def demo_funds(req: DemoFundsRequest) -> dict:
    if req.amount <= 0 or req.amount > 1_000_000:
        raise HTTPException(400, "amount out of range")
    cmd = [sys.executable, "-m", "tools.bybit_demo_funds", "--amount", str(req.amount)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                          cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return {"returncode": proc.returncode, "output": (proc.stdout + proc.stderr)[-500:]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8440)
