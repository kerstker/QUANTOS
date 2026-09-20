"""RF3 daily forward observer: one bounded public step per UTC day (research only).

Single-run, reviewable observer for
docs/engineering/plans/2026-09-20-rf3-daily-forward.md.  It registers a frozen plan
in a new exclusive state directory, then performs at most one bounded public request
per invocation, only inside the 00:00Z-00:05Z window and only for the fixed BTCUSDC
90-day URL, and publishes an intended target.

Boundaries.  No service/timer installation, no credentials, no order/broker path, no
PnL and no automatic continuation.  The only network access is the existing bounded
public child process ``tools.profitability_public_http`` (15s process timeout, no
redirects).  Registration is offline.  A step takes an exclusive non-blocking lock,
allows at most three attempts per slot, never overwrites an artifact and never
backfills a window that already closed.  ``--status`` is read-only.

Identity.  The collector identity is scoped to the two RF3 files, the RF2 signal
module, the public HTTP tool, the containing package ``__init__`` files when present,
the dependency lock and the interpreter - never to unrelated files.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from services.research import rf3_daily_forward as rf3

ROOT = Path(__file__).resolve().parents[1]
SCOPE_FILES = ("services/research/rf3_daily_forward.py", "tools/rf3_daily_forward.py",
               "services/research/rf2_bounded_strategy.py", "tools/profitability_public_http.py",
               "services/research/__init__.py", "tools/__init__.py")
LOCKFILE = "requirements-ci.lock"
FORBIDDEN_PREFIXES = ("/srv", "/opt")
ANCESTRY_SCHEMA = "QUANTOS_RF3_ANCESTRY_V1"
ATTEMPT_SCHEMA = "QUANTOS_RF3_ATTEMPT_V1"
STATUS_SCHEMA = "QUANTOS_RF3_DAILY_FORWARD_STATUS_V1"
RF2_CASE_SCHEMA = "QUANTOS_RF2_BOUNDED_CASE_V1"
RF2_CANDIDATE = "return_sign90"
RF2_SIGNAL = "RETURN_SIGN_90_SAME_WINDOW_NO_HYSTERESIS_V1"
TECHNICAL = "TECHNICAL_VALIDATION_ONLY"
AUTHORITY_FIELDS = ("authoritative", "authority_admitted", "live_eligible", "paper_admitted")
MAX_ATTEMPTS_PER_SLOT = 3
FETCH_TIMEOUT_S = 15
CHILD_REQUEST_TIMEOUT_S = 10
FAILURE_OUTCOMES = frozenset({"FETCH_FAILED", "REJECTED", "LATE", "IDENTITY_DRIFT",
                              "ATTEMPTS_EXHAUSTED", "LOCKED"})


class PathRejection(ValueError):
    """A rejected path argument; nothing was created, read or overwritten."""


class RegistrationRejection(ValueError):
    """A rejected ancestry document; no state root was created."""


def now_ms():
    return time.time_ns() // 1_000_000


def source_fingerprint(repo_root=ROOT):
    """Scoped collector identity: only the bound components can invalidate a window."""
    root = Path(repo_root)
    components = {}
    for relative in SCOPE_FILES:
        path = root / relative
        if not path.is_file():
            if path.name == "__init__.py":
                continue
            raise PathRejection(f"scoped source file is missing: {path}")
        components[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    lock = root / LOCKFILE
    components["lock"] = hashlib.sha256(lock.read_bytes()).hexdigest() if lock.is_file() else None
    components["python"] = sys.version
    return {"components": components, "fingerprint": rf3.digest(components)}


def collector_identity(repo_root=ROOT):
    return source_fingerprint(repo_root)["fingerprint"]


def _reject_symlink_chain(path, label):
    current = path
    while True:
        if current.is_symlink():
            raise PathRejection(f"{label} path may not be a symlink: {current}")
        parent = current.parent
        if parent == current:
            return
        current = parent


def _resolve_root(value, *, must_exist):
    path = Path(value)
    if not path.is_absolute():
        raise PathRejection("--state-root must be an absolute path")
    _reject_symlink_chain(path, "state root")
    resolved = path.resolve()
    for forbidden in FORBIDDEN_PREFIXES:
        prefix = Path(forbidden)
        if resolved == prefix or prefix in resolved.parents:
            raise PathRejection(f"state root under {forbidden} is forbidden")
    if must_exist and not path.is_dir():
        raise PathRejection(f"state root does not exist: {path}")
    return path


def _new_root(value):
    path = _resolve_root(value, must_exist=False)
    if path.exists():
        raise PathRejection(f"state root must not already exist (no overwrite): {path}")
    if not path.parent.is_dir():
        raise PathRejection(f"state root parent directory does not exist: {path.parent}")
    return path


def _read_json(path):
    _reject_symlink_chain(path, "state artifact")
    try:
        return rf3.strict_loads(path.read_text(encoding="utf-8"))
    except rf3.Rf3Rejection as exc:
        raise PathRejection(f"state artifact is not readable: {path} ({exc.code})") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise PathRejection(f"state artifact is missing or unreadable: {path}") from exc


def _write_new(path, payload):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, allow_nan=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _lock(root):
    _reject_symlink_chain(root / "lock", "lock")
    handle = os.open(root / "lock", os.O_RDWR)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(handle)
        return None
    return handle


def _check_ancestry(case):
    if not isinstance(case, dict):
        raise RegistrationRejection("research result must be a JSON object")
    if case.get("schema") != RF2_CASE_SCHEMA:
        raise RegistrationRejection(f"ancestry must be an {RF2_CASE_SCHEMA} artifact")
    if case.get("candidate") != RF2_CANDIDATE or case.get("signal") != RF2_SIGNAL:
        raise RegistrationRejection("ancestry must be the RF2 return_sign90 technical case")
    if case.get("status") != TECHNICAL or case.get("evidence_class") != TECHNICAL:
        raise RegistrationRejection("ancestry must be a completed technical-only case")
    for field in AUTHORITY_FIELDS:
        if case.get(field) is not False:
            raise RegistrationRejection(f"ancestry must record {field} = false")


def register(state_root, research_result, *, clock=now_ms, repo_root=ROOT):
    """Create the exclusive state directory and freeze ancestry plus plan (offline)."""
    root = _new_root(state_root)
    result = Path(research_result)
    if not result.is_absolute():
        raise PathRejection("--research-result must be an absolute path")
    _reject_symlink_chain(result, "research result")
    if not result.is_file():
        raise PathRejection(f"research result is not a file: {result}")
    raw = result.read_bytes()
    try:
        case = rf3.strict_loads(raw.decode("utf-8"))
    except (rf3.Rf3Rejection, UnicodeDecodeError) as exc:
        raise RegistrationRejection(f"research result is not readable JSON: {exc}") from exc
    _check_ancestry(case)
    ancestry_hash = hashlib.sha256(raw).hexdigest()
    plan = rf3.make_plan(clock(), collector_identity(repo_root), ancestry_hash)
    try:
        root.mkdir()
    except FileExistsError as exc:
        raise PathRejection(f"state root must not already exist (no overwrite): {root}") from exc
    _write_new(root / "ancestry.json", {
        "schema": ANCESTRY_SCHEMA, "source_file": str(result), "sha256": ancestry_hash,
        "case_schema": case["schema"], "candidate": case["candidate"], "signal": case["signal"],
        "status": case["status"], "authoritative": False, "authority_admitted": False,
        "live_eligible": False, "paper_admitted": False})
    _write_new(root / "plan.json", plan)
    (root / "lock").touch(exist_ok=False)
    return {"schema": ANCESTRY_SCHEMA, "state_root": str(root), "ancestry_hash": ancestry_hash,
            "plan_hash": plan["plan_hash"], "collector_identity": plan["collector_identity"],
            "created_ms": plan["created_ms"], "slots": plan["slots"], "authority": False,
            "order_submission": False, "pnl": None}


def public_fetch(url, clock, *, repo_root=ROOT, timeout=FETCH_TIMEOUT_S):
    """One bounded public request through the existing fixed public HTTP child."""
    requested_ms = clock()
    if not isinstance(url, str) or not url.startswith(rf3.URL_PREFIX):
        return {"ok": False, "category": "UNREGISTERED_URL", "url": url,
                "requested_ms": requested_ms, "received_ms": requested_ms}
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "tools.profitability_public_http", url,
             str(CHILD_REQUEST_TIMEOUT_S), "--include-raw"],
            capture_output=True, text=True, timeout=timeout, check=False, cwd=repo_root)
    except subprocess.TimeoutExpired:
        return {"ok": False, "category": "TIMEOUT", "url": url,
                "requested_ms": requested_ms, "received_ms": clock()}
    received_ms = clock()
    receipt = {"ok": False, "url": url, "requested_ms": requested_ms, "received_ms": received_ms}
    if completed.returncode != 0:
        receipt["category"] = "SUBPROCESS_FAILED"
        return receipt
    try:
        result = rf3.strict_loads(completed.stdout)
    except rf3.Rf3Rejection:
        receipt["category"] = "INVALID_SUBPROCESS_JSON"
        return receipt
    if not isinstance(result, dict) or result.get("ok") is not True:
        receipt["category"] = (result.get("category") or "FETCH_FAILED") if isinstance(result, dict) else "INVALID_SUBPROCESS_JSON"
        return receipt
    receipt["ok"] = True
    receipt["raw_utf8"] = result.get("raw_utf8")
    return receipt


def _state(root):
    plan = _read_json(root / "plan.json")
    rf3.verify_plan(plan)
    ancestry = _read_json(root / "ancestry.json")
    if ancestry.get("sha256") != plan["ancestry_hash"]:
        raise RegistrationRejection("ancestry differs from registered plan")
    return plan


def _attempts(root, index):
    # Reserve before network I/O: interruption must also consume its budget.
    return sorted(root.glob(f"claim-{index}-*.json"))


def _saved_signal(root, index):
    path = root / f"signal-{index}.json"
    if not path.is_file():
        return None
    try:
        publication_path = root / f"publication-{index}.json"
        if not publication_path.is_file():
            return None  # Interrupted write is retained but not usable evidence.
        publication = _read_json(publication_path)
        if publication.get("status") == "LATE":
            return None
        saved = _read_json(path)
        attempt = saved["attempt"]
        if type(attempt) is not int or not 1 <= attempt <= MAX_ATTEMPTS_PER_SLOT:
            raise ValueError("invalid attempt index")
        record = _read_json(root / f"attempt-{index}-{attempt}.json")
        receipt = {key: record[key] for key in ("url", "requested_ms", "received_ms", "raw_utf8")}
        receipt["ok"] = True
        expected = rf3.issue_target(_state(root), receipt, saved["issued_ms"])
        recorded = saved["recorded_ms"]
        completed = publication["completed_ms"]
        if (record["status"] != "PUBLISHED" or expected["slot_index"] != index
                or type(recorded) is not int or not saved["issued_ms"] <= recorded < expected["entry_ms"]
                or type(completed) is not int or not recorded <= completed < expected["entry_ms"]
                or publication.get("status") != "PUBLISHED"
                or publication.get("signal_hash") != rf3.digest(saved)
                or record["recorded_ms"] != recorded
                or saved != {**expected, "attempt": attempt, "recorded_ms": recorded}):
            raise ValueError("saved signal differs from original receipt or publication window")
        return saved
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise RegistrationRejection(f"invalid saved signal {index}: {exc}") from exc


def _persist(root, index, slot_ms, status, reason, receipt, clock, signal=None):
    attempt = len(_attempts(root, index))
    record = {"schema": ATTEMPT_SCHEMA, "slot_index": index, "slot_ms": slot_ms,
              "attempt": attempt, "status": status, "reason": reason, "recorded_ms": clock(),
              "url": receipt.get("url") if isinstance(receipt, dict) else None,
              "requested_ms": receipt.get("requested_ms") if isinstance(receipt, dict) else None,
              "received_ms": receipt.get("received_ms") if isinstance(receipt, dict) else None,
              "receipt_sha256": None}
    if signal is not None and not signal["issued_ms"] <= record["recorded_ms"] < slot_ms + rf3.DEADLINE_MS:
        status, reason, signal = "LATE", "PUBLICATION_DEADLINE", None
        record.update(status=status, reason=reason)
    raw = receipt.get("raw_utf8") if isinstance(receipt, dict) else None
    if isinstance(raw, str):
        record["receipt_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        record["raw_utf8"] = raw
    _write_new(root / f"attempt-{index}-{attempt}.json", record)
    outcome = {"outcome": status, "slot_index": index, "reason": reason,
               "recorded_ms": record["recorded_ms"], "signal": None}
    if signal is not None:
        path = root / f"signal-{index}.json"
        if path.is_file():
            saved = _saved_signal(root, index)
            outcome.update(outcome="ALREADY_ISSUED" if saved is not None else "INCOMPLETE_PUBLICATION", signal=saved)
        else:
            published = dict(signal, attempt=attempt, recorded_ms=record["recorded_ms"])
            _write_new(path, published)
            completed = clock()
            valid = type(completed) is int and record["recorded_ms"] <= completed < signal["entry_ms"]
            _write_new(root / f"publication-{index}.json", {
                "status": "PUBLISHED" if valid else "LATE", "completed_ms": completed,
                "signal_hash": rf3.digest(published),
            })
            outcome.update(outcome="PUBLISHED" if valid else "LATE", signal=published if valid else None)
    return outcome


def run_once(state_root, *, clock=now_ms, fetch=public_fetch, identity=None, repo_root=ROOT):
    """Perform at most one bounded step; outside the window nothing is requested."""
    root = _resolve_root(state_root, must_exist=True)
    plan = _state(root)
    identity = identity or (lambda: collector_identity(repo_root))
    handle = _lock(root)
    if handle is None:
        return {"outcome": "LOCKED", "slot_index": None, "reason": "another step holds the lock",
                "signal": None}
    try:
        slots = plan["slots"]
        saved = {index: _saved_signal(root, index) for index in range(plan["slot_count"])}
        now = clock()
        deadline = plan["deadline_offset_ms"]
        open_index = next((index for index, slot in enumerate(slots)
                           if slot <= now < slot + deadline), None)
        if open_index is not None and saved[open_index] is not None:
            return {"outcome": "ALREADY_ISSUED", "slot_index": open_index, "reason": None,
                    "signal": saved[open_index]}
        if open_index is not None and (root / f"signal-{open_index}.json").exists():
            return {"outcome": "INCOMPLETE_PUBLICATION", "slot_index": open_index,
                    "reason": "uncommitted or late signal retained; no overwrite", "signal": None}
        if open_index is None:
            missed = [index for index, slot in enumerate(slots)
                      if saved[index] is None and now >= slot + deadline]
            closed = now >= slots[-1] + deadline
            return {"outcome": "WINDOW_CLOSED" if closed else "WAITING_OUTSIDE_WINDOW",
                    "slot_index": None, "reason": None, "signal": None, "missed": missed}
        slot_ms = slots[open_index]
        if len(_attempts(root, open_index)) >= MAX_ATTEMPTS_PER_SLOT:
            return {"outcome": "ATTEMPTS_EXHAUSTED", "slot_index": open_index,
                    "reason": "attempt budget for this slot is exhausted", "signal": None}
        attempt = len(_attempts(root, open_index)) + 1
        _write_new(root / f"claim-{open_index}-{attempt}.json", {
            "slot_ms": slot_ms, "attempt": attempt, "started_ms": now, "plan_hash": plan["plan_hash"],
        })
        if identity() != plan["collector_identity"]:
            return _persist(root, open_index, slot_ms, "IDENTITY_DRIFT",
                            "collector identity changed before the request", None, clock)
        try:
            receipt = fetch(rf3.fixed_url(slot_ms), clock)
        except Exception as exc:
            return _persist(root, open_index, slot_ms, "FETCH_FAILED", type(exc).__name__, None, clock)
        if identity() != plan["collector_identity"]:
            return _persist(root, open_index, slot_ms, "IDENTITY_DRIFT",
                            "collector identity changed after the request", receipt, clock)
        if not isinstance(receipt, dict) or receipt.get("ok") is not True:
            return _persist(root, open_index, slot_ms, "FETCH_FAILED",
                            (receipt or {}).get("category") if isinstance(receipt, dict)
                            else "MALFORMED_RECEIPT", receipt, clock)
        try:
            signal = rf3.issue_target(plan, receipt, clock())
        except rf3.Rf3Rejection as exc:
            status = "LATE" if exc.code == "LATE" else "REJECTED"
            return _persist(root, open_index, slot_ms, status, exc.code, receipt, clock)
        if identity() != plan["collector_identity"]:
            return _persist(root, open_index, slot_ms, "IDENTITY_DRIFT",
                            "collector identity changed before publication", receipt, clock)
        return _persist(root, open_index, slot_ms, "PUBLISHED", None, receipt, clock, signal)
    finally:
        os.close(handle)


def status(state_root, *, clock=now_ms, repo_root=ROOT):
    """Read-only report of slots, gaps and remaining attempts; no PnL is computed."""
    root = _resolve_root(state_root, must_exist=True)
    plan = _state(root)
    now = clock()
    deadline = plan["deadline_offset_ms"]
    current = collector_identity(repo_root)
    slots, published, gaps, pending = [], [], [], []
    for index, slot_ms in enumerate(plan["slots"]):
        saved = _saved_signal(root, index)
        attempts = len(_attempts(root, index))
        missed = saved is None and now >= slot_ms + deadline
        if saved is not None:
            published.append(index)
        elif missed:
            gaps.append(index)
        else:
            pending.append(index)
        slots.append({"index": index, "slot_ms": slot_ms, "published": saved is not None,
                      "slot_utc": datetime.fromtimestamp(slot_ms / 1000, timezone.utc).isoformat(),
                      "attempts": attempts, "missed": missed,
                      "remaining_attempts": max(0, MAX_ATTEMPTS_PER_SLOT - attempts)})
    return {"schema": STATUS_SCHEMA, "state_root": str(root), "plan_hash": plan["plan_hash"],
            "collector_identity": plan["collector_identity"], "identity_current": current,
            "identity_matches": current == plan["collector_identity"], "observed_ms": now,
            "published": published, "gaps": gaps, "pending": pending, "slots": slots,
            "authority": False, "order_submission": False, "pnl": None}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="RF3 fixed 90-close daily target observer (signal only, no order, no PnL)")
    parser.add_argument("--state-root", required=True, help="absolute RF3 state directory")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--register", action="store_true", help="freeze ancestry and plan (offline)")
    mode.add_argument("--once", action="store_true", help="perform at most one bounded step")
    mode.add_argument("--status", action="store_true", help="read-only slot/gap report")
    parser.add_argument("--research-result", default=None,
                        help="absolute RF2 return_sign90 technical case for --register")
    args = parser.parse_args(argv)
    if args.register and not args.research_result:
        parser.error("--register requires --research-result ABS")
    if args.research_result and not args.register:
        parser.error("--research-result is only valid together with --register")
    try:
        if args.register:
            result = register(args.state_root, args.research_result)
        elif args.once:
            result = run_once(args.state_root)
        else:
            result = status(args.state_root)
    except (PathRejection, RegistrationRejection, rf3.Rf3Rejection) as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}, allow_nan=False))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 1 if result.get("outcome") in FAILURE_OUTCOMES else 0


if __name__ == "__main__":
    raise SystemExit(main())
