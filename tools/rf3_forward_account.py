"""RF3 offline forward-account evaluation: published daily targets -> the V2 ledger.

Offline, single-shot CLI for
docs/engineering/plans/2026-09-20-rf3-daily-forward.md (part two).  It consumes
exactly two frozen inputs and nothing else:

* the RF3 observer state directory (``plan.json``, ``ancestry.json`` and the
  fourteen published ``signal-<i>.json`` / ``attempt-<i>-<a>.json`` /
  ``publication-<i>.json`` records), and
* one completed public market captured by
  ``tools.profitability_forward_market.capture_market`` (``request-plan.json``
  plus the numbered ``<i>.json`` receipts, in exactly that on-disk format).

No network, no credentials, no broker/risk-gate path, no order submission and no
second ledger: the market is rebuilt by the existing ``market_from_receipts`` and
every case is replayed by ``services.research.rf3_forward_account.evaluate`` on
the unique canonical V2 engine.  Every input file and every scoped source file
(plus the ARM64 dependency lock and the interpreter/numpy versions) is content
hashed before anything is loaded and re-checked after loading, around every case
and around the final summary; drift or any error leaves a ``failure.json`` marker
and a nonzero exit code, never a summary.

Registration is committed before the first replay: ``registration.json`` retains
the full observer plan, all fourteen bundles, the bound identities and the nine
case definitions, and the nine trials are appended PLANNED before any RUNNING.
Output is new-only and never overwritten or resumed.

Authority.  Every number is a simulated technical replay of intended targets.
``authority``, ``economic_eligible`` and ``order_submission`` are always false.
The original collector identity is retained from registration and the current
evaluator identity is bound separately, so an unrelated later edit cannot
invalidate the window and a matching hash cannot promote the result either.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np

from services.research import rf3_daily_forward as rf3
from services.research import rf3_forward_account as account
from services.research.trial_ledger import TrialLedger, TrialLedgerError, TrialRecord
from tools.profitability_forward_market import market_from_receipts

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "QUANTOS_RF3_FORWARD_ACCOUNT_EVALUATION_V1"
REGISTRATION_SCHEMA = "QUANTOS_RF3_FORWARD_ACCOUNT_REGISTRATION_V1"
CASE_SCHEMA = "QUANTOS_RF3_FORWARD_ACCOUNT_CASE_V1"
FAILURE_SCHEMA = "QUANTOS_RF3_FORWARD_ACCOUNT_FAILURE_V1"
STATUS = "TECHNICAL_VALIDATION_ONLY"
POLICY = "RF3_FORWARD_ACCOUNT_TECHNICAL_ONLY_V1"
QUOTE = "USDC"
DAY = rf3.DAY_MS
MINUTE = 60_000
CHUNK_MINUTES = 1000
MAX_RECEIPT_ROWS = 1000
MAX_MARKET_FILE_BYTES = 16 * 1024 * 1024
MAX_STATE_FILE_BYTES = 1 * 1024 * 1024
MAX_CODE_FILE_BYTES = 4 * 1024 * 1024
MAX_INPUT_FILES = 256
FORBIDDEN_PREFIXES = ("/srv", "/opt")
LOCKFILE = "requirements-ci-linux-aarch64.lock"
# Scoped evaluator identity: the new tool, the pure account, the RF3 observer pair,
# the RF2 signal, the canonical ledger/attribution/trial modules, the public market
# helpers and the containing package __init__ files.  Never a full-repo scan.
SCOPE_FILES = (
    "tools/rf3_forward_account.py",
    "services/research/rf3_forward_account.py",
    "services/research/rf3_daily_forward.py",
    "tools/rf3_daily_forward.py",
    "services/research/rf2_bounded_strategy.py",
    "services/research/perpetual_alpha.py",
    "services/execution/perpetual_ledger_v2.py",
    "services/research/profitability_attribution.py",
    "services/research/trial_ledger.py",
    "tools/profitability_forward_market.py",
    "tools/profitability_market_capture.py",
    "tools/profitability_study.py",
    "tools/profitability_public_http.py",
    "services/__init__.py",
    "services/research/__init__.py",
    "services/execution/__init__.py",
    "tools/__init__.py",
)
LIMITATIONS = list(account.LIMITATIONS) + ["FOURTEEN_DAYS_IS_NOT_STATISTICAL_STABILITY_EVIDENCE"]


class Rejection(ValueError):
    """A rejected path, artifact, market or lifecycle; nothing was overwritten."""

    def __init__(self, code, detail):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class DriftError(Rejection):
    """A frozen input, scoped source file or evaluator version changed mid-run."""

    def __init__(self, label):
        super().__init__("INPUT_DRIFT", f"frozen input or evaluator identity changed at {label}")


def now_ms():
    return time.time_ns() // 1_000_000


def _reject(code, detail):
    raise Rejection(code, detail)


def evaluator_versions():
    """The interpreter and array library the evaluator actually runs on."""
    return {"python": sys.version, "numpy": np.__version__}


def _digest(payload):
    return rf3.digest(payload)


def _reject_symlink_chain(path, label):
    current = path
    while True:
        if current.is_symlink():
            _reject("SYMLINK", f"{label} path may not be a symlink: {current}")
        parent = current.parent
        if parent == current:
            return
        current = parent


def _resolved(value, label, *, must_exist):
    try:
        path = value if isinstance(value, Path) else Path(value)
    except (TypeError, ValueError):
        _reject("PATH_INVALID", f"{label} must be an absolute filesystem path")
    if not path.is_absolute():
        _reject("PATH_INVALID", f"{label} must be an absolute path")
    _reject_symlink_chain(path, label)
    resolved = path.resolve()
    for forbidden in FORBIDDEN_PREFIXES:
        prefix = Path(forbidden)
        if resolved == prefix or prefix in resolved.parents:
            _reject("PATH_FORBIDDEN", f"{label} under {forbidden} is forbidden")
    if must_exist and not path.is_dir():
        _reject("PATH_MISSING", f"{label} is not an existing directory: {path}")
    return resolved


def _overlaps(left, right):
    return left == right or left in right.parents or right in left.parents


def resolve_paths(state_root, market_root, output):
    """Reject forbidden roots and any input/output nesting, alias or overlap."""
    state = _resolved(state_root, "--state-root", must_exist=True)
    market = _resolved(market_root, "--market-root", must_exist=True)
    target = _resolved(output, "--output", must_exist=False)
    pairs = (("state-root", state), ("market-root", market), ("output", target))
    for position, (left_name, left) in enumerate(pairs):
        for right_name, right in pairs[position + 1:]:
            if _overlaps(left, right):
                _reject("PATH_OVERLAP",
                        f"{left_name} and {right_name} must not nest, alias or overlap: {left} vs {right}")
    return state, market, target


def _read_bytes(path, limit):
    _reject_symlink_chain(path, "input artifact")
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            _reject("INPUT_TYPE", f"input must be a regular file: {path}")
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError as exc:
        _reject("INPUT_UNREADABLE", f"input artifact is missing or unreadable: {path} ({exc})")
    if len(raw) > limit:
        _reject("INPUT_TOO_LARGE", f"input artifact exceeds {limit} bytes: {path}")
    return raw


def _read_json(path, limit):
    raw = _read_bytes(path, limit)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        _reject("INPUT_UNREADABLE", f"input artifact is not UTF-8 text: {path} ({exc})")
    try:
        return rf3.strict_loads(text)
    except rf3.Rf3Rejection as exc:
        _reject("INPUT_NOT_JSON", f"input artifact is not strict JSON: {path} ({exc.code})")


def _sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash_tree(root):
    """Hash a bounded flat capture directory; never traverse unrelated trees."""
    hashes = {}
    paths = []
    for path in root.iterdir():
        if len(paths) >= MAX_INPUT_FILES:
            _reject("INPUT_TOO_LARGE", "capture directory has too many entries")
        paths.append(path)
    for path in sorted(paths):
        _reject_symlink_chain(path, "input artifact")
        if not path.is_file():
            _reject("INPUT_TYPE", f"capture must be flat regular files: {path}")
        hashes[path.relative_to(root).as_posix()] = _sha256(_read_bytes(path, MAX_MARKET_FILE_BYTES))
    return hashes


def scoped_code_hashes(repo_root=ROOT):
    """Content hashes of the scoped evaluator sources and the ARM64 dependency lock."""
    root = Path(repo_root)
    hashes = {}
    for relative in SCOPE_FILES:
        path = root / relative
        if not path.is_file():
            if path.name == "__init__.py":
                continue
            _reject("SCOPE_MISSING", f"scoped source file is missing: {path}")
        hashes[relative] = _sha256(_read_bytes(path, MAX_CODE_FILE_BYTES))
    lock = root / LOCKFILE
    hashes[LOCKFILE] = _sha256(_read_bytes(lock, MAX_CODE_FILE_BYTES))
    return hashes


class Frozen:
    """A frozen content snapshot; every lifecycle step re-checks it in full."""

    def __init__(self, state_root, market_root, repo_root):
        self.state_root = state_root
        self.market_root = market_root
        self.repo_root = repo_root
        self.snapshot = self._capture()

    def _capture(self):
        files = {}
        for prefix, root in (("state", self.state_root), ("market", self.market_root)):
            for name, digest in _hash_tree(root).items():
                files[f"{prefix}/{name}"] = digest
        for name, digest in scoped_code_hashes(self.repo_root).items():
            files[f"code/{name}"] = digest
        return {"files": files, "versions": evaluator_versions()}

    def verify(self, label):
        if self._capture() != self.snapshot:
            raise DriftError(label)

    @property
    def input_files(self):
        return {name: digest for name, digest in self.snapshot["files"].items()
                if name.startswith(("state/", "market/"))}

    @property
    def code_files(self):
        return {name: digest for name, digest in self.snapshot["files"].items()
                if name.startswith("code/")}

    @property
    def input_digest(self):
        return _digest(self.input_files)

    @property
    def source_digest(self):
        return _digest({"files": self.code_files, "versions": self.snapshot["versions"]})


def expected_requests(start, end, quote=QUOTE):
    """The exact request plan ``capture_market`` builds for this window."""
    requests = []
    for kind, low in (("klines", start // DAY * DAY - DAY), ("markPriceKlines", start)):
        for at in range(low, end + MINUTE, CHUNK_MINUTES * MINUTE):
            last = min(end, at + (CHUNK_MINUTES - 1) * MINUTE)
            requests.append([kind, f"https://fapi.binance.com/fapi/v1/{kind}?symbol=BTC{quote}"
                                   f"&interval=1m&startTime={at}&endTime={last + 59999}&limit=1000"])
    requests.append(["fundingRate", f"https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTC{quote}"
                                     f"&startTime={start - DAY}&endTime={end - 1}&limit=1000"])
    return requests


def _load_plan(state_root):
    plan = _read_json(state_root / "plan.json", MAX_STATE_FILE_BYTES)
    try:
        rf3.verify_plan(plan)
    except rf3.Rf3Rejection as exc:
        _reject(exc.code, f"observer plan: {exc.detail}")
    ancestry = _read_json(state_root / "ancestry.json", MAX_STATE_FILE_BYTES)
    if not isinstance(ancestry, dict) or ancestry.get("sha256") != plan["ancestry_hash"]:
        _reject("ANCESTRY_MISMATCH", "observer ancestry record does not seal the registered plan")
    return plan


def _load_bundles(state_root, plan):
    """The fourteen published signal/attempt/publication records, strict JSON only."""
    bundles = []
    for index in range(rf3.SLOT_COUNT):
        signal = _read_json(state_root / f"signal-{index}.json", MAX_STATE_FILE_BYTES)
        if not isinstance(signal, dict):
            _reject("SIGNAL_INVALID", f"slot {index} saved signal must be a JSON object")
        attempt = signal.get("attempt")
        if type(attempt) is not int or not 1 <= attempt <= account.ATTEMPT_LIMIT:
            _reject("SIGNAL_INVALID", f"slot {index} saved signal must carry its attempt index")
        bundles.append({
            "signal": signal,
            "attempt": _read_json(state_root / f"attempt-{index}-{attempt}.json", MAX_STATE_FILE_BYTES),
            "publication": _read_json(state_root / f"publication-{index}.json", MAX_STATE_FILE_BYTES),
        })
    return bundles


def _verify_capture_seal(document):
    """Verify the original capture writer's JSON encoding, not a new seal format."""
    if not isinstance(document, dict):
        _reject("CAPTURE_SEAL", "capture must be a sealed JSON object")
    body = {key: value for key, value in document.items() if key != "result_hash"}
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, allow_nan=False)
    if document.get("result_hash") != _sha256(encoded.encode("utf-8")):
        _reject("CAPTURE_SEAL", "original capture content hash does not match")


def _load_market(market_root, start, end, *, evaluated_ms):
    """Validate and rebuild the captured public market; never a second ledger."""
    request_plan = _read_json(market_root / "request-plan.json", MAX_MARKET_FILE_BYTES)
    _verify_capture_seal(request_plan)
    if not isinstance(request_plan, dict):
        _reject("REQUEST_PLAN", "request-plan.json must be the capture's JSON object")
    if type(request_plan.get("start_ms")) is not int or request_plan["start_ms"] != start:
        _reject("REQUEST_PLAN", "request-plan start must equal the plan's rebalance window start")
    if type(request_plan.get("end_ms")) is not int or request_plan["end_ms"] != end:
        _reject("REQUEST_PLAN", "request-plan end must equal the plan's rebalance window end")
    expected = expected_requests(start, end)
    if request_plan.get("requests") != expected:
        _reject("REQUEST_PLAN", "request-plan requests must be the exact deterministic capture list")
    present = {path.name for path in market_root.iterdir()}
    wanted = {"request-plan.json"} | {f"{index}.json" for index in range(len(expected))}
    if present != wanted:
        _reject("MARKET_FILES",
                f"market must contain exactly 0..{len(expected) - 1}.json and request-plan.json; "
                f"extra={sorted(present - wanted)} missing={sorted(wanted - present)}")
    receipts = []
    for index, (kind, url) in enumerate(expected):
        receipt = _read_json(market_root / f"{index}.json", MAX_MARKET_FILE_BYTES)
        _verify_capture_seal(receipt)
        if not isinstance(receipt, dict):
            _reject("RECEIPT_INVALID", f"receipt {index} must be the capture's JSON object")
        if receipt.get("kind") != kind or receipt.get("url") != url:
            _reject("RECEIPT_BINDING", f"receipt {index} kind/url do not match its request")
        rows = receipt.get("payload")
        if not isinstance(rows, list) or len(rows) > MAX_RECEIPT_ROWS:
            _reject("RECEIPT_PAYLOAD", f"receipt {index} payload must be a bounded public list")
        query = parse_qs(urlsplit(url).query)
        lower, upper = int(query["startTime"][0]), int(query["endTime"][0])
        if kind == "fundingRate":
            if any(not isinstance(row, dict) or type(row.get("fundingTime")) is not int
                   or not lower <= row["fundingTime"] <= upper for row in rows):
                _reject("RECEIPT_WINDOW", f"funding receipt {index} exceeds its request")
        else:
            if (any(not isinstance(row, list) or not row or type(row[0]) is not int for row in rows)
                    or [row[0] for row in rows] != list(range(lower, upper+1, MINUTE))):
                _reject("RECEIPT_WINDOW", f"minute receipt {index} differs from its request")
        received = receipt.get("received_ms")
        if type(received) is not int:
            _reject("RECEIPT_CLOCK", f"receipt {index} received_ms must be an integer ms clock")
        if not end + MINUTE < received <= evaluated_ms:
            _reject("RECEIPT_CLOCK",
                    f"receipt {index} must be received after the window's closing minute and "
                    f"no later than the evaluation clock")
        receipts.append(receipt)
    try:
        market = market_from_receipts(receipts, start=start, end=end, quote=QUOTE)
    except (ValueError, KeyError, TypeError) as exc:
        _reject("MARKET_INVALID", f"captured market is incomplete or inconsistent: {exc}")
    return market


def _case_definitions(identity):
    """The nine fixed cases, each with an identity binding input, source and plan."""
    evaluation = _digest({"schema": POLICY, "plan_hash": identity["plan_hash"],
                          "input_digest": identity["input_digest"],
                          "source_digest": identity["source_digest"]})
    definitions = []
    for name in account.ACCOUNTS:
        for slippage in account.SLIPPAGE_BPS:
            case = _digest({"account": name, "slippage_bps": slippage, "fee_bps": int(account.FEE_BPS)})
            definitions.append({
                "index": len(definitions), "account": name, "slippage_bps": slippage,
                "fee_bps": int(account.FEE_BPS),
                "trial_id": _digest({"schema": "QUANTOS_RF3_ACCOUNT_TRIAL_V1",
                                     "evaluation": evaluation, "case": case}),
                "alpha_id": case,
                "family_id": _digest({"schema": "QUANTOS_RF3_ACCOUNT_FAMILY_V1", "evaluation": evaluation}),
                "exact_definition_id": _digest({"schema": "QUANTOS_RF3_ACCOUNT_DEFINITION_V1",
                                                "plan_hash": identity["plan_hash"],
                                                "input_digest": identity["input_digest"], "case": case}),
                "validation_policy_digest": _digest({"schema": POLICY,
                                                     "source_digest": identity["source_digest"],
                                                     "plan_hash": identity["plan_hash"]}),
            })
    return definitions


def _trial_record(definition, status, event_index, at):
    return TrialRecord(definition["trial_id"], definition["alpha_id"], definition["family_id"],
                       definition["exact_definition_id"], definition["validation_policy_digest"],
                       status, event_index, at)


def _append_trial(ledger, definition, status, at):
    try:
        event_index = len(ledger.load(definition["trial_id"]).events)
    except TrialLedgerError:
        event_index = 0
    ledger.append(_trial_record(definition, status, event_index, at))


def _write_new(path, payload):
    """Exclusive, fsynced write; an existing path is never overwritten."""
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, allow_nan=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        _reject("OUTPUT_EXISTS", f"refusing to overwrite an existing artifact: {path}")


class CaseObserver:
    """Lifecycle bridge: register RUNNING, verify drift, persist, register PASSED."""

    def __init__(self, ledger, frozen, output, definitions, at):
        self._ledger = ledger
        self._frozen = frozen
        self._output = output
        self._definitions = definitions
        self._by_case = {(row["account"], row["slippage_bps"]): row for row in definitions}
        self._at = at
        self.current = None
        self.artifacts = []

    def __call__(self, event):
        if not isinstance(event, dict) or event.get("state") not in ("START", "COMPLETED"):
            _reject("EVENT_INVALID", f"unknown lifecycle event from the account engine: {event!r}")
        definition = self._by_case.get((event.get("account"), event.get("slippage_bps")))
        if definition is None:
            _reject("EVENT_INVALID", "lifecycle event does not belong to a registered case")
        if event["state"] == "START":
            _append_trial(self._ledger, definition, "RUNNING", self._at)
            self.current = definition
            self._frozen.verify(f"case {definition['index']} before replay")
            return
        case = event.get("case")
        if not isinstance(case, dict) or case.get("account") != definition["account"] \
                or case.get("slippage_bps") != definition["slippage_bps"]:
            _reject("EVENT_INVALID", "completed case does not match its registered definition")
        self._frozen.verify(f"case {definition['index']} after replay")
        artifact = {"schema": CASE_SCHEMA, "index": definition["index"],
                    "account": definition["account"], "slippage_bps": definition["slippage_bps"],
                    "fee_bps": definition["fee_bps"], "trial_id": definition["trial_id"],
                    "status": "PASSED", "authority": False, "economic_eligible": False,
                    "case": case}
        _write_new(self._output / "cases" / f"{definition['trial_id']}.json", artifact)
        _append_trial(self._ledger, definition, "PASSED", self._at)
        self.artifacts.append(artifact)
        self.current = None


def build_registration(plan, bundles, definitions, identity, market_files, evaluated_ms):
    return {"schema": REGISTRATION_SCHEMA, "status": STATUS, "policy": POLICY,
            "evaluated_ms": evaluated_ms, "plan": plan, "bundles": bundles,
            "identity": identity, "cases": definitions, "case_count": len(definitions),
            "market_receipts": market_files,
            "state_root": identity["state_root"], "market_root": identity["market_root"],
            "window": {"start_ms": identity["start_ms"], "end_ms": identity["end_ms"]},
            "authority": False, "economic_eligible": False, "order_submission": False,
            "pnl": None, "limitations": LIMITATIONS}


def build_summary(plan, result, artifacts, definitions, identity, evaluated_ms, window):
    results = []
    for artifact in artifacts:
        case = artifact["case"]
        attribution = case["attribution"]
        receipt = case["receipt"]
        results.append({
            "index": artifact["index"], "account": artifact["account"],
            "slippage_bps": artifact["slippage_bps"], "fee_bps": artifact["fee_bps"],
            "trial_id": artifact["trial_id"], "status": artifact["status"],
            "attribution_status": attribution["status"], "net_pnl": attribution["net_pnl"],
            "gross_reference_pnl": attribution["gross_reference_pnl"],
            "embedded_slippage": attribution["embedded_slippage"], "fees": attribution["fees"],
            "funding": attribution["funding"], "diagnosis": attribution["diagnosis"],
            "fill_count": attribution["fill_count"], "halted_at": receipt["halted_at"],
            "ending_equity": receipt["reconciliation"]["ending_equity"],
            "minute_max_drawdown": receipt["minute_max_drawdown"],
        })
    return {"schema": SCHEMA, "status": STATUS, "policy": POLICY,
            "plan_hash": plan["plan_hash"], "artifacts_digest": result["artifacts_digest"],
            "window": window, "evaluated_ms": evaluated_ms, "results": results,
            "case_count": len(results), "expected_case_count": len(definitions),
            "identity": identity, "authority": False, "economic_eligible": False,
            "order_submission": False, "actual_venue_costs": None, "pnl": None,
            "forward_profit_claim": False, "limitations": LIMITATIONS}


def _failure_payload(exc, identity, evaluated_ms):
    return {"schema": FAILURE_SCHEMA, "status": "FAILED", "reason": type(exc).__name__,
            "code": getattr(exc, "code", None), "detail": str(exc),
            "evaluated_ms": evaluated_ms, "identity": identity, "summary_written": False,
            "authority": False, "economic_eligible": False, "order_submission": False,
            "pnl": None, "limitations": LIMITATIONS}


def _finalize_failure(ledger, definitions, output, identity, evaluated_ms, exc):
    """Retain completed cases, fail the running one, cancel the rest, never summarise."""
    at = evaluated_ms if type(evaluated_ms) is int else 0
    try:
        if ledger is not None:
            for definition in definitions:
                try:
                    status = ledger.load(definition["trial_id"]).status
                except TrialLedgerError:
                    continue
                if status == "RUNNING":
                    _append_trial(ledger, definition, "FAILED", at)
                elif status == "PLANNED":
                    _append_trial(ledger, definition, "CANCELLED", at)
        if output is not None and not (output / "failure.json").exists():
            _write_new(output / "failure.json", _failure_payload(exc, identity, evaluated_ms))
    except Exception:
        # A retained failure marker must never mask the original rejection.
        pass


def run(state_root, market_root, output, *, clock=now_ms, repo_root=ROOT):
    """Evaluate the nine fixed cases offline; new output only, no resume or overwrite."""
    state, market_dir, target = resolve_paths(state_root, market_root, output)
    if not target.parent.is_dir():
        _reject("OUTPUT_UNWRITABLE", f"output parent directory does not exist: {target.parent}")
    try:
        target.mkdir()
    except FileExistsError as exc:
        _reject("OUTPUT_EXISTS", f"output directory must not already exist (no overwrite): {target}")
    except OSError as exc:
        _reject("OUTPUT_UNWRITABLE", f"output directory cannot be created: {target} ({exc})")
    (target / "cases").mkdir()
    ledger = None
    definitions = []
    identity = {"plan_hash": None, "input_digest": None, "source_digest": None}
    evaluated_ms = None
    try:
        evaluated_ms = clock()
        if type(evaluated_ms) is not int:
            _reject("CLOCK_INVALID", "the evaluation clock must be an integer ms clock")
        frozen = Frozen(state, market_dir, repo_root)
        plan = _load_plan(state)
        start, end = account.account_window(plan)
        market = _load_market(market_dir, start, end, evaluated_ms=evaluated_ms)
        bundles = _load_bundles(state, plan)
        frozen.verify("after loading the frozen inputs")
        identity = {"plan_hash": plan["plan_hash"], "collector_identity": plan["collector_identity"],
                    "collector_identity_policy": "ORIGINAL_REGISTRATION_NOT_RECOMPUTED",
                    "evaluator_identity": frozen.source_digest, "input_digest": frozen.input_digest,
                    "source_digest": frozen.source_digest, "state_root": str(state),
                    "market_root": str(market_dir), "output_root": str(target),
                    "start_ms": start, "end_ms": end,
                    "evaluator_versions": frozen.snapshot["versions"],
                    "input_files": frozen.input_files, "source_files": frozen.code_files}
        definitions = _case_definitions(identity)
        ledger = TrialLedger(str(target / "trials.db"), create=True)
        for definition in definitions:
            _append_trial(ledger, definition, "PLANNED", evaluated_ms)
        registration = build_registration(plan, bundles, definitions, identity,
                                          {name: digest for name, digest in frozen.input_files.items()
                                           if name.startswith("market/")}, evaluated_ms)
        _write_new(target / "registration.json", registration)
        observer = CaseObserver(ledger, frozen, target, definitions, evaluated_ms)
        result = account.evaluate(plan, bundles, market, evaluated_ms=evaluated_ms,
                                  case_observer=observer)
        frozen.verify("before the final summary")
        window = {"start_ms": result["start_ms"], "end_ms": result["end_ms"],
                  "mature_at_ms": result["mature_at_ms"], "slot_count": result["slot_count"]}
        summary = build_summary(plan, result, observer.artifacts, definitions, identity,
                                evaluated_ms, window)
        if summary["case_count"] != len(definitions) or summary["case_count"] != len(account.ACCOUNTS) * len(account.SLIPPAGE_BPS):
            _reject("CASES_INCOMPLETE", "all nine fixed cases must complete before a summary is written")
        frozen.verify("after the final summary")
        ledger.verify()
        _write_new(target / "summary.json", summary)
        return summary
    except BaseException as exc:
        _finalize_failure(ledger, definitions, target, identity, evaluated_ms, exc)
        raise
    finally:
        if ledger is not None:
            ledger.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="RF3 offline forward account: published daily targets on the unique V2 ledger "
                    "(technical validation only, no order, no PnL authority)")
    parser.add_argument("--state-root", required=True, help="absolute RF3 observer state directory")
    parser.add_argument("--market-root", required=True,
                        help="absolute captured public market directory (capture_market format)")
    parser.add_argument("--output", required=True, help="new absolute evaluation output directory")
    args = parser.parse_args(argv)
    try:
        summary = run(args.state_root, args.market_root, args.output)
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "code": getattr(exc, "code", None),
                          "detail": str(exc)}, allow_nan=False, sort_keys=True))
        return 2
    print(json.dumps({"schema": summary["schema"], "status": summary["status"],
                      "case_count": summary["case_count"], "output": str(args.output),
                      "authority": False, "economic_eligible": False}, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
