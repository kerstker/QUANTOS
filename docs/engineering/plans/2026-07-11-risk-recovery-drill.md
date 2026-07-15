# Risk Recovery Drill Implementation Plan

> **Execution:** Follow the repository's task-by-task TDD workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an isolated offline drill that proves critical QUANTOS risk state survives restart, remains fail-closed, and produces machine-verifiable M4 evidence.

**Architecture:** Existing kill-switch, pre-trade gate, `LiveLedger`, and `DurableAuditLog` APIs remain authoritative. A new tool creates a deterministic state bundle under an isolated directory, closes and reopens every persisted component, verifies semantic and byte-level recovery, injects two safe faults in copied artifacts, and atomically publishes evidence only after success. The top-tier assessor consumes that evidence through a dedicated fail-closed checker.

**Tech Stack:** Python 3.11+ stdlib, Pydantic contracts, SQLite, pytest, existing QUANTOS governance assessor.

## Global Constraints

- Preserve `L2_DRY_RUN_ONLY`; never enable live flags, broker connectivity, credentials, order submission, or AI direct trading.
- Never read from or write to active `data/cache`, `key`, or configured live state paths.
- Use only caller-supplied empty drill directories; reject symlinks, active paths, and non-empty targets.
- Reuse existing persistence formats and risk-gate APIs; do not create a second risk-state model.
- Any path ambiguity, state mismatch, unexpected gate result, audit failure, malformed evidence, or RTO breach fails closed.
- Failed drills never replace prior PASS evidence.
- Follow strict TDD: write each behavioral test, observe the expected failure, then add the minimum implementation.
- Prefix every shell command with `rtk`; use `rtk python scripts/validate.py` as the canonical final gate.

---

### Task 1: Explicit SQLite Store Lifecycle

**Files:**
- Modify: `live/execution/position_ledger.py`
- Modify: `services/monitoring/audit_store.py`
- Modify: `tests/live/test_position_ledger.py`
- Modify: `tests/monitoring/test_audit_store.py`

**Interfaces:**
- Consumes: existing `LiveLedger._conn` and `DurableAuditLog._c` SQLite connections.
- Produces: idempotent public `LiveLedger.close() -> None` and `DurableAuditLog.close() -> None` methods used by the restart drill.

- [ ] **Step 1: Write failing lifecycle tests**

Add `import sqlite3` to `tests/monitoring/test_audit_store.py`, then add:

```python
def test_close_releases_durable_audit_connection(tmp_path):
    log = _log(tmp_path)
    log.append(event_id="e1", actor="risk-drill", action="start", timestamp=1)
    log.close()
    log.close()
    with pytest.raises(sqlite3.ProgrammingError):
        _ = log.events
```

Add to `tests/live/test_position_ledger.py`:

```python
def test_close_releases_live_ledger_connection(tmp_path):
    ledger = LiveLedger(str(tmp_path / "live.db"), create=True)
    ledger.close()
    ledger.close()
    with pytest.raises(sqlite3.ProgrammingError):
        ledger.orders()
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
rtk python -m pytest -q tests/live/test_position_ledger.py::test_close_releases_live_ledger_connection tests/monitoring/test_audit_store.py::test_close_releases_durable_audit_connection
```

Expected: both tests fail with `AttributeError` because `close` does not exist.

- [ ] **Step 3: Implement idempotent close methods**

Add to `LiveLedger`:

```python
    def close(self) -> None:
        self._conn.close()
```

Add to `DurableAuditLog`:

```python
    def close(self) -> None:
        self._c.close()
```

- [ ] **Step 4: Run focused lifecycle suites and verify GREEN**

Run:

```bash
rtk python -m pytest -q tests/live/test_position_ledger.py tests/monitoring/test_audit_store.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit the lifecycle change**

```bash
rtk git add live/execution/position_ledger.py services/monitoring/audit_store.py tests/live/test_position_ledger.py tests/monitoring/test_audit_store.py
rtk git commit -m "feat(state): add explicit persistent store lifecycle"
```

---

### Task 2: Offline Risk Recovery Drill Core

**Files:**
- Create: `tools/risk_recovery_drill.py`
- Create: `tests/live/test_risk_recovery_drill.py`

**Interfaces:**
- Consumes: `KillSwitch`, `save_kill_switch_state`, `load_kill_switch_state`, `LiveLedger`, `DurableAuditLog`, `PreTradeRiskGate`, and existing risk contracts.
- Produces: `RiskRecoveryDrillError`, `_prepare_drill_dir(path, forbidden_roots)`, and `run_risk_recovery_drill(*, drill_dir, rto_ms, observed_at_ms) -> dict[str, Any]`.

- [ ] **Step 1: Write the failing successful-restart test**

Create `tests/live/test_risk_recovery_drill.py`:

```python
import json

import pytest


def test_risk_recovery_drill_reopens_critical_state_and_fails_closed(tmp_path):
    from tools.risk_recovery_drill import run_risk_recovery_drill

    result = run_risk_recovery_drill(
        drill_dir=str(tmp_path / "drill"),
        rto_ms=30_000,
        observed_at_ms=1_783_734_400_000,
    )

    assert result["schema_version"] == 1
    assert result["verdict"] == "PASS"
    assert result["drill_mode"] == "ISOLATED_OFFLINE"
    assert result["recovered_state_count"] == 3
    assert result["kill_switch_persisted"] is True
    assert result["gate_rejected_while_tripped"] is True
    assert result["gate_rejection_code"] == "KILL_SWITCH_ACTIVE"
    assert result["ledger_recovered"] is True
    assert result["audit_chain_verified"] is True
    assert result["corrupt_kill_switch_failed_closed"] is True
    assert result["tampered_audit_detected"] is True
    assert result["active_state_untouched"] is True
    assert result["pre_restart_sha256"] == result["post_restart_sha256"]
    assert result["ledger_before"] == result["ledger_after"]
    assert result["rto_met"] is True
    json.dumps(result)
```

- [ ] **Step 2: Run the successful-restart test and verify RED**

```bash
rtk python -m pytest -q tests/live/test_risk_recovery_drill.py::test_risk_recovery_drill_reopens_critical_state_and_fails_closed
```

Expected: collection fails with `ModuleNotFoundError: tools.risk_recovery_drill`.

- [ ] **Step 3: Add path, hash, and semantic helpers**

Create `tools/risk_recovery_drill.py` with these imports and helpers:

```python
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any, Iterable

from live.execution.position_ledger import LiveLedger
from live.governance.kill_switch_store import load_kill_switch_state, save_kill_switch_state
from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.rejection import RejectionReason
from quantos.contracts.trading import OrderIntent, OrderSide
from services.monitoring.audit_store import DurableAuditLog
from services.risk.pre_trade import (
    AccountRiskState,
    KillSwitch,
    MarketContext,
    PreTradeRiskGate,
    RiskContext,
    RiskLimits,
    StrategyApproval,
)


class RiskRecoveryDrillError(RuntimeError):
    """The drill cannot prove complete fail-closed risk-state recovery."""


_REPO_ROOT = Path(__file__).resolve().parents[1]
_ACTIVE_ROOTS = (_REPO_ROOT / "data" / "cache", _REPO_ROOT / "key")


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _prepare_drill_dir(path: str, forbidden_roots: Iterable[Path] = _ACTIVE_ROOTS) -> Path:
    raw = Path(path).absolute()
    for candidate in (raw, *raw.parents):
        if candidate.exists() and candidate.is_symlink():
            raise RiskRecoveryDrillError("drill path must not contain symlinks")
    resolved = raw.resolve(strict=False)
    if any(_is_within(resolved, root.resolve(strict=False)) for root in forbidden_roots):
        raise RiskRecoveryDrillError("drill path overlaps active or credential state")
    if resolved.exists():
        if not resolved.is_dir() or any(resolved.iterdir()):
            raise RiskRecoveryDrillError("drill directory must be empty or absent")
    else:
        resolved.mkdir(parents=True)
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _state_hashes(root: Path) -> dict[str, str]:
    names = ("kill_switch.json", "ledger.db", "audit.db")
    return {name: _sha256_file(root / name) for name in names}


def _ledger_summary(ledger: LiveLedger) -> dict[str, Any]:
    return {
        "orders": ledger.orders(),
        "fills": ledger.fills(),
        "positions": ledger.all_positions(),
    }


def _clean_gate_inputs() -> tuple[OrderIntent, RiskContext, RiskLimits]:
    intent = OrderIntent(
        instrument_id="AAA", side=OrderSide.BUY, quantity=10, strategy_id="risk-drill")
    context = RiskContext(
        account=AccountRiskState(
            account_id="drill-account", cash=1_000_000.0, equity=1_000_000.0,
            peak_equity=1_000_000.0),
        market=MarketContext(
            instrument_id="AAA", known=True, reference_price=100.0, market_open=True,
            price_as_of=1_000, est_daily_volume=1_000_000.0, instrument_vol=0.2),
        strategy=StrategyApproval(
            strategy_id="risk-drill", approved=True, approval_id="drill-approval"),
        now=1_000,
    )
    limits = RiskLimits(
        allowed_instruments=frozenset({"AAA"}),
        allowed_accounts=frozenset({"drill-account"}),
    )
    return intent, context, limits
```

- [ ] **Step 4: Implement the close-and-reopen drill**

Append the complete orchestration function:

```python
def run_risk_recovery_drill(
    *,
    drill_dir: str,
    rto_ms: int,
    observed_at_ms: int,
) -> dict[str, Any]:
    if rto_ms <= 0:
        raise RiskRecoveryDrillError("rto_ms must be positive")
    if observed_at_ms < 0:
        raise RiskRecoveryDrillError("observed_at_ms must be non-negative")

    root = _prepare_drill_dir(drill_dir)
    kill_path = root / "kill_switch.json"
    ledger_path = root / "ledger.db"
    audit_path = root / "audit.db"
    started = time.perf_counter()

    audit = DurableAuditLog(str(audit_path), create=True)
    audit.append(
        event_id="risk-recovery-drill.start",
        actor="risk-recovery-drill",
        action="risk_recovery.start",
        timestamp=observed_at_ms,
        target="isolated-state-bundle",
    )
    kill_switch = KillSwitch()
    kill_switch.arm(
        Actor(actor_id="risk-recovery-drill", kind=ActorKind.SYSTEM),
        "isolated recovery drill halt",
        at=observed_at_ms,
        incident_id="risk-recovery-drill-incident",
        audit_sink=audit,
    )
    save_kill_switch_state(str(kill_path), kill_switch.state)

    ledger = LiveLedger(str(ledger_path), create=True)
    ledger.record_submission(
        "drill-order-1", "AAA", OrderSide.BUY, 2.0,
        strategy_id="risk-drill", submitted_at=observed_at_ms)
    ledger.record_fill(
        "drill-fill-1", "drill-order-1", "AAA", OrderSide.BUY, 2.0, 100.0,
        strategy_id="risk-drill", filled_at=observed_at_ms + 1)
    ledger_before = _ledger_summary(ledger)
    commitment = audit.seal()
    ledger.close()
    audit.close()
    pre_hashes = _state_hashes(root)

    recovered_state = load_kill_switch_state(str(kill_path))
    recovered_ledger = LiveLedger(str(ledger_path), create=False)
    recovered_audit = DurableAuditLog(str(audit_path), create=False)
    ledger_after = _ledger_summary(recovered_ledger)
    kill_switch_persisted = (
        recovered_state.tripped
        and recovered_state.incident_id == "risk-recovery-drill-incident"
        and recovered_state.reason == "isolated recovery drill halt"
    )
    intent, context, limits = _clean_gate_inputs()
    decision = PreTradeRiskGate(
        limits, kill_switch=KillSwitch(recovered_state)).evaluate(intent, context)
    gate_rejected = decision.reasons == (RejectionReason.KILL_SWITCH_ACTIVE,)
    ledger_recovered = ledger_after == ledger_before
    audit_verified = recovered_audit.verify_against(commitment)
    recovered_ledger.close()
    recovered_audit.close()
    post_hashes = _state_hashes(root)

    corrupt_kill_path = root / "fault-corrupt-kill-switch.json"
    corrupt_kill_path.write_text("{corrupt", encoding="utf-8")
    corrupt_state = load_kill_switch_state(str(corrupt_kill_path))
    corrupt_failed_closed = corrupt_state.tripped and corrupt_state.is_active

    tampered_audit_path = root / "fault-tampered-audit.db"
    shutil.copy2(audit_path, tampered_audit_path)
    connection = sqlite3.connect(str(tampered_audit_path))
    connection.execute(
        "UPDATE audit_events SET actor='tampered' WHERE event_id='risk-recovery-drill.start'")
    connection.commit()
    connection.close()
    tampered_audit = DurableAuditLog(str(tampered_audit_path), create=False)
    tamper_detected = not tampered_audit.chain_valid()
    tampered_audit.close()

    elapsed_ms = int(round((time.perf_counter() - started) * 1000))
    checks = {
        "kill_switch_persisted": kill_switch_persisted,
        "gate_rejected_while_tripped": gate_rejected,
        "ledger_recovered": ledger_recovered,
        "audit_chain_verified": audit_verified,
        "corrupt_kill_switch_failed_closed": corrupt_failed_closed,
        "tampered_audit_detected": tamper_detected,
        "active_state_untouched": True,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if pre_hashes != post_hashes:
        failed.append("state_digest_match")
    if elapsed_ms > rto_ms:
        failed.append("rto")
    if failed:
        raise RiskRecoveryDrillError("risk recovery drill failed: " + ", ".join(failed))

    return {
        "schema_version": 1,
        "verdict": "PASS",
        "drill_mode": "ISOLATED_OFFLINE",
        "observed_at_ms": observed_at_ms,
        "elapsed_ms": elapsed_ms,
        "rto_ms": rto_ms,
        "rto_met": True,
        "artifact_names": ["audit.db", "kill_switch.json", "ledger.db"],
        "recovered_state_count": 3,
        "pre_restart_sha256": pre_hashes,
        "post_restart_sha256": post_hashes,
        "ledger_before": ledger_before,
        "ledger_after": ledger_after,
        "gate_rejection_code": RejectionReason.KILL_SWITCH_ACTIVE.value,
        **checks,
    }
```

- [ ] **Step 5: Run the successful-restart test and verify GREEN**

```bash
rtk python -m pytest -q tests/live/test_risk_recovery_drill.py::test_risk_recovery_drill_reopens_critical_state_and_fails_closed
```

Expected: `1 passed`.

- [ ] **Step 6: Add failing path and RTO tests**

Append to `tests/live/test_risk_recovery_drill.py`:

```python
def test_risk_recovery_drill_rejects_nonempty_and_forbidden_targets(tmp_path):
    from tools.risk_recovery_drill import RiskRecoveryDrillError, _prepare_drill_dir

    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(RiskRecoveryDrillError, match="empty"):
        _prepare_drill_dir(str(nonempty), forbidden_roots=())

    active = tmp_path / "active"
    with pytest.raises(RiskRecoveryDrillError, match="active"):
        _prepare_drill_dir(str(active / "drill"), forbidden_roots=(active,))


def test_risk_recovery_drill_rejects_symlink_target(tmp_path):
    from tools.risk_recovery_drill import RiskRecoveryDrillError, _prepare_drill_dir

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(RiskRecoveryDrillError, match="symlink"):
        _prepare_drill_dir(str(link), forbidden_roots=())


def test_risk_recovery_drill_fails_when_rto_is_exceeded(tmp_path, monkeypatch):
    import tools.risk_recovery_drill as drill

    ticks = iter([10.0, 10.2])
    monkeypatch.setattr(drill.time, "perf_counter", lambda: next(ticks))
    with pytest.raises(drill.RiskRecoveryDrillError, match="rto"):
        drill.run_risk_recovery_drill(
            drill_dir=str(tmp_path / "drill"),
            rto_ms=100,
            observed_at_ms=1_783_734_400_000,
        )
```

- [ ] **Step 7: Run path and RTO tests**

```bash
rtk python -m pytest -q tests/live/test_risk_recovery_drill.py
```

Expected: all tests pass, with the symlink test skipped only when Windows denies symlink creation.

- [ ] **Step 8: Commit the recovery core**

```bash
rtk git add tools/risk_recovery_drill.py tests/live/test_risk_recovery_drill.py
rtk git commit -m "feat(risk): add isolated recovery drill core"
```

---

### Task 3: Evidence Publication And Governance Boundary

**Files:**
- Modify: `tools/risk_recovery_drill.py`
- Modify: `tests/live/test_risk_recovery_drill.py`
- Create: `tests/governance/test_risk_recovery_drill_boundary.py`

**Interfaces:**
- Consumes: Task 2 `run_risk_recovery_drill` result.
- Produces: `_write_evidence(path, result)`, `run_and_write_evidence(...)`, and CLI arguments `--drill-dir`, `--rto-ms`, `--observed-at-ms`, and `--evidence`.

- [ ] **Step 1: Write failing publication-preservation test**

Append to `tests/live/test_risk_recovery_drill.py`:

```python
def test_failed_drill_does_not_replace_prior_pass_evidence(tmp_path, monkeypatch):
    import tools.risk_recovery_drill as drill

    evidence = tmp_path / "evidence.json"
    prior = {"schema_version": 1, "verdict": "PASS", "marker": "prior"}
    evidence.write_text(json.dumps(prior), encoding="utf-8")

    def fail(**_kwargs):
        raise drill.RiskRecoveryDrillError("expected failure")

    monkeypatch.setattr(drill, "run_risk_recovery_drill", fail)
    with pytest.raises(drill.RiskRecoveryDrillError, match="expected failure"):
        drill.run_and_write_evidence(
            drill_dir=str(tmp_path / "drill"),
            evidence_path=str(evidence),
            rto_ms=30_000,
            observed_at_ms=1_783_734_400_000,
        )
    assert json.loads(evidence.read_text(encoding="utf-8")) == prior
```

- [ ] **Step 2: Run the publication test and verify RED**

```bash
rtk python -m pytest -q tests/live/test_risk_recovery_drill.py::test_failed_drill_does_not_replace_prior_pass_evidence
```

Expected: fail because `run_and_write_evidence` does not exist.

- [ ] **Step 3: Implement atomic publication and CLI**

Append to `tools/risk_recovery_drill.py`:

```python
def _write_evidence(path: str, result: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(result, output, indent=2, sort_keys=True)
        output.write("\n")
    os.replace(temporary, destination)


def run_and_write_evidence(
    *,
    drill_dir: str,
    evidence_path: str,
    rto_ms: int,
    observed_at_ms: int,
) -> dict[str, Any]:
    result = run_risk_recovery_drill(
        drill_dir=drill_dir, rto_ms=rto_ms, observed_at_ms=observed_at_ms)
    _write_evidence(evidence_path, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run isolated QUANTOS risk recovery drill")
    parser.add_argument("--drill-dir", required=True)
    parser.add_argument("--rto-ms", type=int, default=300_000)
    parser.add_argument("--observed-at-ms", type=int)
    parser.add_argument(
        "--evidence", default=os.path.join("evidence", "risk_recovery_drill_latest.json"))
    args = parser.parse_args()
    observed_at_ms = (
        args.observed_at_ms
        if args.observed_at_ms is not None
        else int(time.time() * 1000)
    )
    result = run_and_write_evidence(
        drill_dir=args.drill_dir,
        evidence_path=args.evidence,
        rto_ms=args.rto_ms,
        observed_at_ms=observed_at_ms,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Add governance boundary test**

Create `tests/governance/test_risk_recovery_drill_boundary.py`:

```python
import ast
from pathlib import Path


def test_risk_recovery_drill_has_no_network_broker_or_submission_boundary():
    path = Path("tools/risk_recovery_drill.py")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.names[0].name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not imports & {"requests", "httpx", "urllib", "socket", "ccxt"}
    lowered = source.lower()
    assert "submit_order" not in lowered
    assert "cancel_order" not in lowered
    assert "live_trading_enabled" not in lowered
    assert "real_broker_enabled" not in lowered
    assert "allow_order_submission" not in lowered
```

- [ ] **Step 5: Run publication and governance tests**

```bash
rtk python -m pytest -q tests/live/test_risk_recovery_drill.py tests/governance/test_risk_recovery_drill_boundary.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit evidence publication**

```bash
rtk git add tools/risk_recovery_drill.py tests/live/test_risk_recovery_drill.py tests/governance/test_risk_recovery_drill_boundary.py
rtk git commit -m "feat(risk): publish recovery drill evidence"
```

---

### Task 4: Machine-Verifiable M4 Assessment

**Files:**
- Modify: `services/governance/top_tier_assessment.py`
- Modify: `tests/governance/test_top_tier_assessment.py`
- Modify: `config/top_tier_requirements.json`
- Create: `evidence/risk_recovery_drill_latest.json`
- Regenerate: `evidence/top_tier_assessment.json`
- Regenerate: `docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md`

**Interfaces:**
- Consumes: Task 3 schema-version-1 evidence.
- Produces: checker name `risk_recovery_evidence`, M4 PASS only when recovery, fault injection, digests, rejection code, state count, and RTO all verify.

- [ ] **Step 1: Write failing assessor tests**

Append to `tests/governance/test_top_tier_assessment.py`:

```python
def _valid_risk_recovery_evidence():
    hashes = {"audit.db": "a" * 64, "kill_switch.json": "b" * 64, "ledger.db": "c" * 64}
    ledger = {
        "orders": [{"client_order_id": "drill-order-1"}],
        "fills": [{"fill_id": "drill-fill-1"}],
        "positions": {"AAA": 2.0},
    }
    return {
        "schema_version": 1,
        "verdict": "PASS",
        "drill_mode": "ISOLATED_OFFLINE",
        "observed_at_ms": 1_783_734_400_000,
        "elapsed_ms": 10,
        "rto_ms": 100,
        "rto_met": True,
        "artifact_names": ["audit.db", "kill_switch.json", "ledger.db"],
        "recovered_state_count": 3,
        "pre_restart_sha256": hashes,
        "post_restart_sha256": hashes,
        "ledger_before": ledger,
        "ledger_after": ledger,
        "kill_switch_persisted": True,
        "gate_rejected_while_tripped": True,
        "gate_rejection_code": "KILL_SWITCH_ACTIVE",
        "ledger_recovered": True,
        "audit_chain_verified": True,
        "corrupt_kill_switch_failed_closed": True,
        "tampered_audit_detected": True,
        "active_state_untouched": True,
    }


def test_risk_recovery_checker_requires_all_recovery_and_fault_invariants(tmp_path):
    evidence = tmp_path / "risk-recovery.json"
    registry = _write_registry(tmp_path, [_requirement(
        "RISK.RECOVERY.DRILL",
        domain="risk_management",
        tier="B",
        target_maturity="M4",
        red_line=True,
        checker="risk_recovery_evidence",
        checker_args={"path": "risk-recovery.json", "pass_maturity": "M4"},
    )])
    payload = _valid_risk_recovery_evidence()
    payload["tampered_audit_detected"] = False
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    assert assess_repository(tmp_path, registry)["requirements"][0]["status"] == "FAILED"

    evidence.write_text(json.dumps(_valid_risk_recovery_evidence()), encoding="utf-8")
    result = assess_repository(tmp_path, registry)["requirements"][0]
    assert result["status"] == "PASS"
    assert result["current_maturity"] == "M4"
```

- [ ] **Step 2: Run assessor test and verify RED**

```bash
rtk python -m pytest -q tests/governance/test_top_tier_assessment.py::test_risk_recovery_checker_requires_all_recovery_and_fault_invariants
```

Expected: fail with invalid checker `risk_recovery_evidence`.

- [ ] **Step 3: Implement the fail-closed checker**

Add before `_result` in `services/governance/top_tier_assessment.py`:

```python
def _risk_recovery_evidence(repo_root: Path, args: dict[str, Any], target: str) -> dict[str, Any]:
    path = args.get("path")
    if not isinstance(path, str):
        return _result("UNVERIFIED", "M0", [], "checker requires an evidence path")
    evidence_path = repo_root / path
    if not evidence_path.is_file():
        return _result("FAILED", "M2", [path], f"missing: {path}")
    try:
        document = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _result("FAILED", "M2", [path], f"invalid risk recovery evidence: {exc}")

    required = {
        "schema_version": 1,
        "verdict": "PASS",
        "drill_mode": "ISOLATED_OFFLINE",
        "rto_met": True,
        "artifact_names": ["audit.db", "kill_switch.json", "ledger.db"],
        "recovered_state_count": 3,
        "kill_switch_persisted": True,
        "gate_rejected_while_tripped": True,
        "gate_rejection_code": "KILL_SWITCH_ACTIVE",
        "ledger_recovered": True,
        "audit_chain_verified": True,
        "corrupt_kill_switch_failed_closed": True,
        "tampered_audit_detected": True,
        "active_state_untouched": True,
    }
    failures = [key for key, expected in required.items() if document.get(key) != expected]
    numeric = ("observed_at_ms", "elapsed_ms", "rto_ms")
    if any(not isinstance(document.get(key), int) for key in numeric):
        failures.append("numeric_fields")
    elif document["observed_at_ms"] < 0 or document["elapsed_ms"] < 0 or document["rto_ms"] <= 0:
        failures.append("numeric_ranges")
    elif document["elapsed_ms"] > document["rto_ms"]:
        failures.append("rto_threshold")
    pre = document.get("pre_restart_sha256")
    post = document.get("post_restart_sha256")
    expected_names = {"audit.db", "kill_switch.json", "ledger.db"}
    if not isinstance(pre, dict) or set(pre) != expected_names or pre != post:
        failures.append("state_digests")
    elif any(
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in pre.values()
    ):
        failures.append("digest_shape")
    ledger_before = document.get("ledger_before")
    ledger_after = document.get("ledger_after")
    if not isinstance(ledger_before, dict) or ledger_before != ledger_after:
        failures.append("ledger_summary")
    elif (
        not ledger_before.get("orders")
        or not ledger_before.get("fills")
        or not ledger_before.get("positions")
    ):
        failures.append("ledger_summary_empty")
    if failures:
        return _result(
            "FAILED", "M3", [path],
            "risk recovery evidence failed: " + ", ".join(sorted(set(failures))))
    maturity = args.get("pass_maturity", target)
    return _result(
        "PASS", maturity, [path],
        "isolated risk-state restart, gate rejection, ledger recovery, audit integrity, and fault injection verified")
```

Register it in `CHECKERS`:

```python
    "risk_recovery_evidence": _risk_recovery_evidence,
```

- [ ] **Step 4: Run assessor suite and verify GREEN**

```bash
rtk python -m pytest -q tests/governance/test_top_tier_assessment.py
```

Expected: all tests pass.

- [ ] **Step 5: Replace manual requirement with the evidence checker**

Change only `RISK.RECOVERY.DRILL` in `config/top_tier_requirements.json`:

```json
"checker": "risk_recovery_evidence",
"checker_args": {
  "path": "evidence/risk_recovery_drill_latest.json",
  "pass_maturity": "M4"
},
"next_action": "Schedule recurring isolated risk recovery drills and monitor freshness"
```

- [ ] **Step 6: Run one real isolated drill and generate canonical artifacts**

First verify `.runtime/risk-recovery-drill-20260711-001` does not exist. Then run:

```bash
rtk python tools/risk_recovery_drill.py --drill-dir .runtime/risk-recovery-drill-20260711-001 --rto-ms 300000
rtk python scripts/assess_top_tier.py
```

Expected: drill verdict `PASS`; assessment reports `NOT_TOP_TIER - 16/22 pass`; generated matrix shows `RISK.RECOVERY.DRILL` M4 PASS and only two open red lines.

- [ ] **Step 7: Verify assessment drift check**

```bash
rtk python scripts/assess_top_tier.py --check
```

Expected: exit zero with no artifact drift.

- [ ] **Step 8: Commit assessment integration and evidence**

```bash
rtk git add services/governance/top_tier_assessment.py tests/governance/test_top_tier_assessment.py config/top_tier_requirements.json evidence/risk_recovery_drill_latest.json evidence/top_tier_assessment.json docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md
rtk git commit -m "feat(risk): verify restart recovery evidence"
```

---

### Task 5: Documentation, Full Gate, And Publication

**Files:**
- Modify: `docs/PROJECT_STATE.md`
- Modify: `docs/QUANTOS_TOP_TIER_STANDARD.md`
- Regenerate: `evidence/top_tier_assessment.json`
- Regenerate: `docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md`

**Interfaces:**
- Consumes: Task 4 evidence and validation output.
- Produces: current project handoff with exact evidence, honest remaining red lines, and a published branch when GitHub connectivity permits.

- [ ] **Step 1: Update project-state evidence without inflating the verdict**

Add a dated paragraph to the top-tier status block in `docs/PROJECT_STATE.md` stating:

```markdown
> **2026-07-11 risk recovery evidence:** `RISK.RECOVERY.DRILL` now has an isolated
> offline restart drill covering persisted kill-switch state, pre-trade gate
> rejection, SQLite ledger reconstruction, durable audit-chain verification,
> corrupt-state fail-closed behavior, and audit-tamper detection. Canonical
> evidence is `evidence/risk_recovery_drill_latest.json`. The assessment is
> `NOT_TOP_TIER`, 16/22 PASS; remaining red lines are
> `GOV.IDENTITY.STRONG_AUTH` and `PLATFORM.SUPPLY_CHAIN`.
```

Add to `docs/QUANTOS_TOP_TIER_STANDARD.md` under the evidence contract:

```markdown
Risk recovery evidence is scoped to the state classes and failure injections
named in its artifact. It does not imply HA, venue recovery, production DR, or
live-trading readiness.
```

- [ ] **Step 2: Regenerate and run focused verification**

```bash
rtk python scripts/assess_top_tier.py
rtk python -m pytest -q tests/live/test_risk_recovery_drill.py tests/live/test_position_ledger.py tests/monitoring/test_audit_store.py tests/governance/test_risk_recovery_drill_boundary.py tests/governance/test_top_tier_assessment.py
```

Expected: all focused tests pass and assessment remains 16/22 PASS with `NOT_TOP_TIER`.

- [ ] **Step 3: Run the canonical fail-closed gate**

```bash
rtk python scripts/validate.py
```

Expected: top-tier artifact check, governance scanner, backend suites, live stack, frontend typecheck, frontend tests, and frontend production build all pass.

- [ ] **Step 4: Review the scoped diff and repository status**

```bash
rtk git diff --check
rtk git status --short
```

Expected: no whitespace errors; only intended risk-recovery files plus the pre-existing untracked `docs/reports/gonogo-2026-07-10.md` are visible.

- [ ] **Step 5: Commit final documentation**

```bash
rtk git add docs/PROJECT_STATE.md docs/QUANTOS_TOP_TIER_STANDARD.md evidence/top_tier_assessment.json docs/QUANTOS_TOP_TIER_CAPABILITY_MATRIX.md
rtk git commit -m "docs: record risk recovery drill evidence"
```

- [ ] **Step 6: Push the feature branch**

```bash
rtk git push -u origin codex/top-tier-wave0
```

Expected: pre-push canonical gate remains green and GitHub confirms `codex/top-tier-wave0`. If the network resets, preserve local commits and report publication as pending without questioning local correctness.
