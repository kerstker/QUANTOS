import asyncio
from threading import Event
import pytest
from tools.gold_joint_capture import collect_joint


def test_failed_quotes_stop_stream_without_waiting_for_full_window():
    async def scenario():
        stopped=Event();closed=[]
        async def stream():
            try:await asyncio.sleep(60)
            finally:closed.append(True)
        async def quotes():
            await asyncio.sleep(0)
            raise ConnectionError('public quote failed')
        values=await asyncio.wait_for(collect_joint(stream(),quotes(),stopped),1)
        assert isinstance(values[1],ConnectionError)
        assert isinstance(values[0],asyncio.CancelledError)
        assert stopped.is_set() and closed==[True]
    asyncio.run(scenario())


def test_incomplete_stream_stops_and_drains_quote_worker():
    async def scenario():
        stopped=Event();drained=[]
        async def stream():return {'status':'INCOMPLETE','reason':'DISCONNECTED'}
        def worker():
            assert stopped.wait(1)
            drained.append(True)
            raise InterruptedError('cancelled')
        values=await asyncio.wait_for(collect_joint(stream(),asyncio.to_thread(worker),stopped),2)
        assert values[0]['reason']=='DISCONNECTED'
        assert isinstance(values[1],InterruptedError) and drained==[True]
    asyncio.run(scenario())


def test_successful_quote_capture_still_waits_for_stream():
    async def scenario():
        stopped=Event()
        async def stream():
            await asyncio.sleep(.01)
            return {'status':'OBSERVED'}
        async def quotes():return {'capture_id':'saved'}
        assert await collect_joint(stream(),quotes(),stopped)==[{'status':'OBSERVED'},{'capture_id':'saved'}]
        assert not stopped.is_set()
    asyncio.run(scenario())


def test_cancelled_public_worker_never_opens_network(monkeypatch):
    from tools import perpetual_public_capture as capture
    stopped=Event();stopped.set()
    def forbidden(*args,**kwargs):raise AssertionError('network opened after cancellation')
    monkeypatch.setattr(capture.urllib.request,'build_opener',forbidden)
    with pytest.raises(InterruptedError,match='PUBLIC_CAPTURE_CANCELLED'):
        capture.capture_public(symbol='XAUUSDT',samples=3,cancel_event=stopped)


def test_stream_cancellation_retains_incomplete_summary_and_original_messages(tmp_path,monkeypatch):
    import json,hashlib
    from tests.tools.test_gold_bar_observe import recorded
    from tools import gold_bar_observe as observer
    recorded(tmp_path,monkeypatch)
    output=tmp_path/'cancelled'
    async def scenario():
        task=asyncio.create_task(observer.observe(tmp_path/'history',output,seconds=60))
        for _ in range(100):
            path=output/'messages.jsonl'
            if path.exists() and path.stat().st_size:break
            await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(scenario())
    summary=json.loads((output/'summary.json').read_text())
    assert summary['status']=='INCOMPLETE' and summary['reason']=='STREAM_CANCELLED'
    assert summary['messages']==1
    assert summary['messages_sha256']==hashlib.sha256((output/'messages.jsonl').read_bytes()).hexdigest()


def test_operator_cancellation_drains_worker_before_returning():
    async def scenario():
        stopped=Event();started=Event();drained=Event()
        async def stream():await asyncio.sleep(60)
        def worker():
            started.set()
            assert stopped.wait(2)
            drained.set()
            raise InterruptedError('stopped')
        task=asyncio.create_task(collect_joint(stream(),asyncio.to_thread(worker),stopped))
        while not started.is_set():await asyncio.sleep(.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert drained.is_set()
    asyncio.run(scenario())
