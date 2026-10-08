"""Original public validation and original Gold accounts over frozen segments."""
from decimal import Decimal
from bisect import bisect_left
from pathlib import Path
import tempfile

from quantos.contracts import gold_segmented_input as contract
from quantos.contracts.registered_research_run import digest
from services.execution.backtest_authority import exact_identity_hash
from services.execution.perpetual_public_replay import prepare_public_replay
from services.execution.perpetual_ledger_v2 import FundingEvent
from services.execution.perpetual_simulation import SimulationFrame
from tools.gold_bar_journal import restore, bind_capture


def compare(arguments, *, history=None):
    from tools.gold_bar_experiment import _encode

    document = _encode(arguments)
    raw_journals = contract.journals(document)
    parts = contract.documents(document)
    return _compare_parts(arguments, zip(parts, raw_journals, strict=True),
                          input_transport_sha256=digest(document), history=history)


def _compare_parts(arguments, pairs, *, input_transport_sha256, history=None,
                   preparation=None, source_binding=None, replay_budget=None):
    """Original validation/accounting with an explicit trusted input transport."""
    from tools.gold_bar_cost_replay import _compare_bars
    from services.research.gold_net_portfolio import compare_portfolio
    if replay_budget is not None:
        from quantos.contracts.perpetual_replay_budget import validate
        replay_budget = validate(replay_budget)
        contract.require(source_binding is not None, 'REPLAY_BUDGET_REQUIRES_NATIVE_SOURCE')
    collection_source = arguments['release_identity']
    if source_binding is not None and source_binding.get('schema') == 'QUANTOS_GOLD_SOURCE_EXECUTION_BINDING_V2':
        from quantos.contracts.gold_native_source import collection_identity
        from tools.gold_bar_experiment import _encode
        contract.require(source_binding == arguments.get('source_binding'), 'SOURCE_BINDING_CHANGED')
        collection_source = collection_identity(_encode(arguments))

    with tempfile.TemporaryDirectory(prefix='quantos-gold-segments-') as directory:
        reviews = [] if preparation is None else preparation.reviews
        all_frames = [] if preparation is None else preparation.frames
        identities = [] if preparation is None else preparation.segments
        updates = [] if preparation is None else preparation.updates
        previous_bars = None
        observed_times = [] if preparation is None else preparation.decisions
        sample_history = {} if preparation is None else preparation.samples
        previous_capture = None
        first_prepared = None
        count = 0
        chain_cursor = chain_review = None
        for index, (part, files) in enumerate(pairs):
            count += 1
            if preparation is not None:
                preparation.reserve_journal(sum(len(raw) for raw in files.values()))
            root = Path(directory)/str(index)
            root.mkdir()
            for name, raw in files.items():
                with (root/name).open('xb') as handle:
                    handle.write(raw)
            cursor, review = restore(root, previous_cursor=chain_cursor, previous_review=chain_review)
            if 'prefix_chain' in review:
                contract.require(review['execution_source'] == collection_source, 'EXECUTION_SOURCE_CHANGED')
                chain_cursor, chain_review = cursor, review
            reviews.append(dict(root=str(root), review=review))
            capture = part['capture']
            bind_capture(review, capture, part.get('observed_funding_records'))
            contract.require(capture.get('symbol') == 'XAUUSDT', 'MARKET_SCOPE')
            prepared = prepare_public_replay(capture,
                observed_funding_records=part.get('observed_funding_records'),
                settlement_window_ms=part.get('settlement_window_ms'))
            frames = prepared['frames']
            if previous_bars is None:
                closed_count = review['closed_bars']
                contract.require(closed_count > 0, 'NO_CLOSED_BAR')
                warmup = cursor.bars[:-closed_count]
                contract.require(warmup and max(b.available_ms for b in warmup) <= frames[0].timestamp,
                                 'WARMUP_UNAVAILABLE')
                new_bars = cursor.bars[-closed_count:]
                last = frames[0].timestamp-1
                first_prepared = prepared
            else:
                contract.require(tuple(cursor.bars[:len(previous_bars)]) == previous_bars,
                                 'BAR_HISTORY_CHANGED')
                new_bars = cursor.bars[len(previous_bars):]
                last = all_frames[-1].timestamp
                previous_input_end = last
                # Shared trailing receipts may establish continuous policy coverage.
                # They are checked against their originals and never executed twice.
                for sample in capture['samples']:
                    timestamp = sample['depth']['received_at']
                    if timestamp <= last:
                        contract.require(sample_history.get(timestamp) == sample,
                                         'OVERLAP_CHANGED')
                old_frames = ({f.timestamp: f for f in all_frames}
                              if preparation is None else None)
                for frame in frames:
                    if frame.timestamp <= last and any(type(e) is FundingEvent for e in frame.market_events):
                        old = (old_frames.get(frame.timestamp) if preparation is None
                               else all_frames.at_time(frame.timestamp))
                        contract.require(old is not None and frame.funding_available_at == old.funding_available_at
                            and tuple(e for e in frame.market_events if type(e) is FundingEvent)
                            == tuple(e for e in old.market_events if type(e) is FundingEvent),
                            'FUNDING_HISTORY_CHANGED')
                new_samples = [s for s in capture['samples'] if s['depth']['received_at'] > last]
                contract.require(new_samples, 'NO_NEW_QUOTES')
                old_mark_time = max(s['mark']['payload']['time'] for s in sample_history.values())
                contract.require(new_samples[0]['mark']['payload']['time'] >= old_mark_time,
                                 'MARK_OBSERVATION_ORDER')
                if frames[0].timestamp <= last:
                    # The next capture declares its first original quote as the
                    # boundary. Assign the common tail to that epoch once, before
                    # any account executes. Choice does not depend on later P&L.
                    boundary = frames[0].timestamp
                    contract.require(boundary > identities[-1]['first_frame_ms'],
                                     'OVERLAP_REWINDS_PRIOR_EPOCH')
                    end = bisect_left(all_frames, boundary, key=lambda frame: frame.timestamp)
                    contract.require(end > identities[-1]['first_frame'], 'EMPTY_PRIOR_EPOCH')
                    expected = {t for t in sample_history if boundary <= t <= previous_input_end}
                    shared = {s['depth']['received_at'] for s in capture['samples']
                              if s['depth']['received_at'] <= previous_input_end}
                    contract.require(shared == expected, 'OVERLAP_OMITS_ORIGINAL_QUOTES')
                    replacement = {f.timestamp: f for f in frames}
                    for old in all_frames[end:]:
                        funding = tuple(e for e in old.market_events if type(e) is FundingEvent)
                        if funding:
                            new = replacement.get(old.timestamp)
                            contract.require(new is not None
                                and tuple(e for e in new.market_events if type(e) is FundingEvent) == funding
                                and new.funding_available_at == old.funding_available_at,
                                'OVERLAP_OMITS_ORIGINAL_FUNDING')
                    del all_frames[end:]
                    prior_identity = identities[-1]
                    prior_identity.update(last_frame_ms=all_frames[-1].timestamp,
                        retained_frames=end-identities[-1]['first_frame'])
                    if preparation is not None:
                        identities.replace(len(identities)-1, prior_identity)
                    last = all_frames[-1].timestamp
                frames = tuple(f for f in frames if f.timestamp > last)
                contract.require(frames and frames[0].quotes, 'NO_NEW_QUOTES')
                first_quote = frames[0].timestamp
                contract.require(first_quote-last <= min(capture['max_gap_ms'],
                    previous_capture['max_gap_ms']), 'QUOTE_GAP')
                before = previous_capture['exchange_after']
                after = capture['bootstrap']['exchange']
                if after['received_at'] <= before['received_at']:
                    def rows(record):
                        return [r for r in record['payload']['symbols'] if r.get('symbol') == 'XAUUSDT']
                    contract.require(rows(after) == rows(before), 'RULE_OBSERVATION_ORDER')
                contract.require(all(b.available_ms > previous_input_end for b in new_bars),
                                 'RETROSPECTIVE_BAR_RECEIPT')
            contract.require(all(last < b.available_ms <= frames[-1].timestamp for b in new_bars),
                             'BAR_RECEIPT_OUTSIDE_SEGMENT')
            by_time = {f.timestamp: f for f in frames}
            for bar in cursor.bars:
                # A receipt in the small gap is delivered before the next actual
                # quote callback, never used to fabricate a boundary mark.
                if (frames[0].timestamp <= bar.available_ms <= frames[-1].timestamp
                        and bar.available_ms not in by_time):
                    by_time[bar.available_ms] = SimulationFrame(bar.available_ms)
            frames = tuple(by_time[t] for t in sorted(by_time))
            if index:
                updates.append(dict(first_frame=len(all_frames), **{k:prepared[k] for k in
                    ('contract_rules','funding_policies','order_rule_profiles')}))
            identities.append(dict(index=index, first_frame=len(all_frames),
                first_frame_ms=frames[0].timestamp, last_frame_ms=frames[-1].timestamp,
                journal_hashes=review['hashes'], journal_source=review['source'],
                quote_manifest=prepared['manifest'], new_closed_decision_times=[b.available_ms for b in new_bars],
                retained_frames=len(frames)))
            all_frames.extend(frames)
            observed_times.extend(b.available_ms for b in new_bars)
            for sample in capture['samples']:
                sample_history[sample['depth']['received_at']] = sample
            previous_bars = tuple(cursor.bars)
            previous_capture = capture
            if preparation is not None:
                preparation.bars.extend(cursor.bars if index == 0 else new_bars)
                preparation.flush()
        contract.require(count > 0, 'EMPTY_INPUT')
        manifest = dict(schema='GOLD_SEGMENTED_PUBLIC_QUOTE_MANIFEST_V1',
            authority='TECHNICAL_VALIDATION_ONLY', venue='BINANCE_USDM', instruments=['XAUUSDT'],
            start_ms=all_frames[0].timestamp, end_ms=all_frames[-1].timestamp,
            segments=([v['quote_manifest'] for v in identities] if preparation is None
                      else preparation.projection(identities, 'quote_manifest')),
            limitations=list(dict.fromkeys(v for p in identities for v in p['quote_manifest']['limitations'])))
        if any('observed_funding' in p['quote_manifest'] for p in identities):
            manifest['limitations'] = [v for v in manifest['limitations'] if v != 'NO_FUNDING_SETTLEMENT_IN_SAMPLE']
        identity = dict(schema='GOLD_SEGMENTED_BAR_QUOTE_BINDING_V1',
            input_segment_count=count, input_transport_sha256=input_transport_sha256,
            boundary_policy='NEXT_CAPTURE_FIRST_ORIGINAL_QUOTE_SINGLE_EXECUTION',
            journal_hashes=review['hashes'], journal_source=review['source'],
            quote_manifest=manifest, segments=identities, closed_decision_times=observed_times,
            evaluated_frames=len(all_frames))
        if source_binding is not None:
            identity.update(schema='GOLD_CHUNKED_BAR_QUOTE_BINDING_V2', source_binding=source_binding)
        if replay_budget is not None:
            contract.require(count <= replay_budget['maximum_environment_segments'], 'REPLAY_ENVIRONMENT_BUDGET')
            contract.require(all_frames[-1].timestamp-all_frames[0].timestamp <= replay_budget['maximum_elapsed_ms'],
                             'REPLAY_ELAPSED_BUDGET')
            identity.update(schema='GOLD_CHUNKED_BAR_QUOTE_BINDING_V3', replay_budget=replay_budget)
        if preparation is not None:
            preparation.seal()
        prepared = dict(first_prepared, frames=tuple(all_frames) if preparation is None else all_frames, manifest=manifest)
        if updates:
            prepared['environment_updates'] = updates
        if replay_budget is not None:
            prepared['replay_budget'] = replay_budget
        bars = cursor.bars if preparation is None else preparation.bars
        common = {k:arguments[k] for k in ('release_identity','initial_collateral','fee_rate')}
        result = _compare_bars(bars=bars, prepared=prepared, dataset_identity=identity,
            quantity=arguments['quantity'], entry_not_before_ms=min(observed_times),
            incremental_bars=True, history=history, **common)
        if 'portfolio_plan' in arguments:
            plan = arguments['portfolio_plan']
            result['portfolio'] = compare_portfolio(bars=bars, prepared=prepared,
                quantities={n:Decimal(v) for n,v in plan['quantities'].items()},
                max_notional=Decimal(plan['max_notional']), entry_not_before_ms=min(observed_times),
                observation_binding=identity, incremental_bars=True, history=history, **common)
        chain_cursor = chain_review = None
        for original in reviews:
            recheck_cursor, recheck = restore(original['root'], previous_cursor=chain_cursor,
                                            previous_review=chain_review)
            contract.require(recheck == original['review'], 'JOURNAL_CHANGED_DURING_COMPARISON')
            if 'prefix_chain' in recheck:
                chain_cursor, chain_review = recheck_cursor, recheck
        if preparation is not None:
            preparation.verify()
        from quantos.streaming_identity import exact_stream_identity_hash
        hash_result = exact_identity_hash if history is None else exact_stream_identity_hash
        result['result_hash'] = hash_result(
            {k:v for k,v in result.items() if k != 'result_hash'}, name='gold_bar_public_comparison')
        return result
