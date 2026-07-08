"""告警接缝(Stage 0,实盘路线)。设计:docs/superpowers/specs/2026-07-04-forward-track-live-design.md §2.4。

双通道:① 本地台账 data/cache/alerts.jsonl(append-only,永远生效——审计可回放);
② 可选 Telegram push:仅当 env 同时有 QUANTOS_TG_BOT_TOKEN + QUANTOS_TG_CHAT_ID 时启用
(stdlib urllib POST;密钥只读 env,**永不落 repo/日志/台账**;push 失败 fail-soft——本地台账
是权威记录,push 只是通知)。先服务 paper-track 刷新/兑现告警,Stage 1 起复用于实盘监控。
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

_LEDGER = os.path.join("data", "cache", "alerts.jsonl")


def _telegram_push(text: str) -> bool:
    token = os.environ.get("QUANTOS_TG_BOT_TOKEN")
    chat = os.environ.get("QUANTOS_TG_CHAT_ID")
    if not token or not chat:
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False        # fail-soft:本地台账才是权威记录


def emit(alerts, *, context: str, now_ms=None, ledger_path: str = _LEDGER, push=_telegram_push) -> dict:
    """记录一批告警。空列表 = 无事发生,不写不推。返回 {n, pushed}。"""
    alerts = [str(a) for a in alerts if a]
    if not alerts:
        return {"n": 0, "pushed": False}
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
    with open(ledger_path, "a", encoding="utf-8") as fh:
        for a in alerts:
            fh.write(json.dumps({"ts": now_ms, "context": context, "alert": a},
                                ensure_ascii=False) + "\n")
    text = f"[QUANTOS/{context}] " + " | ".join(alerts[:10])
    if len(alerts) > 10:
        text += f" (+{len(alerts) - 10} more)"
    return {"n": len(alerts), "pushed": push(text)}
