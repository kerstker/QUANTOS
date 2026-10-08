"""The original account must keep every fact while spooling long histories."""

import json
from decimal import Decimal as D

import pytest

from services.execution.perpetual_simulation import LinearPerpetualSimulationSession, OrderIntent
from tests.execution.test_perpetual_simulation import T, frame
from tests.execution.test_perpetual_simulation_session import configuration


def trade(ctx):
    if ctx.timestamp == T:
        return (OrderIntent("entry", "BTCUSDT", D(".1")),)
    if ctx.timestamp == T + 30:
        return (OrderIntent("exit", "BTCUSDT", D("-.1"), reduce_only=True),)
    return ()


def load_result(root, manifest):
    import hashlib

    raw = b"".join((root / "blobs" / part["sha256"]).read_bytes() for part in manifest["parts"])
    assert hashlib.sha256(raw).hexdigest() == manifest["sha256"]
    return json.loads(raw)


def test_disk_history_streamed_result_and_checkpoint_match_original_account(tmp_path):
    from tools.perpetual_stream_history import SimulationHistory, write_result

    frames = (frame(0), frame(10, size=".04"), frame(20), frame(30, "105"), frame(40, "105"))
    original = LinearPerpetualSimulationSession(**configuration(trade))
    original.advance(frames)
    with SimulationHistory(tmp_path / "history", maximum_bytes=32_000_000) as storage:
        actual = LinearPerpetualSimulationSession(history_store=storage, **configuration(trade))
        for batch in (frames[:1], frames[1:3], frames[3:]):
            actual.advance(batch)
        assert actual.report() == original.report()
        assert actual._state["account"].ledger.event_records == original._state["account"].ledger.event_records
        assert actual.checkpoint() == original.checkpoint()
        manifest = write_result(actual, tmp_path / "result", maximum_bytes=32_000_000, chunk_bytes=2000)
        assert len(manifest["parts"]) > 1
        assert load_result(tmp_path / "result", manifest) == original.report()
        assert manifest["authoritative"] is manifest["submission_enabled"] is False
        assert len(storage.frames) == 5 and not isinstance(actual._state["account"].history, list)


def test_spooled_epoch_restore_preserves_original_partial_orders_and_cash(tmp_path):
    from tools.perpetual_stream_history import SimulationHistory, write_result
    from tests.execution.test_perpetual_environment_epochs import configuration, environment, tape, trade

    with SimulationHistory(tmp_path / "first", maximum_bytes=32_000_000) as storage:
        actual = LinearPerpetualSimulationSession(history_store=storage, **configuration(trade))
        actual.advance(tape()[:3])
        actual.advance_environment(tape()[3:6], **environment())
        checkpoint = actual.checkpoint()
        with SimulationHistory(tmp_path / "second", maximum_bytes=32_000_000) as other:
            restored = LinearPerpetualSimulationSession.restore_batches(
                checkpoint,
                batches=(tape()[:3], tape()[3:6]),
                environment_updates=[dict(first_frame=3, **environment())],
                history_store=other,
                **configuration(trade),
            )
            for instance in (actual, restored):
                instance.advance(tape()[6:])
            expected = actual.report()
            assert restored.report() == expected
            manifest = write_result(restored, tmp_path / "result", maximum_bytes=32_000_000, chunk_bytes=2000)
            assert load_result(tmp_path / "result", manifest) == expected
            assert expected["reconciliation"]["ending_equity"] == "1000.3116"
            assert expected["reconciliation"]["residual"] == "0"


def test_epoch_history_keeps_actual_venue_constraints_and_protection_rejections(tmp_path):
    from dataclasses import replace
    from tools.perpetual_stream_history import SimulationHistory
    from tools.gold_input_history import GoldInputHistory
    from services.execution.perpetual_simulation import VenueOrderConstraints
    from tests.execution.test_perpetual_environment_epochs import configuration, environment, tape, trade
    from tests.execution.test_perpetual_simulation import quote

    def guarded_trade(ctx):
        if ctx.timestamp == T+20:
            return (OrderIntent('bad-band', 'BTCUSDT', D('.1'), limit_price=D('120')),)
        if ctx.timestamp == T+30:
            return (OrderIntent('bad-market', 'BTCUSDT', D('.1')),)
        return trade(ctx)
    config = configuration(guarded_trade)
    old = VenueOrderConstraints(D('1.2'), D('.8'), 2, D('.1'))
    new = VenueOrderConstraints(D('1.05'), D('.95'), 2, D('.005'))
    config['order_rule_profiles'] = (replace(config['order_rule_profiles'][0], venue_constraints=old),)
    update = environment()
    update['order_rule_profiles'] = (replace(update['order_rule_profiles'][0], venue_constraints=new),)
    frames = list(tape())
    frames[4] = frame(30, ask='102')
    frames[5] = replace(frames[5], quotes=(quote(35, ask='102'),))
    with GoldInputHistory(tmp_path/'prepared-constraints', maximum_bytes=32_000_000) as prepared:
        prepared.updates.append(dict(first_frame=3, **update))
        prepared.seal()
        assert prepared.updates[0]['order_rule_profiles'][0].venue_constraints == new
        original = LinearPerpetualSimulationSession(**config)
        original.advance(frames[:3])
        original.advance_environment(frames[3:6], **update)
        with SimulationHistory(tmp_path/'constraint-history', maximum_bytes=32_000_000) as history:
            actual = LinearPerpetualSimulationSession(history_store=history, **config)
            actual.advance(frames[:3])
            actual.advance_environment(frames[3:6], **update)
            assert actual.checkpoint() == original.checkpoint()
            with SimulationHistory(tmp_path/'restored-constraints', maximum_bytes=32_000_000) as other:
                restored = LinearPerpetualSimulationSession.restore_batches(actual.checkpoint(),
                    batches=(frames[:3], frames[3:6]), environment_updates=[prepared.updates[0]],
                    history_store=other, **config)
                for instance in (original, actual, restored):
                    instance.advance(frames[6:])
                expected = original.report()
                assert actual.report() == restored.report() == expected
                orders = {o['order_id']:o for o in expected['orders']}
                assert orders['bad-band']['reason'] == 'PERCENT_PRICE'
                assert orders['bad-market']['reason'] == 'MARKET_TAKE_BOUND'
                assert orders['bad-band']['filled_quantity'] == orders['bad-market']['filled_quantity'] == '0'
                assert expected['reconciliation']['residual'] == '0'


def test_duplicate_historical_order_id_and_invalid_tail_remain_fail_closed(tmp_path):
    from tools.perpetual_stream_history import SimulationHistory

    def duplicate(ctx):
        return (OrderIntent("same", "BTCUSDT", D(".1")),)

    with SimulationHistory(tmp_path / "history", maximum_bytes=32_000_000) as storage:
        s = LinearPerpetualSimulationSession(history_store=storage, **configuration(duplicate))
        s.advance((frame(0),))
        with pytest.raises(ValueError, match="duplicate order_id"):
            s.advance((frame(10),))
        with pytest.raises(ValueError, match="SESSION_FAILED"):
            s.checkpoint()
        assert len(storage.frames) >= 1


def test_incremental_validator_never_reads_prior_frame_bodies(tmp_path, monkeypatch):
    from services.execution import perpetual_simulation as engine

    s = LinearPerpetualSimulationSession(**configuration())
    s.advance((frame(0), frame(10)))
    original = engine._validate_market_environment
    validated = []

    def observed(rules, policies, profiles, frames, **options):
        validated.append(len(frames))
        return original(rules, policies, profiles, frames, **options)

    monkeypatch.setattr(engine, "_validate_market_environment", observed)
    s.advance((frame(20),))
    assert validated == [1]


def test_failed_stream_publication_preserves_chunks_without_complete_marker(tmp_path):
    from tools.perpetual_stream_history import SimulationHistory, write_result

    with SimulationHistory(tmp_path / "history", maximum_bytes=32_000_000) as storage:
        s = LinearPerpetualSimulationSession(history_store=storage, **configuration(trade))
        s.advance((frame(0), frame(10)))
        with pytest.raises(ValueError, match="RESULT_BYTES"):
            write_result(s, tmp_path / "result", maximum_bytes=3000, chunk_bytes=2000)
        assert (tmp_path / "result/failure.json").is_file()
        assert not (tmp_path / "result/manifest.json").exists()


@pytest.mark.parametrize("damage", ["rewrite", "replace"])
def test_history_changed_before_read_cannot_be_sealed(tmp_path, damage):
    from tools.perpetual_stream_history import SimulationHistory, write_result

    with pytest.raises(ValueError, match="HISTORY_CHANGED"):
        with SimulationHistory(tmp_path / "history", maximum_bytes=32_000_000) as storage:
            s = LinearPerpetualSimulationSession(history_store=storage, **configuration())
            s.advance((frame(0), frame(10)))
            raw = storage.path.read_bytes()
            if damage == "rewrite":
                storage.path.write_bytes(raw)
            else:
                other = tmp_path / "replacement"
                other.write_bytes(raw)
                other.chmod(0o600)
                other.replace(storage.path)
            write_result(s, tmp_path / "result", maximum_bytes=32_000_000, chunk_bytes=2000)
    assert not (tmp_path / "result/manifest.json").exists()


def test_stream_identity_preserves_the_existing_tagged_wire_for_every_scalar_type():
    from quantos.streaming_identity import StreamArray, StreamObject, exact_stream_identity_hash, key_order
    from quantos.contracts.backtest_identity import exact_identity_hash

    values = [None, True, 1, 0.5, D("1.00"), b"bytes", {"\n": ("x", 2), "中文": {"a", "b"}}]
    expected = {"values": values, "value": D(".001")}
    actual = {"values": StreamArray(lambda: iter(values)), "value": D(".001")}
    assert exact_stream_identity_hash(actual) == exact_identity_hash(expected)
    pairs = sorted(expected.items(), key=lambda pair: key_order(pair[0]))
    assert exact_stream_identity_hash(StreamObject(lambda: iter(pairs))) == exact_identity_hash(expected)


def test_batched_restore_rejects_changed_original_source_and_extra_frames(tmp_path):
    from tools.perpetual_stream_history import SimulationHistory

    s = LinearPerpetualSimulationSession(**configuration(trade))
    s.advance((frame(0), frame(10)))
    checkpoint = s.checkpoint()
    with SimulationHistory(tmp_path / "history", maximum_bytes=32_000_000) as storage:
        config = configuration(trade)
        config["release_identity"] = {"commit": "a" * 40}
        with pytest.raises(ValueError, match="CHECKPOINT_REPLAY_MISMATCH"):
            LinearPerpetualSimulationSession.restore_batches(
                checkpoint, batches=((frame(0),), (frame(10),)), history_store=storage, **config
            )
    with pytest.raises(ValueError, match="CHECKPOINT_FRAME_COUNT"):
        LinearPerpetualSimulationSession.restore_batches(
            checkpoint, batches=((frame(0),), (frame(10), frame(20))), **configuration(trade)
        )


def test_changed_result_chunk_cannot_receive_complete_manifest(tmp_path, monkeypatch):
    from tools import perpetual_stream_history as tool

    original = tool.json_parts
    changed = False

    def observed(value, **options):
        nonlocal changed
        for part in original(value, **options):
            yield part
            blobs = list((tmp_path / "result/blobs").iterdir())
            if blobs and not changed:
                blobs[0].write_bytes(blobs[0].read_bytes())
                changed = True

    monkeypatch.setattr(tool, "json_parts", observed)
    with tool.SimulationHistory(tmp_path / "history", maximum_bytes=32_000_000) as storage:
        s = LinearPerpetualSimulationSession(history_store=storage, **configuration(trade))
        s.advance((frame(0), frame(10)))
        with pytest.raises(ValueError, match="FILE_CHANGED"):
            tool.write_result(s, tmp_path / "result", maximum_bytes=32_000_000, chunk_bytes=2000)
    assert changed and not (tmp_path / "result/manifest.json").exists()


def test_history_frame_budget_stops_an_unbounded_iterator_without_losing_all_facts(tmp_path):
    from tools.perpetual_stream_history import SimulationHistory

    read = []

    def infinite():
        index = 0
        while True:
            read.append(index)
            yield frame(index)
            index += 1

    with SimulationHistory(tmp_path / "history", maximum_bytes=32_000_000) as storage:
        s = LinearPerpetualSimulationSession(history_store=storage, **configuration())
        with pytest.raises(ValueError, match="FRAME_BATCH_BUDGET"):
            s.advance(infinite())
        assert len(read) == 4097
        with pytest.raises(ValueError, match="SESSION_FAILED"):
            s.report_stream()


def test_closed_disk_frames_replay_a_cold_checkpoint_without_saved_balances(tmp_path):
    from tools.perpetual_stream_history import SimulationHistory, write_result, frames_from_history

    prefix = (frame(0), frame(10, size=".04"))
    with SimulationHistory(tmp_path / "before", maximum_bytes=32_000_000) as history:
        original = LinearPerpetualSimulationSession(history_store=history, **configuration(trade))
        original.advance(prefix)
        checkpoint = original.checkpoint()
        manifest = write_result(original, tmp_path / "prefix-result", maximum_bytes=32_000_000, chunk_bytes=2000)
    archived = manifest["input_archive"]
    with SimulationHistory(tmp_path / "after", maximum_bytes=32_000_000) as history:
        restored = LinearPerpetualSimulationSession.restore_batches(
            checkpoint,
            batches=frames_from_history(
                tmp_path / "before",
                database_sha256=archived["sha256"],
                frame_count=archived["frame_count"],
                batch_frames=2,
            ),
            history_store=history,
            **configuration(trade),
        )
        assert restored.checkpoint() == checkpoint
        restored.advance((frame(20), frame(30, "105"), frame(40, "105")))
        expected = LinearPerpetualSimulationSession(**configuration(trade))
        expected.advance((*prefix, frame(20), frame(30, "105"), frame(40, "105")))
        assert restored.report() == expected.report()
    path = tmp_path / "before/history.sqlite3"
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="HISTORY_SOURCE_CHANGED"):
        list(frames_from_history(tmp_path / "before", database_sha256=archived["sha256"], frame_count=2))


def test_cold_frame_reader_preserves_an_atomic_forecast_funding_batch(tmp_path):
    from dataclasses import asdict, replace
    from services.execution.perpetual_ledger_v2 import FundingEvent
    from services.execution.perpetual_simulation import ForecastFundingPolicySnapshot
    from tools.perpetual_stream_history import SimulationHistory, write_result, frames_from_history

    config = configuration()
    policy = replace(config["funding_policies"][0], settlement_times=(T + 4095,))
    config["funding_policies"] = (ForecastFundingPolicySnapshot(**asdict(policy), settlement_window_ms=1000),)
    with SimulationHistory(tmp_path / "before", maximum_bytes=128_000_000) as history:
        source = LinearPerpetualSimulationSession(history_store=history, **config)
        source.advance(tuple(frame(i) for i in range(4095)))
        source.advance(
            (
                replace(frame(4095), funding_pause_until=T + 4200),
                replace(
                    frame(4097, funding=(FundingEvent(T + 4097, "BTCUSDT", D(".01"), D("100")),)),
                    funding_available_at=T + 4200,
                ),
                frame(4200),
            )
        )
        checkpoint = source.checkpoint()
        manifest = write_result(source, tmp_path / "prefix-result", maximum_bytes=32_000_000)
    archived = manifest["input_archive"]
    with SimulationHistory(tmp_path / "after", maximum_bytes=128_000_000) as history:
        restored = LinearPerpetualSimulationSession.restore_batches(
            checkpoint,
            batches=frames_from_history(
                tmp_path / "before", database_sha256=archived["sha256"], frame_count=archived["frame_count"]
            ),
            history_store=history,
            **config,
        )
        assert restored.checkpoint() == checkpoint
