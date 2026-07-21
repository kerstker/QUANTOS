import hashlib
import json
import sqlite3

from services.data.raw_archive import (archive_json_response,
                                       archive_response,
                                       summarize_archive)
from services.data.admission_store import DataQualityAdmissionStore
from tools.data_quality_admission import (
    build_data_quality_evidence,
    run_data_quality_admission,
    write_data_quality_evidence,
)

_DAY = 86_400_000
_NOW = 21 * _DAY


def _seed(tmp_path, *, funding_gap=False, include_lineage=True):
    price_path = tmp_path / "prices.db"
    conn = sqlite3.connect(price_path)
    conn.execute(
        "CREATE TABLE bars (key TEXT NOT NULL, event_time INTEGER NOT NULL, "
        "knowledge_time INTEGER NOT NULL, payload_json TEXT NOT NULL, "
        "PRIMARY KEY (key, event_time, knowledge_time))"
    )
    for day in range(13, 21):
        ts = day * _DAY
        conn.execute("INSERT INTO bars VALUES (?,?,?,?)", ("BTC", ts, ts, "{}"))
    conn.commit()
    conn.close()

    funding_days = tuple(day for day in range(13, 21) if not funding_gap or day != 19)
    funding_path = tmp_path / "funding.json"
    funding_path.write_text(
        json.dumps({"BTC": {str(day * _DAY): 0.001 for day in funding_days}}),
        encoding="utf-8",
    )

    oi_path = tmp_path / "oi.json"
    record = {
        "oi": 100.0,
        "oi_value": 1000.0,
        "top_ls": 1.1,
        "global_ls": 1.2,
        "taker_ls": 0.9,
    }
    oi_path.write_text(
        json.dumps({
            "_meta": {},
            "data": {
                "BTC": {
                    str((20 - offset) * _DAY): record for offset in range(8)
                }
            },
        }),
        encoding="utf-8",
    )

    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps({"instruments": ["BTC"]}), encoding="utf-8")
    refresh_path = tmp_path / "refresh.json"
    refresh_path.write_text(
        json.dumps({"ran_at": _NOW, "reports": []}), encoding="utf-8"
    )
    raw_path = tmp_path / "raw"
    lineage_path = tmp_path / "lineage.json"
    if include_lineage:
        diagnostics = {
            "status": "ACCEPTED", "input_rows": 1, "accepted_rows": 1,
            "rejected_rows": 0, "duplicate_rows": 0,
        }
        for dataset in ("perp_mark_prices", "oi_metrics"):
            archive_response(
                root=str(raw_path), payload=f"raw-{dataset}".encode("ascii"),
                source_uri=f"https://data.example.test/{dataset}/BTC.bin",
                provider="example-public-data", dataset_id=dataset,
                instrument_id="BTC", observed_at_ms=_NOW,
                representation="HTTP_BODY_BYTES",
                parse_diagnostics=diagnostics,
            )
        for dataset in ("funding", "spot_klines"):
            archive_json_response(
                root=str(raw_path), payload={"dataset": dataset},
                source_uri=f"https://api.example.test/{dataset}?symbol=BTCUSDT",
                provider="example-public-data", dataset_id=dataset,
                instrument_id="BTC", observed_at_ms=_NOW,
                parse_diagnostics=diagnostics,
            )
        def artifact(path):
            payload = path.read_bytes()
            return {
                "path": str(path).replace("\\", "/"),
                "size_bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }

        source = {
            "attempted_records": 8,
            "matched_records": 8,
            "failures": [],
            "failed_tickers": [],
            "passed": True,
        }
        lineage_path.write_text(json.dumps({
            "schema_version": 1,
            "readiness": "L2_DRY_RUN_ONLY",
            "purpose": "RECENT_FORWARD_RAW_LINEAGE_BACKFILL",
            "generated_at_ms": _NOW,
            "instrument_scope": ["BTC"],
            "required_days": [day * _DAY for day in range(13, 21)],
            "source_required_days": {
                "funding": [day * _DAY for day in range(13, 21)],
                "perp_mark_prices": [day * _DAY for day in range(12, 20)],
                "oi_metrics": [day * _DAY for day in range(12, 20)],
            },
            "artifacts": {
                "prices": artifact(price_path),
                "funding": artifact(funding_path),
                "oi_metrics": artifact(oi_path),
            },
            "sources": {
                "perp_mark_prices": {"source": "perp_mark_prices", **source},
                "funding": {"source": "funding", **source},
                "oi_metrics": {"source": "oi_metrics", **source},
            },
            "raw_lineage": summarize_archive(str(raw_path)),
            "verdict": "PASS",
            "block_reasons": [],
        }), encoding="utf-8")
    return (price_path, funding_path, oi_path, snapshot_path, refresh_path,
            raw_path, lineage_path)


def _build(tmp_path, *, funding_gap=False, price_basis="PERP_MARK_PRICE"):
    price, funding, oi, snapshot, refresh, raw, lineage = _seed(
        tmp_path, funding_gap=funding_gap
    )
    return build_data_quality_evidence(
        now_ms=_NOW,
        instruments=["BTC"],
        price_path=str(price),
        funding_path=str(funding),
        oi_path=str(oi),
        snapshot_path=str(snapshot),
        refresh_status_path=str(refresh),
        raw_archive_path=str(raw),
        lineage_backfill_path=str(lineage),
        price_basis=price_basis,
    )


def test_complete_perp_scoped_artifacts_pass_quality_and_admission(tmp_path):
    evidence = _build(tmp_path)

    assert evidence["quality_verdict"] == "PASS"
    assert evidence["verdict"] == "PASS"
    assert evidence["instrument_scope"] == ["BTC"]
    assert evidence["sources"]["prices"]["failed_instruments"] == []
    assert evidence["sources"]["funding"]["failed_instruments"] == []
    assert evidence["sources"]["oi_metrics"]["failed_instruments"] == []
    assert evidence["lineage_verdict"] == "PASS"
    assert evidence["raw_lineage"]["datasets"] == {
        "funding": 1,
        "oi_metrics": 1,
        "perp_mark_prices": 1,
        "spot_klines": 1,
    }
    assert all(
        len(artifact["sha256"]) == 64
        for artifact in evidence["artifacts"].values()
    )


def test_funding_gap_blocks_quality_admission(tmp_path):
    evidence = _build(tmp_path, funding_gap=True)

    assert evidence["quality_verdict"] == "BLOCK"
    assert evidence["verdict"] == "BLOCK"
    assert evidence["sources"]["funding"]["failed_instruments"] == ["BTC"]
    assert "completeness" in evidence["sources"]["funding"]["instruments"]["BTC"]["detail"]


def test_spot_proxy_is_not_eligible_as_perp_forward_evidence(tmp_path):
    evidence = _build(tmp_path, price_basis="SPOT_PROXY_FOR_PERP")

    assert evidence["quality_verdict"] == "PASS"
    assert evidence["verdict"] == "BLOCK"
    assert "SPOT_PROXY_FOR_PERP" in evidence["block_reasons"]


def test_missing_raw_lineage_blocks_admission_even_when_canonical_data_passes(tmp_path):
    price, funding, oi, snapshot, refresh, raw, lineage = _seed(
        tmp_path, include_lineage=False
    )

    evidence = build_data_quality_evidence(
        now_ms=_NOW,
        instruments=["BTC"],
        price_path=str(price),
        funding_path=str(funding),
        oi_path=str(oi),
        snapshot_path=str(snapshot),
        refresh_status_path=str(refresh),
        raw_archive_path=str(raw),
        lineage_backfill_path=str(lineage),
        price_basis="PERP_MARK_PRICE",
    )

    assert evidence["quality_verdict"] == "PASS"
    assert evidence["lineage_verdict"] == "BLOCK"
    assert evidence["verdict"] == "BLOCK"
    assert "LINEAGE:ARCHIVE_MISSING" in evidence["block_reasons"]


def test_canonical_artifact_change_invalidates_lineage_backfill(tmp_path):
    price, funding, oi, snapshot, refresh, raw, lineage = _seed(tmp_path)
    funding.write_text(
        json.dumps({"BTC": {str(day * _DAY): 0.001 for day in (18, 19, 20)}}, indent=2),
        encoding="utf-8",
    )

    evidence = build_data_quality_evidence(
        now_ms=_NOW,
        instruments=["BTC"],
        price_path=str(price),
        funding_path=str(funding),
        oi_path=str(oi),
        snapshot_path=str(snapshot),
        refresh_status_path=str(refresh),
        raw_archive_path=str(raw),
        lineage_backfill_path=str(lineage),
        price_basis="PERP_MARK_PRICE",
    )

    assert evidence["quality_verdict"] == "PASS"
    assert evidence["lineage_verdict"] == "BLOCK"
    assert any(
        reason.startswith("LINEAGE:BACKFILL_ARTIFACT_MISMATCH:funding")
        for reason in evidence["block_reasons"]
    )


def test_mark_price_json_is_admitted_as_perp_price_evidence(tmp_path):
    _, funding, oi, snapshot, refresh, raw, lineage = _seed(tmp_path)
    mark_path = tmp_path / "mark.json"
    mark_path.write_text(
        json.dumps({
            "_meta": {"BTC": {"through": 19 * _DAY}},
            "data": {
                "BTC": {
                    str((19 - offset) * _DAY): 100.0 + offset
                    for offset in range(8)
                }
            },
        }),
        encoding="utf-8",
    )
    lineage_payload = json.loads(lineage.read_text(encoding="utf-8"))
    mark_bytes = mark_path.read_bytes()
    lineage_payload["artifacts"]["prices"] = {
        "path": str(mark_path).replace("\\", "/"),
        "size_bytes": len(mark_bytes),
        "sha256": hashlib.sha256(mark_bytes).hexdigest(),
    }
    lineage.write_text(json.dumps(lineage_payload), encoding="utf-8")

    evidence = build_data_quality_evidence(
        now_ms=_NOW,
        instruments=["BTC"],
        price_path=str(mark_path),
        funding_path=str(funding),
        oi_path=str(oi),
        snapshot_path=str(snapshot),
        refresh_status_path=str(refresh),
        raw_archive_path=str(raw),
        lineage_backfill_path=str(lineage),
        price_basis="PERP_MARK_PRICE",
    )

    assert evidence["sources"]["prices"]["passed"] is True
    assert evidence["verdict"] == "PASS"


def test_write_data_quality_evidence_publishes_complete_json(tmp_path):
    price, funding, oi, snapshot, refresh, raw, lineage = _seed(tmp_path)
    output = tmp_path / "evidence.json"

    evidence = write_data_quality_evidence(
        output_path=str(output),
        now_ms=_NOW,
        instruments=["BTC"],
        price_path=str(price),
        funding_path=str(funding),
        oi_path=str(oi),
        snapshot_path=str(snapshot),
        refresh_status_path=str(refresh),
        raw_archive_path=str(raw),
        lineage_backfill_path=str(lineage),
        price_basis="PERP_MARK_PRICE",
    )

    assert json.loads(output.read_text(encoding="utf-8")) == evidence


def test_run_records_authoritative_decision_before_publishing_manifest(tmp_path):
    price, funding, oi, snapshot, refresh, raw, lineage = _seed(tmp_path)
    output = tmp_path / "evidence.json"
    authority = tmp_path / "admission.db"

    evidence, decision, inserted = run_data_quality_admission(
        output_path=str(output),
        authority_path=str(authority),
        recorded_at_ms=_NOW + 1,
        now_ms=_NOW,
        instruments=["BTC"],
        price_path=str(price),
        funding_path=str(funding),
        oi_path=str(oi),
        snapshot_path=str(snapshot),
        refresh_status_path=str(refresh),
        raw_archive_path=str(raw),
        lineage_backfill_path=str(lineage),
        price_basis="PERP_MARK_PRICE",
    )

    assert inserted is True
    assert decision.verdict == "PASS"
    assert json.loads(output.read_text(encoding="utf-8")) == evidence
    store = DataQualityAdmissionStore(str(authority), create=False)
    assert store.decisions() == (decision,)
    assert store.evidence(decision.admission_id) == evidence
    assert len(store.pending_event_outbox()) == 1
    store.close()
