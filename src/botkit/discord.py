"""Discord 채널 소급 처리(catchup) 공용 — dalbots-bmi.10.

가챠봇·QWER봇·콜라보카페봇·LCK팀봇이 각자 복붙한 "history(after=커서) → process → 커서 저장" 루프를
하나로 모은다. 핵심 차이:

  - **메시지별 handler 가 성공한 뒤에만 커서를 그 메시지로 전진**한다.
  - handler 가 `LLMUnavailable`(크레딧/인증/과부하)이나 dal.wiki 장애(`SyncAborted`/`ListFailed`) — `stop_on` —
    를 던지면 **그 자리에서 멈춘다** — 커서 전진 없음, 재시도 큐 미삽입, 남은 채널도 건너뜀, alert 1회. 다음 run 이 같은 자리부터 다시 처리한다(10-03 12메시지 영구 누락 재발 방지).
  - 그 외 예외(파싱 실패 등 메시지 1건 문제)는 **재시도 큐 파일**에 넣고 커서는 전진시킨다.
    다음 catchup 시작 시 큐를 먼저 처리하고, 성공하면 제거, `max_attempts` 초과면 alert 후 폐기.
  - `run_catchup_once()` 는 on_ready 에서 catchup 을 `try/finally: client.close()` 로 감싸 봇이
    행(hang)으로 티어를 막는 일을 없앤다. catchup 중 on_message 는 등록하지 않는다.

    from botkit.discord import CursorStore, RetryQueue, run_catchup_once

    async def handle(msg):             # 성공하면 그냥 return, 실패면 raise
        data = llm_json(...)           # LLMUnavailable 은 그대로 올라가게 둔다
        engine.sync(...)

    stats = run_catchup_once(TOKEN, CHANNEL_IDS, CursorStore("state.db"), RetryQueue("retry.json"), handle)

discord.py 는 지연 import — 이 모듈을 import 만 해도 discord 가 필요하진 않다.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Optional

from dalwiki_client.client import ListFailed

from .llm import LLMUnavailable
from .run import alert
from .sync import SyncAborted

logger = logging.getLogger("botkit.discord")

Handler = Callable[[Any], Awaitable[Any] | Any]

# handler 가 던지면 "메시지 1건 실패"가 아니라 run 전체 중단(커서 유지·재시도 큐 미삽입·alert)인 예외들.
# dal.wiki 장애(SyncAborted/ListFailed)를 메시지 실패로 세면 장애가 길 때 max_attempts 초과로 영구 누락된다(.69).
DEFAULT_STOP_ON: tuple[type[BaseException], ...] = (LLMUnavailable, SyncAborted, ListFailed)


# ---------------------------------------------------------------------------
# 커서 저장소 (sqlite)
# ---------------------------------------------------------------------------
class CursorStore:
    """channel_id → last_message_id. 봇 sqlite 를 같이 써도 된다(테이블 `discord_cursor`)."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS discord_cursor (
        channel_id TEXT PRIMARY KEY,
        last_message_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """

    def __init__(self, path: str | Path | sqlite3.Connection):
        self.conn = path if isinstance(path, sqlite3.Connection) else sqlite3.connect(str(path))
        self.conn.executescript(self._SCHEMA)
        self.conn.commit()

    def get(self, channel_id: int | str) -> Optional[int]:
        row = self.conn.execute(
            "SELECT last_message_id FROM discord_cursor WHERE channel_id=?", (str(channel_id),)
        ).fetchone()
        return int(row[0]) if row else None

    def set(self, channel_id: int | str, message_id: int | str) -> None:
        self.conn.execute(
            """INSERT INTO discord_cursor (channel_id, last_message_id, updated_at) VALUES (?,?,?)
               ON CONFLICT(channel_id) DO UPDATE SET last_message_id=excluded.last_message_id,
                                                     updated_at=excluded.updated_at""",
            (str(channel_id), str(message_id), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        )
        self.conn.commit()

    def import_legacy(self, mapping: dict) -> int:
        """기존 봇의 {channel_id: last_message_id} 를 이관(이미 있으면 유지)."""
        n = 0
        for ch, mid in mapping.items():
            if mid and self.get(ch) is None:
                self.set(ch, mid)
                n += 1
        return n


# ---------------------------------------------------------------------------
# 재시도 큐 (json 파일)
# ---------------------------------------------------------------------------
class RetryQueue:
    """handler 실패 메시지 목록. 항목: {channel_id, message_id, attempts, last_error, first_failed}."""

    def __init__(self, path: str | Path, *, max_attempts: int = 3):
        self.path = Path(path)
        self.max_attempts = max_attempts
        self.items: list[dict] = []
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8") or "[]")
                self.items = data if isinstance(data, list) else []
            except Exception as e:
                logger.warning(f"RetryQueue 로드 실패({self.path}): {e} → 빈 큐")
                self.items = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def __len__(self) -> int:
        return len(self.items)

    def push(self, channel_id: int | str, message_id: int | str, error: str) -> None:
        for it in self.items:
            if it["channel_id"] == str(channel_id) and it["message_id"] == str(message_id):
                it["attempts"] = int(it.get("attempts", 0)) + 1
                it["last_error"] = error[:500]
                self._save()
                return
        self.items.append({
            "channel_id": str(channel_id),
            "message_id": str(message_id),
            "attempts": 1,
            "last_error": error[:500],
            "first_failed": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        self._save()

    def remove(self, channel_id: int | str, message_id: int | str) -> None:
        before = len(self.items)
        self.items = [it for it in self.items
                      if not (it["channel_id"] == str(channel_id) and it["message_id"] == str(message_id))]
        if len(self.items) != before:
            self._save()

    def exhausted(self) -> list[dict]:
        return [it for it in self.items if int(it.get("attempts", 0)) >= self.max_attempts]


# ---------------------------------------------------------------------------
# catchup
# ---------------------------------------------------------------------------
@dataclass
class CatchupStats:
    channels: int = 0
    fetched: int = 0
    processed: int = 0
    failed: int = 0           # 재시도 큐로 간 메시지
    retried_ok: int = 0
    dropped: int = 0          # max_attempts 초과 폐기
    first_run_channels: int = 0
    llm_unavailable: bool = False
    timed_out: bool = False   # run_catchup_once: on_ready 미도달(ready_timeout) → catchup 생략, 커서 유지
    stopped_at: Optional[tuple[str, str]] = None   # (channel_id, message_id) 중단 예외로 멈춘 자리
    stopped_by: Optional[str] = None               # 중단 예외 이름(LLMUnavailable / SyncAborted / ListFailed …)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        s = (f"catchup channels={self.channels} fetched={self.fetched} ok={self.processed} "
             f"fail={self.failed} retried_ok={self.retried_ok} dropped={self.dropped}")
        if self.first_run_channels:
            s += f" first_run={self.first_run_channels}"
        if self.llm_unavailable:
            s += f" LLM_UNAVAILABLE stopped_at={self.stopped_at}"
        elif self.stopped_by:
            s += f" STOPPED({self.stopped_by}) stopped_at={self.stopped_at}"
        if self.timed_out:
            s += " READY_TIMEOUT"
        return s


async def _call_handler(handler: Handler, msg) -> None:
    res = handler(msg)
    if inspect.isawaitable(res):
        await res


def _stop(stats: CatchupStats, e: BaseException, ch_id, mid, where: str) -> None:
    """중단 예외 기록 + alert 한 줄."""
    stats.stopped_by = type(e).__name__
    stats.stopped_at = (str(ch_id), str(mid))
    if isinstance(e, LLMUnavailable):
        stats.llm_unavailable = True
        alert(f"LLM 불가 → catchup 중단{where} msg={mid}: {e.reason}")
    else:
        alert(f"{type(e).__name__} → catchup 중단{where} msg={mid}: {' '.join(str(e).split())[:200]}")


async def _process_retry_queue(client, queue: RetryQueue, handler: Handler, stats: CatchupStats,
                               delay: float, stop_on: tuple = DEFAULT_STOP_ON) -> bool:
    """큐 처리. 중단 예외(stop_on)로 멈추면 False."""
    for it in list(queue.items):
        ch_id, mid = int(it["channel_id"]), int(it["message_id"])
        if int(it.get("attempts", 0)) >= queue.max_attempts:
            alert(f"discord 재시도 {queue.max_attempts}회 초과 → 폐기 ch={ch_id} msg={mid}: {it.get('last_error', '')[:120]}")
            queue.remove(ch_id, mid)
            stats.dropped += 1
            continue
        channel = client.get_channel(ch_id)
        if channel is None:
            try:
                channel = await client.fetch_channel(ch_id)
            except Exception as e:
                queue.push(ch_id, mid, f"채널 조회 실패: {e!r}")
                continue
        try:
            msg = await channel.fetch_message(mid)
        except Exception as e:
            name = type(e).__name__
            if name in ("NotFound",):
                logger.info(f"재시도 메시지 삭제됨 ch={ch_id} msg={mid} → 큐에서 제거")
                queue.remove(ch_id, mid)
                continue
            queue.push(ch_id, mid, f"메시지 조회 실패: {e!r}")
            continue
        try:
            await _call_handler(handler, msg)
            queue.remove(ch_id, mid)
            stats.retried_ok += 1
        except stop_on as e:
            _stop(stats, e, ch_id, mid, f"(재시도 큐 처리 중) ch={ch_id}")
            return False
        except Exception as e:
            queue.push(ch_id, mid, repr(e))
            logger.warning(f"재시도 실패 ch={ch_id} msg={mid}: {e!r}")
        if delay:
            await asyncio.sleep(delay)
    return True


async def catchup(
    client,
    channel_ids: Iterable[int],
    cursors: CursorStore,
    handler: Handler,
    *,
    retry_queue: Optional[RetryQueue] = None,
    limit: int = 200,
    delay: float = 1.0,
    skip_bots: bool = False,
    first_run: str = "mark_latest",
    stop_on: tuple[type[BaseException], ...] = DEFAULT_STOP_ON,
) -> CatchupStats:
    """각 채널의 커서 이후 메시지를 오래된 것부터 handler 에 넘긴다.

    Args:
        client: 로그인된 discord.Client.
        channel_ids: 처리할 채널 id 들(순서대로).
        cursors: CursorStore. 채널 커서가 없으면 `first_run` 정책.
        handler: `await handler(msg)` 또는 동기 함수. 성공=return, 실패=raise.
            LLMUnavailable → 즉시 중단(커서 그대로). 그 외 예외 → 재시도 큐 + 커서 전진.
        retry_queue: None 이면 실패 메시지는 로그만 남기고 커서 전진(큐 없음 — 권장하지 않음).
        limit: 채널당 최대 fetch.
        delay: 메시지 간 대기(초). Discord rate limit 완화.
        skip_bots: True 면 msg.author.bot 메시지는 handler 호출 없이 커서만 전진.
        first_run: 커서 없을 때 "mark_latest"(최신 메시지를 커서로 저장만, gachabot 방식) |
            "process_recent"(limit 건 처리).
        stop_on: handler 가 던지면 run 전체 중단(커서 유지·재시도 큐 미삽입·alert)할 예외 집합.
            기본 (LLMUnavailable, SyncAborted, ListFailed). LLMUnavailable 은 항상 중단으로 다룬다.
    """
    stop_on = tuple(stop_on) if LLMUnavailable in tuple(stop_on) else (LLMUnavailable, *stop_on)
    stats = CatchupStats()
    if retry_queue is not None:
        ok = await _process_retry_queue(client, retry_queue, handler, stats, delay, stop_on)
        if not ok:
            return stats

    import discord  # 지연 import

    for ch_id in channel_ids:
        stats.channels += 1
        channel = client.get_channel(ch_id)
        if channel is None:
            try:
                channel = await client.fetch_channel(ch_id)
            except Exception as e:
                stats.errors.append(f"channel {ch_id}: {e!r}")
                logger.warning(f"채널 {ch_id} 조회 실패: {e!r}")
                continue
        name = getattr(channel, "name", str(ch_id))
        last_id = cursors.get(ch_id)

        if last_id is None and first_run == "mark_latest":
            stats.first_run_channels += 1
            async for m in channel.history(limit=1):
                cursors.set(ch_id, m.id)
                logger.info(f"#{name} 첫 실행 — 최신 메시지 {m.id} 를 커서로 저장(소급 없음)")
            continue

        kwargs: dict[str, Any] = {"limit": limit, "oldest_first": True}
        if last_id is not None:
            kwargs["after"] = discord.Object(id=last_id)
        msgs = [m async for m in channel.history(**kwargs)]
        stats.fetched += len(msgs)
        if not msgs:
            logger.info(f"#{name} 새 메시지 없음")
            continue
        logger.info(f"#{name} 새 메시지 {len(msgs)}건 처리")

        for m in msgs:
            if skip_bots and getattr(getattr(m, "author", None), "bot", False):
                cursors.set(ch_id, m.id)
                continue
            try:
                await _call_handler(handler, m)
            except stop_on as e:
                _stop(stats, e, ch_id, m.id, f", 커서 유지 #{name}")
                logger.info(stats.summary())
                return stats
            except Exception as e:
                stats.failed += 1
                stats.errors.append(f"{ch_id}/{m.id}: {e!r}")
                logger.warning(f"#{name} msg={m.id} 처리 실패 → 재시도 큐: {e!r}")
                if retry_queue is not None:
                    retry_queue.push(ch_id, m.id, repr(e))
            else:
                stats.processed += 1
            # 성공 또는 '재시도 큐로 격리' 뒤에만 커서 전진
            cursors.set(ch_id, m.id)
            if delay:
                await asyncio.sleep(delay)

    logger.info(stats.summary())
    return stats


def run_catchup_once(
    token: str,
    channel_ids: Iterable[int],
    cursors: CursorStore,
    handler: Handler,
    *,
    retry_queue: Optional[RetryQueue] = None,
    intents=None,
    ready_timeout: Optional[float] = 300,
    **catchup_kwargs,
) -> CatchupStats:
    """discord.Client 를 띄워 on_ready 에서 catchup 1회 → 반드시 close → 통계 반환(블로킹).

    on_message 는 등록하지 않는다(catchup 모드에선 커서 기반 처리가 유일한 경로).
    ready_timeout 초(기본 300, None 이면 무한) 안에 on_ready 가 안 오면 close + alert +
    `CatchupStats.timed_out=True` 로 돌아온다 — 커서·큐는 그대로(다음 run 이 같은 자리부터). 게이트웨이가
    on_ready 를 안 줘서 티어 전체가 멈추는 일을 막는다(.67).
    """
    import discord

    if intents is None:
        intents = discord.Intents.default()
        intents.message_content = True
    client = discord.Client(intents=intents)
    result: dict[str, Any] = {}
    channel_ids = list(channel_ids)
    ready = asyncio.Event()

    @client.event
    async def on_ready():
        if ready.is_set() or result.get("timed_out"):
            return            # 재연결 시 on_ready 재호출 / 타임아웃 뒤 늦게 온 ready → 무시
        ready.set()
        try:
            result["stats"] = await catchup(
                client, channel_ids, cursors, handler, retry_queue=retry_queue, **catchup_kwargs
            )
        except Exception as e:
            result["error"] = e
            logger.exception("catchup 예외")
        finally:
            await client.close()

    async def runner():
        async with client:
            start = asyncio.ensure_future(client.start(token))
            waiter = asyncio.ensure_future(ready.wait())
            done, _ = await asyncio.wait({start, waiter}, timeout=ready_timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
            if not done:
                result["timed_out"] = True
                await client.close()
            waiter.cancel()
            try:
                await start
            except asyncio.CancelledError:
                pass

    asyncio.run(runner())
    if result.get("timed_out"):
        alert(f"discord on_ready {ready_timeout}s 미도달 → close, catchup 생략(커서 유지)")
        return CatchupStats(timed_out=True)
    if "error" in result:
        raise result["error"]
    return result.get("stats", CatchupStats())
