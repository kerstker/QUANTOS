"""Kill-switch 人工复位(2026-07-05,机构审计 P1:"强制走三方")。

`services/risk/pre_trade/kill_switch.py` 同时提供弱路径 `reset()`(单个 `ApprovalRecord`)
和强路径 `reset_with_grant()`(三方不同真人 + 哈希链 `ApprovalRequest`)。审计发现:两条路径
并存,操作工具没有理由让人在真钱场景下选到弱的那条。**本工具只走强路径**——结构上不提供
弱路径的入口。

三个人必须是不同的真人 actor_id(open_request/review/approve 各一人,`consume`/执行复位可以
是第 4 个人或其中一人,不强制)。用法:

    python -m tools.live_kill_switch_reset --venue binance --incident-id trip-1 \\
        --requester alice --reviewer bob --approver carol --executor dave

诚实边界:`services/governance/approval.py` 是纯状态机、无持久化存储——三步在同一次调用里
连续执行,"三个不同真人"这个不变量此刻靠命令行参数各自不同来体现,不是三个人分别在不同
时间各自登录系统操作(那需要一个持久化审批队列,是更大的后续工作,见审计报告)。
"""
from __future__ import annotations

import argparse
import time

from live.governance.kill_switch_store import load_kill_switch_state, save_kill_switch_state
from quantos.contracts.alpha import Actor, ActorKind
from services.governance.approval import approve, consume, open_request, review
from services.risk.pre_trade.kill_switch import KillSwitch
from tools.live_track_advance import _VENUES, _venue_paths


def _human(name: str) -> Actor:
    return Actor(actor_id=name, kind=ActorKind.HUMAN)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--venue", required=True, choices=sorted(_VENUES))
    p.add_argument("--incident-id", required=True)
    p.add_argument("--requester", required=True)
    p.add_argument("--reviewer", required=True)
    p.add_argument("--approver", required=True)
    p.add_argument("--executor", default=None, help="执行复位动作的人(默认同 --approver)")
    args = p.parse_args()

    names = {args.requester, args.reviewer, args.approver}
    if len(names) < 3:
        raise SystemExit("requester/reviewer/approver must be three DISTINCT people "
                        "(three-party requirement — see module docstring)")

    paths = _venue_paths(_VENUES[args.venue]["suffix"])
    state = load_kill_switch_state(paths["kill_switch"])
    if not state.tripped:
        print(f"kill-switch for {args.venue} is not currently tripped — nothing to reset")
        return
    if state.incident_id != args.incident_id:
        raise SystemExit(f"--incident-id {args.incident_id!r} does not match the currently "
                         f"tripped incident {state.incident_id!r}")

    now = int(time.time() * 1000)
    req = open_request(f"reset-{args.incident_id}", args.incident_id, _human(args.requester), at=now)
    req = review(req, _human(args.reviewer), at=now)
    req = approve(req, _human(args.approver), at=now)
    req = consume(req, args.incident_id, _human(args.executor or args.approver), at=now)

    ks = KillSwitch(state=state)
    new_state = ks.reset_with_grant(_human(args.executor or args.approver), req, at=now)
    save_kill_switch_state(paths["kill_switch"], new_state)
    print(f"kill-switch for {args.venue} (incident {args.incident_id}) reset via three-party "
         f"grant (requester={args.requester}, reviewer={args.reviewer}, approver={args.approver})")


if __name__ == "__main__":
    main()
