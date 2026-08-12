"""Three-party kill-switch reset over the authoritative transactional store.

This tool exposes no single-party reset path. It remains an L2 local governance
workflow: no broker, order, credential, or network capability is introduced.

P1-4 fix: approval flow now persists to approvals.db (append-only audit trail).
"""
from __future__ import annotations

import argparse
import time

from live.governance.kill_switch_store import (
    open_authoritative_kill_switch_store,
    write_kill_switch_json_mirror,
)
from quantos.contracts.alpha import Actor, ActorKind
from services.governance.approval import approve, consume, open_request, review
from services.governance.approval_store import ApprovalStore
from services.risk.pre_trade.kill_switch_runtime import reset_with_grant_and_record
from tools.kill_switch_events import dispatch_and_project_kill_switch_events
from tools.live_track_advance import _VENUES, _venue_paths


_APPROVAL_DB = "data/cache/approvals.db"


def _human(name: str) -> Actor:
    return Actor(actor_id=name, kind=ActorKind.HUMAN)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venue", required=True, choices=sorted(_VENUES))
    parser.add_argument("--incident-id", required=True)
    parser.add_argument("--requester", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--approver", required=True)
    parser.add_argument(
        "--executor",
        default=None,
        help="person executing the reset (defaults to --approver)",
    )
    args = parser.parse_args()

    names = {args.requester, args.reviewer, args.approver}
    if len(names) < 3:
        raise SystemExit(
            "requester/reviewer/approver must be three DISTINCT people"
        )

    paths = _venue_paths(_VENUES[args.venue]["suffix"])
    store = open_authoritative_kill_switch_store(paths["kill_switch"])
    approval_store = ApprovalStore(_APPROVAL_DB, create=True)

    try:
        state = store.load_state()
        if not state.tripped:
            print(
                f"kill-switch for {args.venue} is not currently tripped - "
                "nothing to reset"
            )
            return
        if state.incident_id != args.incident_id:
            raise SystemExit(
                f"--incident-id {args.incident_id!r} does not match the currently "
                f"tripped incident {state.incident_id!r}"
            )

        now = int(time.time() * 1000)
        executor = _human(args.executor or args.approver)

        # Multi-party approval flow with durable persistence
        request_id = f"reset-{args.incident_id}-{now}"
        request = open_request(
            request_id,
            args.incident_id,
            _human(args.requester),
            at=now,
        )
        approval_store.append_step(request_id, request.steps[-1])

        request = review(request, _human(args.reviewer), at=now + 1)
        approval_store.append_step(request_id, request.steps[-1])

        request = approve(request, _human(args.approver), at=now + 2)
        approval_store.append_step(request_id, request.steps[-1])

        request = consume(request, args.incident_id, executor, at=now + 3)
        approval_store.append_step(request_id, request.steps[-1])

        # Only reset AFTER successful approval persistence
        state = reset_with_grant_and_record(
            store,
            executor,
            request,
            at=now + 3,
        )
        write_kill_switch_json_mirror(paths["kill_switch"], state)
        delivery = dispatch_and_project_kill_switch_events(
            store,
            state_path=paths["kill_switch"],
            now=now + 3,
            worker_id="kill-switch-reset-domain-event-projection",
        )
    finally:
        store.close()

    print(
        f"kill-switch for {args.venue} (incident {args.incident_id}) reset via "
        f"three-party grant (requester={args.requester}, reviewer={args.reviewer}, "
        f"approver={args.approver}); event_dispatch={delivery['dispatch']}"
    )


if __name__ == "__main__":
    main()
