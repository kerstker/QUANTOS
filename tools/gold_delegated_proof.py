"""Export original executor evidence over its private bounded handoff only."""

import base64
import os
from pathlib import Path

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import require
from live.execution.demo_gold_delegated_admission import bounded
from tools.gold_bar_fresh_inputs import FreshEpisode, snapshot
from tools.gold_bar_worker_inputs import load
from tools.gold_bar_episode_tail import restore_snapshot
from tools.demo_reported_recovery import _read
from tools.demo_executor_stream import fixed_account
from tools.demo_resident_recovery import require_custody
from tools.demo_issuer import private_pipe, verify_source
from live.execution.demo_gold_delegation_enable import load_enabled
from live.execution.demo_gold_execution_delegation import delegation_base


def freeze_prefix(*, root, runtime, decision, source_commit, uid):
    root = Path(root).absolute()
    if runtime["schema"] == "GOLD_BAR_RETAINED_EPISODE_INPUT_V1":
        engine, _ = load(root, runtime, source_commit=source_commit, uid=uid)
        directory = root / "strategy-bar-observations" / runtime["observation_sha256"]
        entry = runtime["entry_decision_ms"]
        original = snapshot(directory, uid)
        tail = None
    else:
        episode = FreshEpisode(root, runtime, source_commit=source_commit, uid=uid, read_only=True)
        engine = episode.engine
        entry = episode.entry_decision_ms
        directory = episode.directory
        original = episode.files
        digest = decision["bar_continuation_sha256"]
        engine = restore_snapshot(
            root, bundle_sha256=_digest(runtime), engine=engine, entry_decision_ms=entry, snapshot_sha256=digest
        )
        area = root / "strategy-bar-tails" / _digest(runtime)
        frozen = _read(area / "snapshots" / (digest + ".json"), root, 200000)
        binding = _read(area / "binding" / (_digest(frozen["binding"]) + ".json"), root)
        tail = dict(
            binding=binding,
            snapshot=frozen,
            events=[_read(area / "events" / (value + ".json"), root, 200000) for value in frozen["prefix"]],
        )
        episode.recheck()
    require(
        _digest(engine.signal_at(entry)) == decision["bar_signal_sha256"]
        and engine._cursor.last_received <= decision["observed_at_ms"],
        "GOLD_DELEGATED_PREFIX_DECISION",
    )
    require(snapshot(directory, uid) == original, "GOLD_DELEGATED_PREFIX_CHANGED")
    return bounded(
        dict(
            schema="GOLD_DELEGATED_FROZEN_PREFIX_V1",
            files={name: base64.b64encode(raw).decode() for name, raw in original},
            entry_decision_ms=entry,
            tail=tail,
        )
    )


def _pilot_records(root, uid):
    from tools.gold_pilot_install import _read_target
    from services.governance.json_integrity import load_json_without_duplicate_keys as decode

    def read(path, maximum=32768):
        return decode(_read_target(path, uid=uid, maximum=maximum))

    area = root / "gold-pilot"
    intent = area / "enrollment-intent.json"
    stop = area / "stopped.json"
    return dict(
        request=read(root / "gold-pilot-request.json"),
        configuration=read(area / "configuration.json"),
        receipt=read(area / "enrollment.json"),
        intent=read(intent, 16000000) if intent.exists() or intent.is_symlink() else None,
        stop=read(stop) if stop.exists() or stop.is_symlink() else None,
    )


def _historical_state(state, at):
    """Select an already terminal prefix; never manufacture an old OMS status."""
    recovery = [row for row in state["recovery"] if row["recorded_at_ms"] <= at]
    clients = {row["client_order_id"] for row in recovery}
    reservations = [
        row
        for row in state["reservations"]
        if all(order["newClientOrderId"] in clients for order in row["payload"]["orders"])
    ]
    return dict(
        reservations=reservations,
        recovery=recovery,
        oms=[row for row in state["oms"] if row["client_order_id"] in clients],
        wire=[row for row in state["wire"] if row["at_ms"] <= at],
    )


def export(*, root, runtime, result_sha256, enable, registry, source_commit, uid, now_ms, stores):
    """Read an exact original frame under the original preparation/retention leases.

    Internal callers provide already opened original stores. CLI custody fixes the
    paths and identities; no caller-selected path is exposed across its pipe.
    """
    from copy import deepcopy
    from services.governance.json_integrity import load_json_without_duplicate_keys as decode
    from live.execution import demo_market_partition_runtime as partition
    from live.execution import demo_bar_exit_trigger
    from live.execution.demo_pre_submit import _preparation_lease, _state
    from live.execution.demo_gold_delegated_admission import admit, SCHEMA
    from tools.gold_bar_pilot_capacity import original_context
    from tools.gold_pilot_retention_store import Retention, canonical

    root = Path(root).absolute()
    account = delegation_base(enable["delegation"])["account_ref_sha256"]
    original = original_context(root, enable["runtime"], uid=uid, account=account)
    require(original is not None and original["configuration"] is not None, "GOLD_DELEGATED_ENROLLMENT_REQUIRED")
    require(stores["dispatch"].path.parent == root, "GOLD_DELEGATED_STORE_ROOT")
    with _preparation_lease(stores["dispatch"]):
        with Retention(
            root,
            original=original,
            base_runtime=enable["runtime"],
            uid=uid,
            role="executor",
            read_only=True,
            verify_history=False,
        ) as retained:
            result = decode(retained.read("result", result_sha256))
            claim = retained.reference("result", result_sha256)
            require(
                _digest(result) == result_sha256 and claim["bundle_sha256"] == _digest(runtime),
                "GOLD_DELEGATED_RETAINED_RESULT",
            )
            decision = decode(retained.read_claim(claim, "decision"))
            origin = decode(retained.read_claim(claim, "origin"))
            require(
                result["decision"] == decision and decision["origin_sha256"] == _digest(origin),
                "GOLD_DELEGATED_RETAINED_ORIGIN",
            )
            state = _state(**{k: stores[k] for k in ("dispatch", "recovery", "oms")})
            require(_digest(state) == decision["execution_state_sha256"], "GOLD_DELEGATED_EXECUTION_CHANGED")
            context, _ = partition.resolve_context(state=state, origin=origin, as_of_ms=decision["observed_at_ms"])
            pilot = _pilot_records(root, uid)
            trigger = None
            trigger_evidence = None
            if decision["action"] in ("REDUCE_LONG", "REDUCE_SHORT"):
                episode = context["position_episode"]
                binding = dict(
                    account_ref_sha256=account,
                    definition_hash=enable["definition"]["definition_hash"],
                    entry_client_order_id=episode["client_order_id"],
                    reservation_sha256=episode["reservation_sha256"],
                    opened_venue_at_ms=episode["opened_venue_at_ms"],
                )
                trigger = demo_bar_exit_trigger.load(binding, now_ms=decision["observed_at_ms"])
                require(trigger is not None, "GOLD_DELEGATED_TRIGGER_REQUIRED")
                old_claim = retained.reference("origin", trigger["origin_sha256"])
                old_origin = decode(retained.read_claim(old_claim, "origin"))
                old_decision = decode(retained.read_claim(old_claim, "decision"))
                require(
                    old_claim["bundle_sha256"] == _digest(runtime)
                    and old_decision["observed_at_ms"] == trigger["observed_at_ms"]
                    and old_decision["draft"]["exit_trigger_sha256"] == trigger["record_hash"],
                    "GOLD_DELEGATED_TRIGGER_FRAME",
                )
                old_state = _historical_state(state, trigger["observed_at_ms"])
                require(_digest(old_state) == old_decision["execution_state_sha256"], "GOLD_DELEGATED_TRIGGER_STATE")
                old_context, _ = partition.resolve_context(
                    state=old_state, origin=old_origin, as_of_ms=trigger["observed_at_ms"]
                )
                old_pilot = deepcopy(pilot)
                if old_pilot["stop"] is not None and old_pilot["stop"]["observed_at_ms"] > trigger["observed_at_ms"]:
                    old_pilot["stop"] = None
                trigger_evidence = dict(
                    origin=old_origin,
                    context=old_context,
                    pilot=old_pilot,
                    prefix=freeze_prefix(
                        root=root, runtime=runtime, decision=old_decision, source_commit=source_commit, uid=uid
                    ),
                )
            proof = bounded(
                dict(
                    schema=SCHEMA,
                    source_commit=source_commit,
                    enable_sha256=_digest(enable),
                    delegation_sha256=_digest(enable["delegation"]),
                    runtime=deepcopy(runtime),
                    result=result,
                    origin=origin,
                    claim=claim,
                    capacity=deepcopy(retained.binding),
                    pilot=pilot,
                    execution=dict(
                        state=state, context=context, exit_trigger=trigger, trigger_evidence=trigger_evidence
                    ),
                    prefix=freeze_prefix(
                        root=root, runtime=runtime, decision=decision, source_commit=source_commit, uid=uid
                    ),
                    submission_enabled=False,
                    economic_authority=False,
                )
            )
            admit(
                delegation=enable["delegation"],
                enable=enable,
                base_policy=delegation_base(enable["delegation"]),
                registry=registry,
                proof=proof,
                now_ms=now_ms,
            )
            require(
                _state(**{k: stores[k] for k in ("dispatch", "recovery", "oms")}) == state
                and _pilot_records(root, uid) == pilot
                and original_context(root, enable["runtime"], uid=uid, account=account) == original,
                "GOLD_DELEGATED_ORIGINALS_CHANGED",
            )
            require(retained.read("result", result_sha256) == canonical(result), "GOLD_DELEGATED_RETAINED_CHANGED")
    return proof


def now_ms():
    import time

    return time.time_ns() // 1000000


def _read_only_stores(stack, root, uid):
    from live.execution.demo_dispatch_store import DemoDispatchStore
    from live.execution.order_recovery import SqliteOrderRecoveryStore
    from live.execution.position_ledger import LiveLedger
    from tools.demo_resident_recovery import _check_path

    stores = {}
    for name, factory in (("dispatch", DemoDispatchStore), ("recovery", SqliteOrderRecoveryStore), ("oms", LiveLedger)):
        path = root / (name + ".sqlite3")
        _check_path(path, uid, directory=False)
        for suffix in ("-wal", "-shm", "-journal"):
            member = path.with_name(path.name + suffix)
            if member.exists() or member.is_symlink():
                _check_path(member, uid, directory=False)
        stores[name] = factory(str(path), read_only=True)
        stack.callback(stores[name].close)
    return stores


def run(*, source_commit, input_fd=None, output=None):
    import json
    import sys
    import time
    from contextlib import ExitStack
    from live.execution import demo_authority
    from live.execution.demo_batch_risk import _fields
    from live.execution.demo_gold_delegated_admission import admit
    from services.governance.json_integrity import load_json_without_duplicate_keys as decode
    from tools.demo_execution_session import read_frame

    channel = sys.stdout if output is None else output
    phase = "CONFIGURATION"
    previous = os.umask(0o077)
    try:
        verify_source(source_commit)
        root, account = fixed_account()
        registry = demo_authority.load_registry_for_executor()
        uid = registry["executor_uid"]
        require(
            os.geteuid() == os.getegid() == uid and set(os.getgroups()) <= {uid}, "GOLD_DELEGATED_EXECUTOR_IDENTITY"
        )
        require(require_custody() == root, "GOLD_DELEGATED_EXECUTOR_ROOT")
        fd = sys.stdin.fileno() if input_fd is None else input_fd
        private_pipe(fd)
        private_pipe(channel.fileno())
        phase = "PRIVATE_INPUT"
        request = decode(read_frame(fd, maximum=16000000, deadline=time.monotonic() + 15))
        _fields(request, "schema source_commit runtime result_sha256 enable_sha256")
        require(
            request["schema"] == "GOLD_DELEGATED_PROOF_INPUT_V1" and request["source_commit"] == source_commit,
            "GOLD_DELEGATED_INPUT",
        )
        at = now_ms()
        enabled = load_enabled(
            registry=registry, source_commit=source_commit, account_ref_sha256=account, now_ms=at, role="executor"
        )
        require(_digest(enabled) == request["enable_sha256"], "GOLD_DELEGATED_ENABLE_CHANGED")
        phase = "ORIGINAL_EVIDENCE"
        with ExitStack() as stack:
            stores = _read_only_stores(stack, root, uid)
            proof = export(
                root=root,
                runtime=request["runtime"],
                result_sha256=request["result_sha256"],
                enable=enabled,
                registry=registry,
                source_commit=source_commit,
                uid=uid,
                now_ms=at,
                stores=stores,
            )
        phase = "FINAL_CONFIGURATION"
        verify_source(source_commit)
        require(
            demo_authority.load_registry_for_executor() == registry and fixed_account() == (root, account),
            "GOLD_DELEGATED_CONFIGURATION_CHANGED",
        )
        require(
            load_enabled(
                registry=registry,
                source_commit=source_commit,
                account_ref_sha256=account,
                now_ms=now_ms(),
                role="executor",
            )
            == enabled,
            "GOLD_DELEGATED_ENABLE_CHANGED",
        )
        admit(
            delegation=enabled["delegation"],
            enable=enabled,
            base_policy=delegation_base(enabled["delegation"]),
            registry=registry,
            proof=proof,
            now_ms=now_ms(),
        )
        print(json.dumps(proof, sort_keys=True, separators=(",", ":"), allow_nan=False), file=channel, flush=True)
        return 0
    except Exception:
        result = dict(
            schema="GOLD_DELEGATED_PROOF_RESULT_V1",
            status="INPUTS_UNRESOLVED",
            source_commit=source_commit,
            phase=phase,
            submission_enabled=False,
            economic_authority=False,
        )
        try:
            print(json.dumps(result, sort_keys=True, separators=(",", ":")), file=channel, flush=True)
        except (OSError, ValueError):
            pass
        return 3
    finally:
        os.umask(previous)


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args(argv)
    return run(source_commit=args.source_commit)


if __name__ == "__main__":
    raise SystemExit(main())
