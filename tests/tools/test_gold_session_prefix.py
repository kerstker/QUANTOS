import json

import pytest

from tests.tools.test_gold_bar_observed_replay import continuous_inputs


def inputs(tmp_path, monkeypatch):
    journal, capture, _ = continuous_inputs(tmp_path, monkeypatch)
    quotes=tmp_path/'quotes'
    quotes.mkdir()
    (quotes/'plan.json').write_text(json.dumps(dict(schema='GOLD_PUBLIC_QUOTE_SESSION_PLAN_V1',source={'fixture':True},submission_enabled=False)))
    records=[capture['bootstrap'][kind] for kind in ('exchange','funding','candles')]
    for sample in capture['samples']:
        sample['depth']['requested_at']=sample['mark']['received_at']
        records.extend([sample['mark'],sample['depth']])
    for i,row in enumerate(records):
        (quotes/f'receipt-{i:06d}.json').write_text(json.dumps(row)+'\n')
    bars=tmp_path/'bars'
    bars.mkdir()
    (bars/'plan.json').write_text(json.dumps(dict(schema='GOLD_CONTINUOUS_BAR_SESSION_V1',source={'fixture':True},submission_enabled=False)))
    for name in ['warmup.json','warmup-receipt.json']:
        (bars/name).write_bytes((journal/name).read_bytes())
    messages=[json.loads(x) for x in (journal/'messages.jsonl').read_text().splitlines()]
    decisions=[json.loads(x) for x in (journal/'decisions.jsonl').read_text().splitlines()]
    (bars/'messages-0000.jsonl').write_text(''.join(json.dumps(dict(row,decision=decision))+'\n' for row,decision in zip(messages,decisions)))
    return quotes,bars,capture,messages


def test_prefix_uses_common_receipt_clock_and_preserves_raw_inputs(tmp_path,monkeypatch):
    from tools.gold_session_prefix import snapshot
    quotes,bars,capture,messages=inputs(tmp_path,monkeypatch)
    result=snapshot(quotes,bars,tmp_path/'snapshot')
    assert result['as_of_ms']==min(capture['samples'][-1]['depth']['received_at'],messages[-1]['received_at_ms'])
    saved=json.loads((tmp_path/'snapshot/inputs.json').read_text())
    assert all(row['depth']['received_at']<=result['as_of_ms'] for row in saved['samples'])
    assert all(row['received_at_ms']<=result['as_of_ms'] for row in saved['bar_messages'])
    assert result['replay_ready'] is False and result['submission_enabled'] is False
    assert (tmp_path/'snapshot/warmup.json').read_bytes()==(bars/'warmup.json').read_bytes()


def test_partial_writing_tail_is_excluded_but_earlier_missing_file_rejects(tmp_path,monkeypatch):
    from tools.gold_session_prefix import snapshot
    quotes,bars,_,_=inputs(tmp_path,monkeypatch)
    n=len(list(quotes.glob('receipt-*.json')))
    (quotes/f'receipt-{n:06d}.json').write_bytes(b'{')
    with (bars/'messages-0000.jsonl').open('ab') as f:
        f.write(b'{')
    result=snapshot(quotes,bars,tmp_path/'first')
    assert result['ignored_incomplete_tail'] is True
    (quotes/'receipt-000004.json').unlink()
    with pytest.raises(ValueError,match='SEQUENCE'):
        snapshot(quotes,bars,tmp_path/'second')


def test_bar_decision_tamper_cannot_enter_common_prefix(tmp_path,monkeypatch):
    from tools.gold_session_prefix import snapshot
    quotes,bars,_,_=inputs(tmp_path,monkeypatch)
    p=bars/'messages-0000.jsonl'
    rows=[json.loads(line) for line in p.read_text().splitlines()]
    rows[0]['decision']['close_ms']+=900000
    p.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    with pytest.raises(ValueError,match='DECISION_MISMATCH'):
        snapshot(quotes,bars,tmp_path/'snapshot')
    assert not (tmp_path/'snapshot').exists()


def test_quote_pair_with_reversed_request_clock_rejects(tmp_path,monkeypatch):
    from tools.gold_session_prefix import snapshot
    quotes,bars,_,_=inputs(tmp_path,monkeypatch)
    mark=json.loads((quotes/'receipt-000003.json').read_text())
    p=quotes/'receipt-000004.json'
    depth=json.loads(p.read_text())
    depth['requested_at']=mark['received_at']-1
    p.write_text(json.dumps(depth)+'\n')
    with pytest.raises(ValueError,match='QUOTE_CLOCK'):
        snapshot(quotes,bars,tmp_path/'snapshot')
