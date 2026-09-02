from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.killswitch import KillSwitchState
from services.console.api import mexc_views, sqlite_snapshot
from services.data.market_provider import ingest_provider_rows
from services.data.persistent_store import SqlitePITStore
from services.execution.dry_run_store import DryRunRun, DryRunStore
from services.risk.pre_trade.kill_switch_store import KillSwitchStateStore


_DAY = 86_400_000
_T0 = 1_700_000_000_000 // _DAY * _DAY


def _authority_state(path: Path) -> dict[str, tuple[bytes, int, int]]:
    result = {}
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            result[candidate.name] = (
                candidate.read_bytes(),
                candidate.stat().st_mtime_ns,
                candidate.stat().st_size,
            )
    return result


def _configure(monkeypatch: pytest.MonkeyPatch, state: Path) -> None:
    monkeypatch.setattr(mexc_views, "_STATE_ROOT", state)
    monkeypatch.setattr(
        mexc_views, "_MEXC_KLINES_JSON", str(state / "data/cache/mexc_btc_klines.json")
    )
    monkeypatch.setattr(mexc_views, "_MEXC_DB", str(state / "data/cache/crypto_mexc.db"))
    monkeypatch.setattr(mexc_views, "_DRY_RUN_DB", str(state / "data/cache/dry_run.db"))
    monkeypatch.setattr(
        mexc_views,
        "_LIVE_KILL_SWITCH_STATE",
        str(state / "data/cache/live_kill_switch.json"),
    )
    monkeypatch.setattr(
        mexc_views,
        "_MEXC_SMOKE_EVIDENCE",
        str(state / "evidence/mexc_mainnet_smoke.json"),
    )


def _market_row(index: int) -> dict:
    timestamp = _T0 + index * _DAY
    return {
        "instrument_id": "BTC",
        "event_time": timestamp,
        "knowledge_time": timestamp,
        "open": 100.0 + index,
        "high": 102.0 + index,
        "low": 99.0 + index,
        "close": 101.0 + index,
        "volume": 10.0,
    }


def _dry_run(index: int) -> DryRunRun:
    return DryRunRun(
        run_id=f"mexc-{index}",
        strategy_id="btc_spot_trend",
        timestamp=_T0 + index,
        n_intents=1,
        n_approved=1,
        n_rejected=0,
        rejected_reasons=[],
        pnl={"total_return": 0.1, "sharpe": 1.2},
        audit_root="a" * 64,
        detail={"data_source": "MEXC_PIT", "equity_curve": []},
    )


def test_market_and_strategy_read_committed_live_wal_without_source_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    cache = state / "data/cache"
    cache.mkdir(parents=True)
    _configure(monkeypatch, state)

    market_path = cache / "crypto_mexc.db"
    market = SqlitePITStore(str(market_path), create=True)
    market._conn.execute("PRAGMA journal_mode=WAL")
    market._conn.execute("PRAGMA wal_autocheckpoint=0")
    ingest_provider_rows(
        [_market_row(0), _market_row(1)],
        market,
        dataset_id="mexc-BTC",
        now=_T0 + 2 * _DAY,
        time_unit="ms",
        strict=False,
    )

    dry_path = cache / "dry_run.db"
    dry = DryRunStore(str(dry_path), create=True)
    dry._conn.execute("PRAGMA wal_autocheckpoint=0")
    legacy = _dry_run(1)
    legacy_record = legacy.as_record()
    # Reproduce a retained pre-authority database row without using the current
    # authoritative writer boundary: the console must treat this as read-only,
    # invalidated history rather than silently upgrading it to evidence.
    dry._conn.execute(
        "INSERT INTO dry_run(run_id,strategy_id,timestamp,n_intents,n_approved,"
        "n_rejected,rejected_reasons,pnl,audit_root,detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            legacy_record["run_id"],
            legacy_record["strategy_id"],
            legacy_record["timestamp"],
            legacy_record["n_intents"],
            legacy_record["n_approved"],
            legacy_record["n_rejected"],
            json.dumps(legacy_record["rejected_reasons"]),
            json.dumps(legacy_record["pnl"]),
            legacy_record["audit_root"],
            json.dumps(legacy_record["detail"]),
        ),
    )
    dry._conn.commit()
    try:
        assert Path(f"{market_path}-wal").stat().st_size > 0
        assert Path(f"{dry_path}-wal").stat().st_size > 0
        before = {
            "market": _authority_state(market_path),
            "dry": _authority_state(dry_path),
        }

        market_payload = mexc_views._mexc_market_payload()
        strategy_payload = mexc_views._mexc_strategy_payload()

        assert market_payload["snapshot_status"] == "REAL"
        assert market_payload["latest_price"] == 102.0
        assert strategy_payload["snapshot_status"] == "REAL"
        assert strategy_payload["retained_invalidated_run_count"] == 1
        assert strategy_payload["n_runs"] == 0
        assert strategy_payload["authoritative"] is False
        assert (
            strategy_payload["invalidation_code"]
            == "INVALIDATED_PERP_LEDGER_V1"
        )
        assert before == {
            "market": _authority_state(market_path),
            "dry": _authority_state(dry_path),
        }
    finally:
        market._conn.close()
        dry.close()


def test_market_payload_uses_one_sqlite_cut_for_summary_and_candles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    cache = state / "data/cache"
    cache.mkdir(parents=True)
    _configure(monkeypatch, state)
    market_path = cache / "crypto_mexc.db"
    market = SqlitePITStore(str(market_path), create=True)
    ingest_provider_rows(
        [_market_row(0), _market_row(1)],
        market,
        dataset_id="mexc-BTC",
        now=_T0 + 2 * _DAY,
        time_unit="ms",
        strict=False,
    )
    market._conn.close()
    real_snapshot = mexc_views.stable_sqlite_snapshot
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_snapshot(*args, **kwargs)

    monkeypatch.setattr(mexc_views, "stable_sqlite_snapshot", counted)
    payload = mexc_views._mexc_market_payload()

    assert calls == 1
    assert payload["latest_price"] == payload["bars"][-1]["close"]
    assert payload["coverage"]["n_rows"] == len(payload["bars"])


def test_invalid_or_symlink_sqlite_is_unavailable_and_never_writes_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    cache = state / "data/cache"
    cache.mkdir(parents=True)
    _configure(monkeypatch, state)
    corrupt = cache / "crypto_mexc.db"
    corrupt.write_bytes(b"not sqlite")
    before = _authority_state(corrupt)
    payload = mexc_views._mexc_market_payload()
    assert payload["snapshot_status"] == "UNAVAILABLE"
    assert payload["db"]["status"] == "CORRUPT_OR_UNREADABLE"
    assert _authority_state(corrupt) == before

    target = cache / "real.db"
    target.write_bytes(b"another non-database")
    corrupt.unlink()
    try:
        corrupt.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")
    target_before = target.read_bytes(), target.stat().st_mtime_ns
    payload = mexc_views._mexc_market_payload()
    assert payload["snapshot_status"] == "UNAVAILABLE"
    assert payload["db"]["present"] is True
    assert (target.read_bytes(), target.stat().st_mtime_ns) == target_before


def test_market_snapshot_identity_race_fails_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    cache = state / "data/cache"
    cache.mkdir(parents=True)
    _configure(monkeypatch, state)
    path = cache / "crypto_mexc.db"
    store = SqlitePITStore(str(path), create=True)
    ingest_provider_rows(
        [_market_row(0)],
        store,
        dataset_id="mexc-BTC",
        now=_T0 + _DAY,
        time_unit="ms",
        strict=False,
    )
    store._conn.close()
    original = sqlite_snapshot._read_or_copy

    def always_change(source, destination):
        result = original(source, destination)
        if destination is not None and source == path:
            current = path.stat().st_mtime_ns
            os.utime(path, ns=(current + 10_000_000, current + 10_000_000))
        return result

    monkeypatch.setattr(sqlite_snapshot, "_read_or_copy", always_change)
    payload = mexc_views._mexc_market_payload()
    assert payload["snapshot_status"] == "UNAVAILABLE"
    assert payload["db"]["status"] == "UNAVAILABLE"


def test_sqlite_rejects_parent_symlink_escape_and_parent_identity_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "crypto_mexc.db"
    store = SqlitePITStore(str(target), create=True)
    store._conn.close()
    state.mkdir()
    try:
        (state / "data").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")
    monkeypatch.setattr(mexc_views, "_STATE_ROOT", state)
    escaped = state / "data/crypto_mexc.db"
    assert mexc_views._market_from_pit_db(str(escaped))["status"] == "UNAVAILABLE"

    (state / "data").unlink()
    cache = state / "data/cache"
    cache.mkdir(parents=True)
    path = cache / "crypto_mexc.db"
    store = SqlitePITStore(str(path), create=True)
    store._conn.close()
    real_inspect = mexc_views._inspect_sqlite_components
    calls = 0

    def changed_once(source):
        nonlocal calls
        result = real_inspect(source)
        calls += 1
        if calls == 2:
            current = cache.stat().st_mtime_ns
            os.utime(cache, ns=(current + 10_000_000, current + 10_000_000))
        return result

    monkeypatch.setattr(mexc_views, "_inspect_sqlite_components", changed_once)
    assert mexc_views._market_from_pit_db(str(path))["status"] == "UNAVAILABLE"


def test_sqlite_rejects_mocked_windows_reparse_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    cache = state / "data/cache"
    cache.mkdir(parents=True)
    path = cache / "crypto_mexc.db"
    store = SqlitePITStore(str(path), create=True)
    store._conn.close()
    monkeypatch.setattr(mexc_views, "_STATE_ROOT", state)
    monkeypatch.setattr(
        mexc_views,
        "_is_reparse_point",
        lambda metadata: Path(getattr(metadata, "_test_path", "")) == cache,
    )
    real_lstat = Path.lstat

    class WrappedStat:
        def __init__(self, value, test_path):
            self.__dict__.update(
                {name: getattr(value, name) for name in dir(value) if name.startswith("st_")}
            )
            self._test_path = os.fspath(test_path)

    def marked_lstat(candidate):
        return WrappedStat(real_lstat(candidate), candidate)

    monkeypatch.setattr(Path, "lstat", marked_lstat)
    assert mexc_views._market_from_pit_db(str(path))["status"] == "UNAVAILABLE"


def test_kill_switch_reads_live_wal_and_missing_or_unstable_is_tripped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    cache = state / "data/cache"
    cache.mkdir(parents=True)
    _configure(monkeypatch, state)
    authority = cache / "live_kill_switch.db"
    writer = KillSwitchStateStore(str(authority), create=True)
    writer._conn.execute("PRAGMA wal_autocheckpoint=0")
    tripped = KillSwitchState(
        tripped=True,
        incident_id="mexc-console-test",
        reason="test latch",
        armed_by=Actor(actor_id="risk-sentinel", kind=ActorKind.SYSTEM),
        armed_at=10,
        trip_count=1,
    )
    writer.bootstrap(tripped, recorded_at=10)
    try:
        before = _authority_state(authority)
        loaded = mexc_views._load_mexc_kill_switch_state()
        assert loaded == tripped
        assert _authority_state(authority) == before

        monkeypatch.setattr(mexc_views, "_LIVE_KILL_SWITCH_STATE", str(cache / "absent.json"))
        assert mexc_views._load_mexc_kill_switch_state().tripped is True
    finally:
        writer.close()


def test_kill_switch_invalid_db_blocks_legacy_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    cache = state / "data/cache"
    cache.mkdir(parents=True)
    _configure(monkeypatch, state)
    mirror = cache / "live_kill_switch.json"
    mirror.write_text(KillSwitchState().model_dump_json(), encoding="utf-8")
    authority = cache / "live_kill_switch.db"
    target = cache / "target.db"
    target.write_bytes(b"not sqlite")
    try:
        authority.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")

    loaded = mexc_views._load_mexc_kill_switch_state()

    assert loaded.tripped is True
    assert "authority unavailable" in loaded.reason


def test_kill_switch_db_appearing_during_json_read_blocks_clear_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    cache = state / "data/cache"
    cache.mkdir(parents=True)
    _configure(monkeypatch, state)
    mirror = cache / "live_kill_switch.json"
    mirror.write_text(KillSwitchState().model_dump_json(), encoding="utf-8")
    authority = cache / "live_kill_switch.db"
    real_read = mexc_views.read_json_artifact

    def create_authority_during_read(*args, **kwargs):
        result = real_read(*args, **kwargs)
        writer = KillSwitchStateStore(str(authority), create=True)
        writer.close()
        return result

    monkeypatch.setattr(mexc_views, "read_json_artifact", create_authority_during_read)
    loaded = mexc_views._load_mexc_kill_switch_state()
    assert loaded.tripped is True
    assert "appeared" in loaded.reason


@pytest.mark.parametrize(
    "payload_json",
    (
        '{"open":1,"high":2,"low":0.5,"close":1,"close":2}',
        '{"open":1,"high":2,"low":0.5,"close":NaN}',
        '{"open":1,"high":2,"low":0.5,"close":1e9999}',
        '{"open":1,"high":2,"low":0.5,"close":1,"note":"\\ud800"}',
    ),
)
def test_any_invalid_authoritative_pit_row_invalidates_entire_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload_json: str
) -> None:
    state = tmp_path / "state"
    cache = state / "data/cache"
    cache.mkdir(parents=True)
    _configure(monkeypatch, state)
    path = cache / "crypto_mexc.db"
    conn = __import__("sqlite3").connect(path)
    conn.execute(
        "CREATE TABLE bars(key TEXT,event_time INTEGER,knowledge_time INTEGER,payload_json TEXT,"
        "PRIMARY KEY(key,event_time,knowledge_time))"
    )
    conn.execute("INSERT INTO bars VALUES(?,?,?,?)", ("BTC", _T0, _T0, json.dumps(_market_row(0))))
    conn.execute("INSERT INTO bars VALUES(?,?,?,?)", ("BTC", _T0 + _DAY, _T0 + _DAY, payload_json))
    conn.commit()
    conn.close()

    payload = mexc_views._mexc_market_payload()
    assert payload["snapshot_status"] == "UNAVAILABLE"
    assert payload["db"]["status"] == "CORRUPT_OR_UNREADABLE"
    assert payload["bars"] == []


def test_strict_state_json_and_credential_boundary_are_not_inspected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    cache = state / "data/cache"
    evidence = state / "evidence"
    cache.mkdir(parents=True)
    evidence.mkdir()
    _configure(monkeypatch, state)
    (cache / "mexc_btc_klines.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "fetched_at_ms": _T0,
                "bars": [[_T0, 1, 1, 1, 100], [_T0 + _DAY, 1, 1, 1, 101]],
            }
        ),
        encoding="utf-8",
    )
    (evidence / "mexc_mainnet_smoke.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provenance": "REAL_MEXC_MAINNET_READONLY_SMOKE",
                "ran_at_ms": _T0,
                "ran_at_utc": "2023-11-14T00:00:00Z",
                "checks": [],
                "verdict": "PASS",
                "order_response_shape": "UNVERIFIED_UNTIL_FIRST_FILL",
                "notes": [],
            }
        ),
        encoding="utf-8",
    )

    real_exists = mexc_views.os.path.exists
    real_isfile = mexc_views.os.path.isfile

    def guarded(value):
        lowered = os.fspath(value).lower()
        if "key" in lowered or "mandate" in lowered or "attestation" in lowered:
            raise AssertionError("credential-domain path was inspected")
        return real_exists(value)

    def guarded_isfile(value):
        lowered = os.fspath(value).lower()
        if "key" in lowered or "mandate" in lowered or "attestation" in lowered:
            raise AssertionError("credential-domain path was inspected")
        return real_isfile(value)

    monkeypatch.setattr(mexc_views.os.path, "exists", guarded)
    monkeypatch.setattr(mexc_views.os.path, "isfile", guarded_isfile)
    monkeypatch.setattr(
        mexc_views.os,
        "getenv",
        lambda name, *args: (_ for _ in ()).throw(
            AssertionError(f"environment access forbidden: {name}")
        ),
    )

    payload = mexc_views._mexc_readiness_payload()
    checks = {item["id"]: item for item in payload["checks"]}
    for check_id in ("human_mandate", "validation_attestation", "credential_configured"):
        assert checks[check_id]["status"] == "BLOCKED"
        assert checks[check_id]["reason"] == "CREDENTIAL_BOUNDARY_NOT_INSPECTED"
    assert checks["mainnet_smoke_evidence"]["status"] == "READY"
    assert payload["kill_switch"]["tripped"] is True


def test_readiness_uses_freshest_valid_market_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    (state / "data/cache").mkdir(parents=True)
    _configure(monkeypatch, state)
    now = (_T0 + 20 * _DAY) / 1000
    monkeypatch.setattr(mexc_views.time, "time", lambda: now)
    monkeypatch.setattr(
        mexc_views,
        "_bars_from_klines_json",
        lambda _path: {"fetched_at": now - 10 * 86_400, "bars": []},
    )
    monkeypatch.setattr(
        mexc_views,
        "_bars_from_pit_db",
        lambda _path: {"fetched_at": None, "bars": [{"t": now - 86_400}]},
    )

    payload = mexc_views._mexc_readiness_payload()
    market_check = next(
        item for item in payload["checks"] if item["id"] == "market_data_fresh"
    )

    assert market_check["status"] == "READY"
    assert market_check["reason"] == "数据年龄 24.0h"


def test_expensive_mexc_routes_reuse_short_lived_read_only_snapshot(monkeypatch):
    calls = {"strategy": 0, "readiness": 0}

    def strategy_payload():
        calls["strategy"] += 1
        return {"snapshot_status": "NO_SNAPSHOT", "verdict": "BLOCKED"}

    def readiness_payload():
        calls["readiness"] += 1
        return {"snapshot_status": "NO_SNAPSHOT", "verdict": "BLOCKED"}

    mexc_views._MEXC_STRATEGY_CACHE.clear()
    mexc_views._MEXC_READINESS_CACHE.clear()
    monkeypatch.setattr(mexc_views, "_mexc_strategy_payload", strategy_payload)
    monkeypatch.setattr(mexc_views, "_mexc_readiness_payload", readiness_payload)
    try:
        assert mexc_views.mexc_strategy() == mexc_views.mexc_strategy()
        assert mexc_views.mexc_readiness() == mexc_views.mexc_readiness()
        assert calls == {"strategy": 1, "readiness": 1}
    finally:
        mexc_views._MEXC_STRATEGY_CACHE.clear()
        mexc_views._MEXC_READINESS_CACHE.clear()


def test_no_credential_modules_or_names_are_referenced() -> None:
    source = Path(mexc_views.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any("credential" in name.lower() or name.startswith("live") for name in imports)
    for forbidden in ("MEXC_API_KEY", "mexc_spot.json", "live_mandate_mexc.json"):
        assert forbidden not in source


def test_relative_state_root_import_fails_closed(tmp_path: Path) -> None:
    import subprocess
    import sys

    repository = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "-c", "import services.console.api.mexc_views"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(repository), "QUANTOS_STATE_ROOT": "relative"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode != 0
    assert "QUANTOS_STATE_ROOT must be absolute" in completed.stderr
    assert not (tmp_path / "relative").exists()
