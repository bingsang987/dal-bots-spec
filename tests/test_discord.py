"""botkit.discord.catchup — 커서는 handler 성공 뒤에만 전진, LLM 불가 시 즉시 중단, 실패는 재시도 큐."""
from __future__ import annotations

import asyncio

import pytest

from botkit.discord import CursorStore, RetryQueue, catchup
from botkit.llm import LLMUnavailable


class Msg:
    def __init__(self, mid, content, bot=False):
        self.id = mid
        self.content = content
        self.author = type("A", (), {"bot": bot})()


class Channel:
    def __init__(self, cid, msgs):
        self.id = cid
        self.name = f"ch{cid}"
        self.msgs = sorted(msgs, key=lambda m: m.id)

    async def history(self, limit=100, after=None, oldest_first=False):
        out = [m for m in self.msgs if after is None or m.id > after.id]
        if not oldest_first:
            out = list(reversed(out))
        for m in out[:limit]:
            yield m

    async def fetch_message(self, mid):
        for m in self.msgs:
            if m.id == mid:
                return m
        raise LookupError(mid)


class Client:
    def __init__(self, channels):
        self.channels = {c.id: c for c in channels}

    def get_channel(self, cid):
        return self.channels.get(cid)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def store(tmp_path):
    return CursorStore(tmp_path / "c.db"), RetryQueue(tmp_path / "retry.json", max_attempts=2)


def test_first_run_marks_latest_without_processing(store):
    cur, rq = store
    ch = Channel(1, [Msg(10, "a"), Msg(11, "b")])
    seen = []
    st = run(catchup(Client([ch]), [1], cur, seen.append, retry_queue=rq, delay=0))
    assert seen == [] and cur.get(1) == 11 and st.first_run_channels == 1


def test_cursor_advances_only_through_successes(store):
    cur, rq = store
    cur.set(1, 10)
    ch = Channel(1, [Msg(10, "old"), Msg(11, "a"), Msg(12, "b"), Msg(13, "c")])
    seen = []

    async def handler(m):
        seen.append(m.id)

    st = run(catchup(Client([ch]), [1], cur, handler, retry_queue=rq, delay=0))
    assert seen == [11, 12, 13] and cur.get(1) == 13
    assert st.processed == 3 and st.failed == 0


def test_llm_unavailable_stops_without_advancing(store):
    cur, rq = store
    cur.set(1, 10)
    cur.set(2, 20)
    ch1 = Channel(1, [Msg(11, "a"), Msg(12, "BOOM"), Msg(13, "c")])
    ch2 = Channel(2, [Msg(21, "x")])
    seen = []

    async def handler(m):
        if m.content == "BOOM":
            raise LLMUnavailable("credit", 400)
        seen.append(m.id)

    st = run(catchup(Client([ch1, ch2]), [1, 2], cur, handler, retry_queue=rq, delay=0))
    assert seen == [11]
    assert cur.get(1) == 11            # 12 에서 멈춤, 커서는 11
    assert cur.get(2) == 20            # 다음 채널은 건드리지도 않음
    assert st.llm_unavailable and st.stopped_at == ("1", "12")
    assert len(rq) == 0                # 재시도 큐가 아니라 "다음 run 재시도"

    # 다음 run: LLM 복구 → 12 부터 다시
    async def ok(m):
        seen.append(m.id)

    run(catchup(Client([ch1, ch2]), [1, 2], cur, ok, retry_queue=rq, delay=0))
    assert seen == [11, 12, 13, 21] and cur.get(1) == 13 and cur.get(2) == 21


def test_other_failure_goes_to_retry_queue_and_cursor_advances(store, tmp_path):
    cur, rq = store
    cur.set(1, 10)
    ch = Channel(1, [Msg(11, "a"), Msg(12, "BAD"), Msg(13, "c")])
    attempts = {"BAD": 0}

    async def handler(m):
        if m.content == "BAD":
            attempts["BAD"] += 1
            raise ValueError("parse fail")

    st = run(catchup(Client([ch]), [1], cur, handler, retry_queue=rq, delay=0))
    assert cur.get(1) == 13 and st.failed == 1 and st.processed == 2
    assert len(rq) == 1 and rq.items[0]["message_id"] == "12" and rq.items[0]["attempts"] == 1
    assert (tmp_path / "retry.json").exists()

    # 다음 run: 큐 먼저 재시도(실패 → attempts 2), 새 메시지 없음
    st = run(catchup(Client([ch]), [1], cur, handler, retry_queue=rq, delay=0))
    assert attempts["BAD"] == 2 and rq.items[0]["attempts"] == 2
    # 세 번째 run: max_attempts(2) 도달 → 폐기 + alert
    st = run(catchup(Client([ch]), [1], cur, handler, retry_queue=rq, delay=0))
    assert st.dropped == 1 and len(rq) == 0


def test_retry_queue_success_removes_item(store):
    cur, rq = store
    cur.set(1, 10)
    ch = Channel(1, [Msg(11, "FLAKY")])
    calls = {"n": 0}

    async def handler(m):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("once")

    run(catchup(Client([ch]), [1], cur, handler, retry_queue=rq, delay=0))
    assert len(rq) == 1
    st = run(catchup(Client([ch]), [1], cur, handler, retry_queue=rq, delay=0))
    assert st.retried_ok == 1 and len(rq) == 0


def test_skip_bots_advances_cursor_without_handler(store):
    cur, rq = store
    cur.set(1, 10)
    ch = Channel(1, [Msg(11, "bot", bot=True), Msg(12, "human")])
    seen = []
    run(catchup(Client([ch]), [1], cur, lambda m: seen.append(m.id), retry_queue=rq, delay=0, skip_bots=True))
    assert seen == [12] and cur.get(1) == 12


def test_sync_handler_supported(store):
    cur, rq = store
    cur.set(1, 10)
    ch = Channel(1, [Msg(11, "a")])
    seen = []
    run(catchup(Client([ch]), [1], cur, lambda m: seen.append(m.id), retry_queue=rq, delay=0))
    assert seen == [11]


# ---------------------------------------------------------------------------
# W8-0 .67 — run_catchup_once: on_ready 미도달 시 타임아웃
# ---------------------------------------------------------------------------
class _FakeGatewayClient:
    """discord.Client 대역. ready=False 면 start() 가 close() 까지 on_ready 없이 블록."""
    give_ready = False

    def __init__(self, intents=None):
        self._closed = None
        self.handlers = {}

    def event(self, fn):
        self.handlers[fn.__name__] = fn
        return fn

    async def __aenter__(self):
        self._closed = asyncio.Event()
        return self

    async def __aexit__(self, *a):
        await self.close()

    async def start(self, token):
        if self.give_ready:
            await self.handlers["on_ready"]()
        await self._closed.wait()

    async def close(self):
        self._closed.set()

    def get_channel(self, cid):
        return None


def test_run_catchup_once_ready_timeout_keeps_cursor(store, monkeypatch, capsys):
    import discord
    from botkit.discord import run_catchup_once
    cur, rq = store
    cur.set(1, 10)
    monkeypatch.setattr(discord, "Client", _FakeGatewayClient)
    seen = []
    st = run_catchup_once("tok", [1], cur, seen.append, retry_queue=rq, ready_timeout=0.2, delay=0)
    assert st.timed_out and "READY_TIMEOUT" in st.summary()
    assert seen == [] and cur.get(1) == 10
    assert "[HEALTH-ALERT]" in capsys.readouterr().out


def test_run_catchup_once_normal_path_still_works(store, monkeypatch):
    import discord
    from botkit.discord import run_catchup_once
    cur, rq = store
    cur.set(1, 10)
    ch = Channel(1, [Msg(11, "a")])

    class C(_FakeGatewayClient):
        give_ready = True

        def get_channel(self, cid):
            return ch

    monkeypatch.setattr(discord, "Client", C)
    seen = []
    st = run_catchup_once("tok", [1], cur, lambda m: seen.append(m.id), retry_queue=rq, delay=0)
    assert not st.timed_out and seen == [11] and cur.get(1) == 11
