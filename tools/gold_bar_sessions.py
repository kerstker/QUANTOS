"""Own a bounded public continuation task across protected worker frames."""
import asyncio
from pathlib import Path
import threading
import time

from live.execution.demo_batch_risk import _digest
from tools.gold_bar_episode_tail import EpisodeTail, observe


class Observer:
    def __init__(self, tail):
        self.tail = tail
        self.ready = threading.Event()
        self.done = threading.Event()
        self.loop = self.task = None
        self.fatal = None
        self.thread = threading.Thread(target=self._run, name='gold-bar-continuation', daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            raise ValueError('BAR_CONTINUATION_START_TIMEOUT')

    def _run(self):
        async def run():
            self.loop = asyncio.get_running_loop()
            self.task = asyncio.current_task()
            self.ready.set()
            try:
                await observe(self.tail, seconds=86400)
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                try:
                    if not self.tail.engine._cursor.blocked:
                        self.tail.gap(received_at_ms=max(time.time_ns()//1000000,
                            self.tail.engine._cursor.last_received), reason=type(exc).__name__)
                except Exception:
                    self.fatal = 'BAR_CONTINUATION_FAILURE_NOT_RETAINED'
        try:
            asyncio.run(run())
        except BaseException:
            self.fatal = 'BAR_CONTINUATION_THREAD_FAILED'
        finally:
            self.ready.set()
            self.done.set()

    def snapshot(self):
        if self.fatal:
            raise ValueError(self.fatal)
        return self.tail.snapshot()

    def close(self):
        if not self.done.is_set() and self.loop is not None:
            try:
                self.loop.call_soon_threadsafe(self.task.cancel)
            except RuntimeError:
                if not self.done.wait(1):
                    raise
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise ValueError('BAR_CONTINUATION_STOP_TIMEOUT')


class BarSessions:
    def __init__(self):
        self.key = self.episode = self.observer = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self.observer is not None:
            self.observer.close()
        self.key = self.episode = self.observer = None

    def frame(self, root, bundle, *, source_commit, uid, account):
        from tools.gold_bar_fresh_inputs import FreshEpisode
        key = (str(Path(root).absolute()), _digest(bundle), source_commit, uid, account)
        if self.key != key:
            self.close()
            episode = FreshEpisode(root, bundle, source_commit=source_commit, uid=uid, cash_account=account)
            tail = EpisodeTail(root, bundle_sha256=_digest(bundle), engine=episode.engine,
                entry_decision_ms=episode.entry_decision_ms)
            observer = Observer(tail)
            self.episode, self.observer, self.key = episode, observer, key
        episode = self.episode
        original = episode.recheck()
        engine, prefix, verify_prefix = self.observer.snapshot()
        checked = dict(original, continuation_sha256=prefix)
        def recheck():
            return dict(episode.recheck(), continuation_sha256=verify_prefix())
        if recheck() != checked:
            raise ValueError('BAR_CONTINUATION_FRAME_CHANGED')
        return engine, checked, episode.entry_decision_ms, recheck
