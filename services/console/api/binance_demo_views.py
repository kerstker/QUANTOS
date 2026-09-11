"""Project saved Demo observations without network, credentials or mutations."""
import hashlib
from pathlib import Path
import time

from fastapi import APIRouter

from quantos.contracts.binance_demo import DEMO_REST_BASE, DemoReceipt, MAX_AGE_MS, MAX_RESPONSE_BYTES
from quantos.runtime_paths import runtime_state_root
from services.validation.live_submission_authority import LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE
from services.research.demo_execution_journal import DemoExecutionJournal, replay_report

router = APIRouter(tags=["binance-demo"])


def _receipt_path():
    return runtime_state_root() / "data/cache/binance_demo_readiness.json"


def _execution_journal_path():
    return runtime_state_root() / "data/cache/binance_demo_execution.db"


def build_execution_snapshot(*, path: Path, as_of_ms: int):
    if not path.exists():
        return {"status": "NO_JOURNAL", "report": None}
    try:
        with DemoExecutionJournal(path, read_only=True) as journal:
            report = replay_report(journal)
        if report["observed_at_ms"] is not None and report["observed_at_ms"] > as_of_ms:
            return {"status": "DATA_BLOCKED", "report": None}
        return {"status": "AVAILABLE", "report": report}
    except Exception:
        # A malformed imported diagnostic must not expose raw receipt contents or
        # make the independent connectivity snapshot unavailable.
        return {"status": "CORRUPT_OR_UNREADABLE", "report": None}


def build_snapshot(*, path: Path, as_of_ms: int):
    status, reason, receipt, digest, age = "NO_SNAPSHOT", "RECEIPT_MISSING", None, None, None
    try:
        with path.open("rb") as source:
            raw = source.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("receipt too large")
        candidate = DemoReceipt.model_validate_json(raw)
        if candidate.completed_at_ms > as_of_ms:
            status, reason = "DATA_BLOCKED", "RECEIPT_FROM_FUTURE"
        else:
            receipt = candidate
            digest = hashlib.sha256(raw).hexdigest()
            age = as_of_ms - receipt.observed_at_ms
            if age > MAX_AGE_MS:
                status, reason = "STALE", "REFRESH_OBSERVATION"
            elif receipt.public.status != "READY" or receipt.account.status in ("FAILED", "SKIPPED"):
                status, reason = "DATA_BLOCKED", "PROBE_FAILED"
            else:
                status, reason = "REAL", "OBSERVATION_AVAILABLE"
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, RecursionError):
        status, reason = "CORRUPT_OR_UNREADABLE", "RECEIPT_INVALID_OR_UNREADABLE"
    return {
        "source": "BACKEND_COMPUTED_SNAPSHOT", "read_only": True, "api_version": "0.1.0",
        "data_provenance": "DEMO_CONNECTIVITY_DIAGNOSTIC", "snapshot_id": digest,
        "snapshot_ids": [] if digest is None else [digest], "snapshot_status": status,
        "data_quality": {"status": status, "provenance": "DEMO_CONNECTIVITY_DIAGNOSTIC", "reason": reason},
        "environment": "BINANCE_USDM_DEMO", "rest_base": DEMO_REST_BASE,
        "observed_at_ms": receipt.observed_at_ms if receipt else None,
        "as_of_ms": as_of_ms, "age_ms": age, "max_age_ms": MAX_AGE_MS,
        "public": receipt.public.model_dump() if receipt else None,
        "account": receipt.account.model_dump() if receipt else None,
        "submission_enabled": False, "authoritative": False,
        "execution_reason": LIVE_SUBMISSION_AUTHORITY_UNAVAILABLE,
        "order_stream_status": "NOT_CHECKED", "ledger_reconciliation_status": "NOT_CHECKED",
    }


@router.get("/api/v1/console/binance-demo")
def binance_demo_snapshot():
    as_of_ms = time.time_ns() // 1_000_000
    return build_snapshot(path=_receipt_path(), as_of_ms=as_of_ms) | {
        "execution": build_execution_snapshot(path=_execution_journal_path(), as_of_ms=as_of_ms),
    }
