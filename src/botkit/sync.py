"""SyncEngine — "소스가 말하는 이벤트 목록"을 dal.wiki 토픽 하나와 동기화한다.

봇은 소스를 긁어 `Event` 리스트를 만들고 `SyncEngine.sync(events)` 한 번만 호출한다.
생성/수정/삭제/404 복구/상태 저장은 전부 여기서 처리한다.

    engine = SyncEngine(topic_id=TOPIC, state_path="state.db", absent="delete")
    stats = engine.sync(events, source_complete=all_sources_ok)
    log.info(stats.summary())

동작 (계획서 §2.2):
  1. TopicCache 로드(49일 청크). 어느 청크든 실패(ListFailed)면 **SyncAborted** → 이번 run 쓰기 전면 중단.
  2. key 별로
       NEW       state 에 없음 → (같은 summary+start 가 토픽에 이미 있으면 그 id 를 입양해 update)
                 → 없으면 create
       CHANGED   content_hash 다름 → update. 404 면 캐시에서 (summary,start) 로 복구 → 그래도 없으면 create.
                 단 state id 가 토픽 윈도우 안에 없으면(사람 삭제) 같은 일정의 다른 id 로 복구를 먼저 시도하고,
                 없으면 UNCHANGED 와 똑같이 on_missing 정책(ignore 면 missing+1, PATCH·create 없음).
     입양·404 복구는 이번 run 소스에 있는 다른 key 가 이미 쥔 id 를 건너뛴다(두 key 가 한 이벤트를 가리키지 않게).
     adopt_scope="category" 면 후보를 Event.category_id 가 같은 이벤트로 더 좁힌다(공유 토픽에서 카테고리마다
     같은 (summary,start) 가 있을 때 남의 것을 덮지 않게).
       UNCHANGED hash 같음 → skip
  3. **쓰기 성공 후에만 state 저장**(건별 커밋). 실패하면 state 그대로 → 다음 run 재시도.
  4. 소스에서 사라진 key(absent): 정책 keep | delete(기본). 단
       - 이번 run 소스가 0건이거나
       - source_complete=False(소스 일부 실패)
       - absent 후보가 한도(max_absent 또는 max(10, max_absent_ratio × 범위 행))를 넘음("mass_absent")
     면 absent 처리 전체 스킵(stats.absent_skipped 에 사유). 스킵되거나 삭제가 있으면 alert 한 줄(alert_absent).
     `source_window=(from, to)` 를 주면 그 범위 안의 state 행만 absent 후보(과거 이벤트 보호).
     absent 행의 id 를 이번 run 다른 key 가 쥐었으면(소스가 같은 일정을 새 key 로 재발급 → 입양) DELETE 없이
     state 행만 지운다(ABSENT_RELINKED, stats.relinked).
  5. SyncStats 반환. 실패가 있으면 호출자(또는 run 래퍼)가 alert.

dry-run: dalwiki_client 가 DRY_RUN 이면 쓰기를 가짜 처리하므로 여기선 **state 를 저장하지 않는다**
(가짜 id 가 state 에 박히면 다음 라이브 run 에서 전부 404).
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Literal, Optional

from dalwiki_client import client as dw
from dalwiki_client.client import ListFailed, TopicCache, _iso_z

logger = logging.getLogger("botkit.sync")

AbsentPolicy = Literal["keep", "delete"]
MissingPolicy = Literal["ignore", "recreate", "forget"]
AdoptScope = Literal["topic", "category"]


class SyncAborted(Exception):
    """TopicCache 로드 실패 등으로 이번 run 의 쓰기를 전면 중단했다. 호출자는 [HEALTH-ALERT]."""


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------
@dataclass
class Event:
    """봇이 소스에서 만들어 넘기는 이벤트 한 건.

    key: 봇이 정의하는 안정적인 자연 키(소스 id, 'YYYY-MM-DD_팀_상대' 등). 같은 일정은 run 이 바뀌어도
         같은 key 여야 한다 — 이게 바뀌면 새 이벤트로 취급된다.
    start/end: aware datetime | Unix ms | ISO 문자열. 종일이면 그날 00:00~23:59:59 KST
         (start==end 종일 이벤트는 월간 뷰에서 빠진다).
    category_id: 항상 명시. summary 에 '[카테고리]' prefix 금지(UI 가 칩으로 붙인다 → 이중표시).
    description: 링크는 target="_blank" rel="noopener noreferrer". 출처 표기 금지(link 칸만).
    """
    key: str
    summary: str
    start: object
    end: object
    all_day: bool = False
    category_id: Optional[str] = None
    description: Optional[str] = None
    link: Optional[str] = None
    location: Optional[str] = None
    start_timezone: str = "Asia/Seoul"
    end_timezone: str = "Asia/Seoul"
    rrule: Optional[str] = None

    def start_iso(self) -> str:
        return _iso_z(self.start) or ""

    def end_iso(self) -> str:
        return _iso_z(self.end) or ""

    def content_hash(self) -> str:
        """dal.wiki 에 실제로 보내는 필드만으로 해시. 가드(summary 클립 등)까지 반영해
        서버에 보낸 것과 비교 기준이 같게 한다."""
        payload = {
            "summary": dw._guard_summary(self.summary),
            "start": self.start_iso(),
            "end": self.end_iso(),
            "all_day": bool(self.all_day),
            "category_id": self.category_id or "",
            "description": dw._guard_description(self.description) or "",
            "link": self.link or "",
            "location": dw._guard_location(self.location) or "",
            "start_timezone": self.start_timezone,
            "end_timezone": self.end_timezone,
            "rrule": self.rrule or "",
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def write_kwargs(self) -> dict:
        """create_event/update_event 에 넘길 공통 kwargs."""
        return dict(
            summary=self.summary,
            start=self.start,
            end=self.end,
            all_day=self.all_day,
            start_timezone=self.start_timezone,
            end_timezone=self.end_timezone,
            description=self.description,
            link=self.link,
            location=self.location,
            category_id=self.category_id,
            rrule=self.rrule,
        )


# ---------------------------------------------------------------------------
# SyncState (sqlite)
# ---------------------------------------------------------------------------
@dataclass
class StateRow:
    key: str
    event_id: str
    content_hash: str
    summary: str
    start_iso: str
    first_seen: str
    last_seen: str


class SyncState:
    """key → (event_id, content_hash) sqlite 저장소. 토픽별로 행이 나뉜다(한 DB 를 여러 토픽이 공유 가능)."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS sync_state (
        topic_id     TEXT NOT NULL,
        key          TEXT NOT NULL,
        event_id     TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        summary      TEXT,
        start_iso    TEXT,
        first_seen   TEXT NOT NULL,
        last_seen    TEXT NOT NULL,
        PRIMARY KEY (topic_id, key)
    );
    CREATE INDEX IF NOT EXISTS idx_sync_state_start ON sync_state(topic_id, start_iso);
    """

    def __init__(self, path: str | Path, topic_id: str):
        self.path = str(path)
        self.topic_id = topic_id
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(self._SCHEMA)
        self.conn.commit()
        self.in_memory = False

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def get(self, key: str) -> Optional[StateRow]:
        row = self.conn.execute(
            "SELECT key, event_id, content_hash, summary, start_iso, first_seen, last_seen "
            "FROM sync_state WHERE topic_id=? AND key=?",
            (self.topic_id, key),
        ).fetchone()
        return StateRow(*row) if row else None

    def put(self, key: str, event_id: str, content_hash: str, summary: str, start_iso: str) -> None:
        """쓰기 성공 직후 호출. 건별 커밋."""
        now = self._now()
        self.conn.execute(
            """INSERT INTO sync_state (topic_id, key, event_id, content_hash, summary, start_iso, first_seen, last_seen)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(topic_id, key) DO UPDATE SET
                 event_id=excluded.event_id, content_hash=excluded.content_hash,
                 summary=excluded.summary, start_iso=excluded.start_iso, last_seen=excluded.last_seen""",
            (self.topic_id, key, event_id, content_hash, summary, start_iso, now, now),
        )
        self.conn.commit()

    def touch(self, keys: Iterable[str]) -> None:
        keys = list(keys)
        if not keys:
            return
        now = self._now()
        self.conn.executemany(
            "UPDATE sync_state SET last_seen=? WHERE topic_id=? AND key=?",
            [(now, self.topic_id, k) for k in keys],
        )
        self.conn.commit()

    def delete(self, key: str) -> None:
        self.conn.execute("DELETE FROM sync_state WHERE topic_id=? AND key=?", (self.topic_id, key))
        self.conn.commit()

    def all(self) -> dict[str, StateRow]:
        rows = self.conn.execute(
            "SELECT key, event_id, content_hash, summary, start_iso, first_seen, last_seen "
            "FROM sync_state WHERE topic_id=?",
            (self.topic_id,),
        ).fetchall()
        return {r[0]: StateRow(*r) for r in rows}

    def forget_before(self, before) -> int:
        """start 가 before 이전인 행을 state 에서만 제거(dal.wiki 는 건드리지 않음). state 비대 방지용."""
        iso = _iso_z(before)
        cur = self.conn.execute(
            "DELETE FROM sync_state WHERE topic_id=? AND start_iso IS NOT NULL AND start_iso != '' AND start_iso < ?",
            (self.topic_id, iso),
        )
        self.conn.commit()
        return cur.rowcount

    def to_memory(self) -> None:
        """이 state 를 메모리 사본으로 바꾼다(파일은 그대로). 이후 쓰기는 이 프로세스에서만 보인다 — dry-run 용."""
        if self.in_memory:
            return
        mem = sqlite3.connect(":memory:")
        self.conn.backup(mem)
        self.conn.close()
        self.conn = mem
        self.in_memory = True
        logger.info(f"SyncState[{self.topic_id}] dry-run → 메모리 사본으로 전환({self.path} 는 변경 없음)")

    def import_legacy(self, rows: Iterable[tuple[str, str]], summary_start: Optional[dict] = None,
                      *, dry_run: Optional[bool] = None) -> int:
        """기존 봇 DB 의 (key, event_id) 를 이관. content_hash 는 빈 값 → 첫 run 에서 CHANGED 로 1회 PATCH 된다
        (서버 상태를 모르는데 hash 를 지어내면 안 됨).

        dry_run: None 이면 dalwiki_client 의 DRY_RUN 판정. dry-run 이면 메모리 사본(`to_memory`)에만 넣고
            파일은 건드리지 않는다 — 같은 프로세스의 dry-run sync 는 이관된 행을 보지만 다음 실제 run 엔 남지 않는다.
        """
        if dw._is_dry_run() if dry_run is None else dry_run:
            self.to_memory()
        n = 0
        now = self._now()
        for key, eid in rows:
            if not key or not eid:
                continue
            s, st = ("", "")
            if summary_start and key in summary_start:
                s, st = summary_start[key]
            self.conn.execute(
                """INSERT OR IGNORE INTO sync_state
                   (topic_id, key, event_id, content_hash, summary, start_iso, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (self.topic_id, key, eid, "", s, st, now, now),
            )
            n += 1
        self.conn.commit()
        return n

    def close(self) -> None:
        self.conn.close()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
@dataclass
class SyncStats:
    topic_id: str
    source_total: int = 0
    created: int = 0
    adopted: int = 0          # NEW 인데 토픽에 같은 (summary,start) 가 있어 그 id 를 입양(update)
    updated: int = 0
    unchanged: int = 0
    recovered: int = 0        # update 404 → 캐시로 id 복구
    deleted: int = 0          # absent 정책 delete 로 지움
    relinked: int = 0         # absent 행의 id 를 이번 run 다른 key 가 쥠 → DELETE 없이 state 행만 제거
    absent_kept: int = 0
    absent_skipped: Optional[str] = None   # absent 처리 전체 스킵 사유
    missing: int = 0          # state 엔 있는데 토픽 윈도우 안에 id 가 없음(사람/타봇 삭제)
    protected: int = 0        # 사람이 편집한 이벤트 → PATCH 보호
    failed: int = 0
    failures: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def writes(self) -> int:
        return self.created + self.adopted + self.updated + self.deleted

    def ok(self) -> bool:
        return self.failed == 0

    def summary(self) -> str:
        parts = [
            f"source={self.source_total}",
            f"create={self.created}",
            f"update={self.updated}",
            f"unchanged={self.unchanged}",
        ]
        if self.adopted:
            parts.append(f"adopt={self.adopted}")
        if self.recovered:
            parts.append(f"recover404={self.recovered}")
        if self.deleted:
            parts.append(f"delete={self.deleted}")
        if self.relinked:
            parts.append(f"relinked={self.relinked}")
        if self.absent_kept:
            parts.append(f"absent_keep={self.absent_kept}")
        if self.absent_skipped:
            parts.append(f"absent_skipped({self.absent_skipped})")
        if self.missing:
            parts.append(f"missing={self.missing}")
        if self.protected:
            parts.append(f"protected={self.protected}")
        parts.append(f"fail={self.failed}")
        tag = "[DRY-RUN] " if self.dry_run else ""
        return f"{tag}sync[{self.topic_id}] " + " ".join(parts)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class SyncEngine:
    def __init__(
        self,
        topic_id: str,
        state_path: str | Path,
        *,
        absent: AbsentPolicy = "delete",
        on_missing: MissingPolicy = "ignore",
        cache_days_back: int = 60,
        cache_days_forward: int = 400,
        cache_chunk_days: int = 49,
        protect_human_edits: bool = False,
        dry_run: Optional[bool] = None,
        state: Optional[SyncState] = None,
        log: Optional[logging.Logger] = None,
        adopt_scope: AdoptScope = "topic",
        cache: Optional[TopicCache] = None,
        max_absent_ratio: Optional[float] = 0.3,
        max_absent: Optional[int] = None,
        alert_absent: bool = True,
    ):
        """
        Args:
            topic_id: 대상 토픽.
            state_path: sqlite 파일(봇 폴더). `state` 를 직접 넘기면 무시.
            absent: 소스에서 사라진 key 처리. "delete"(기본) | "keep".
            on_missing: state 에 있는데 토픽에서 사라진 이벤트(사람/타봇 삭제).
                "ignore"(기본, 사람이 지운 자리 재생성 금지) | "recreate" | "forget"(state 에서만 제거).
            cache_days_back/forward/chunk_days: TopicCache 윈도우. 윈도우 밖 이벤트는 missing 판정 안 함.
            protect_human_edits: True 면 PATCH 전 histories 로 사람 UPDATE 유무 확인(호출 비용 有).
            dry_run: None 이면 dalwiki_client 의 DRY_RUN 판정을 따른다.
            adopt_scope: NEW 입양·404 복구 후보 범위. "topic"(기본, 토픽 전체 (summary,start)) |
                "category"(Event.category_id 가 같은 것만 + 다른 key 가 쥔 id 제외). 한 토픽에 여러
                카테고리 엔진을 돌리거나 카테고리마다 같은 제목·시각 이벤트가 흔하면 "category".
            cache: 미리 strict 로드한 TopicCache. 주면 sync() 에서 다시 로드하지 않고 그걸 쓴다(같은 토픽
                엔진 여러 개가 캐시 하나 공유). 로드 실패 처리는 호출자 몫. None 이면 매 sync() 마다 로드.
            max_absent_ratio / max_absent: 대량 absent 가드(.66). absent 후보가 한도를 넘으면 삭제 전체 보류
                (`absent_skipped="mass_absent"`). 한도 = max_absent(주면 그 값) 또는 max(10, ratio × 범위 행 수).
                범위 행 = source_window·absent_filter 를 통과한 state 행(소스에 있는 것 포함). 둘 다 None 이면 끔.
                파서가 반쯤 깨져 소스가 일부만 돌려줄 때(0건도 예외도 아님) 대량 오삭제를 막는다.
            alert_absent: True(기본)면 absent 가 보류되거나(absent_skipped) 삭제가 1건 이상일 때
                `botkit.run.alert` 한 줄([HEALTH-ALERT]). 봇이 따로 올리지 않던 신호를 엔진이 낸다.
        """
        if absent not in ("keep", "delete"):
            raise ValueError(f"absent 정책은 keep|delete: {absent!r}")
        if on_missing not in ("ignore", "recreate", "forget"):
            raise ValueError(f"on_missing 정책은 ignore|recreate|forget: {on_missing!r}")
        if adopt_scope not in ("topic", "category"):
            raise ValueError(f"adopt_scope 는 topic|category: {adopt_scope!r}")
        self.topic_id = topic_id
        self.absent = absent
        self.on_missing = on_missing
        self.cache_days_back = cache_days_back
        self.cache_days_forward = cache_days_forward
        self.cache_chunk_days = cache_chunk_days
        self.protect_human_edits = protect_human_edits
        self.adopt_scope = adopt_scope
        self.max_absent_ratio = max_absent_ratio
        self.max_absent = max_absent
        self.alert_absent = alert_absent
        self._dry_run = dry_run
        self.state = state or SyncState(state_path, topic_id)
        self.log = log or logger
        self._shared_cache = cache
        self.cache: Optional[TopicCache] = None
        self._claimed: set[str] = set()   # 이번 run 소스 key 가 쥔 event id (입양 제외·ABSENT_RELINKED 판정)

    # -- helpers ---------------------------------------------------------
    @property
    def dry_run(self) -> bool:
        return dw._is_dry_run() if self._dry_run is None else self._dry_run

    def _load_cache(self) -> TopicCache:
        if self._shared_cache is not None:
            self.cache = self._shared_cache
            return self.cache
        cache = TopicCache(self.topic_id)
        try:
            cache.load(
                days_back=self.cache_days_back,
                days_forward=self.cache_days_forward,
                chunk_days=self.cache_chunk_days,
                strict=True,
            )
        except ListFailed as e:
            raise SyncAborted(f"TopicCache 로드 실패 → 이번 run 쓰기 전면 중단: {e}") from e
        self.cache = cache
        return cache

    def _lookup(self, ev: Event) -> Optional[str]:
        """입양·404 복구 후보 id. adopt_scope 에 따라 토픽 전체 / 같은 카테고리(+미점유)."""
        if self.cache is None:
            return None
        summary, start = dw._guard_summary(ev.summary), ev.start_iso()
        # 두 scope 모두 이번 run 소스에 살아 있는 key 가 쥔 id 는 건너뛴다(.104/.68). 소스에서 사라진 key 의
        # id 는 입양 가능 — 같은 일정을 새 key 로 재발급한 경우이고, 옛 key 는 absent 에서 ABSENT_RELINKED.
        if self.adopt_scope == "category":
            return self.cache.lookup(summary, start, category_id=ev.category_id, exclude=self._claimed)
        cands = self.cache.lookup_all(summary, start, exclude=self._claimed)
        return cands[-1] if cands else None   # 구 동작 유지: 같은 키 중 마지막 것

    def _save(self, ev: Event, event_id: str, h: str) -> None:
        self._claimed.add(event_id)
        if self.dry_run:
            return
        self.state.put(ev.key, event_id, h, dw._guard_summary(ev.summary), ev.start_iso())

    def _fail(self, stats: SyncStats, msg: str) -> None:
        stats.failed += 1
        stats.failures.append(msg)
        self.log.warning(f"sync fail: {msg}")

    def _human_edited(self, event_id: str) -> bool:
        if not self.protect_human_edits:
            return False
        try:
            from dalwiki_client.histories import was_human_edited
            return was_human_edited(self.topic_id, event_id)
        except Exception as e:  # histories 실패는 보호 안 함(경고만)
            self.log.warning(f"was_human_edited({event_id}) 실패: {e}")
            return False

    # -- write primitives ----------------------------------------------
    def _create(self, ev: Event, stats: SyncStats, h: str, *, counter: str = "created") -> Optional[str]:
        new_id = dw.create_event(topic_id=self.topic_id, **ev.write_kwargs())
        if not new_id:
            self._fail(stats, f"create 실패 key={ev.key} {ev.summary!r}")
            return None
        self._save(ev, new_id, h)
        if self.cache is not None:
            self.cache.add(dw._guard_summary(ev.summary), ev.start_iso(), new_id, ev.category_id)
        setattr(stats, counter, getattr(stats, counter) + 1)
        self.log.info(f"CREATE {ev.key} {ev.summary!r} → {new_id}")
        return new_id

    def _update(self, ev: Event, event_id: str, stats: SyncStats, h: str, *, counter: str = "updated") -> bool:
        """update → 404 면 캐시 복구 → 그래도 없으면 create. 성공 시 True."""
        if self._human_edited(event_id):
            stats.protected += 1
            self.log.info(f"PROTECTED(사람 편집) {ev.key} {event_id} → PATCH 생략")
            return True
        ok, sc = dw.update_event(event_id, **ev.write_kwargs())
        if ok:
            self._save(ev, event_id, h)
            setattr(stats, counter, getattr(stats, counter) + 1)
            self.log.info(f"UPDATE {ev.key} {event_id}")
            return True
        if sc != 404:
            self._fail(stats, f"update 실패 key={ev.key} id={event_id} status={sc}")
            return False
        # 404 → 캐시 복구
        alt = self._lookup(ev)
        if alt and alt != event_id:
            ok2, sc2 = dw.update_event(alt, **ev.write_kwargs())
            if ok2:
                self._save(ev, alt, h)
                stats.recovered += 1
                setattr(stats, counter, getattr(stats, counter) + 1)
                self.log.info(f"RECOVER404 {ev.key} {event_id} → {alt}")
                return True
            self._fail(stats, f"update(복구 id) 실패 key={ev.key} id={alt} status={sc2}")
            return False
        self.log.warning(f"UPDATE 404 key={ev.key} id={event_id}, 캐시 미스 → create")
        return self._create(ev, stats, h) is not None

    def _missing(self, ev: Event, row: StateRow, stats: SyncStats, h: str) -> None:
        """state 엔 있는데 토픽(윈도우 안)에서 id 가 사라짐 → on_missing 정책."""
        stats.missing += 1
        if self.on_missing == "recreate":
            self.log.warning(f"MISSING {ev.key} {row.event_id} → recreate")
            self._create(ev, stats, h)
        elif self.on_missing == "forget":
            self.log.warning(f"MISSING {ev.key} {row.event_id} → state 에서 제거")
            if not self.dry_run:
                self.state.delete(ev.key)
        else:
            self.log.info(f"MISSING {ev.key} {row.event_id} (ignore — 사람 삭제 존중)")

    def _recover_changed(self, ev: Event, row: StateRow, stats: SyncStats, h: str) -> bool:
        """CHANGED 인데 state id 가 토픽에 없음 → 같은 일정이 다른 id 로 살아 있으면(옛 v1 id·이관 행) 그 id 로
        PATCH(RECOVER404 와 같은 의미). 새 내용·옛 (summary,start) 순으로 찾는다. 못 찾으면 False → on_missing."""
        alt = self._lookup(ev)
        if not alt and row.summary and self.cache is not None:
            cands = self.cache.lookup_all(row.summary, row.start_iso, exclude=self._claimed)
            if self.adopt_scope == "category":
                cands = [c for c in cands if (self.cache.category_of(c) or None) == (ev.category_id or None)]
            alt = cands[-1] if cands else None
        if not alt or alt == row.event_id:
            return False
        if self._human_edited(alt):
            stats.protected += 1
            self.log.info(f"PROTECTED(사람 편집) {ev.key} {alt} → PATCH 생략")
            return True
        ok, sc = dw.update_event(alt, **ev.write_kwargs())
        if not ok:
            self._fail(stats, f"update(복구 id) 실패 key={ev.key} id={alt} status={sc}")
            return True
        self._save(ev, alt, h)
        stats.recovered += 1
        stats.updated += 1
        self.log.info(f"RECOVER {ev.key} {row.event_id} → {alt} (state id 가 토픽에 없음)")
        return True

    def _delete(self, key: str, row: StateRow, stats: SyncStats) -> None:
        if row.event_id in self._claimed:
            # 이 id 는 이번 run 소스에 있는 다른 key 가 쥐고 있다(재발급 입양 등) → 이벤트는 남기고 옛 행만 제거
            if not self.dry_run:
                self.state.delete(key)
            stats.relinked += 1
            self.log.info(f"ABSENT_RELINKED {key} {row.event_id} {row.summary!r} → state 행만 제거(DELETE 없음)")
            return
        ok, sc = dw.delete_event_status(row.event_id)
        # 404 = 이미 없는 이벤트 → 삭제된 걸로 간주(start_iso 없는 이관 행도, .70). 그 외 실패는 캐시로 보조 판정.
        gone = ok or sc == 404
        if not gone and self.cache is not None and not self.cache.has_id(row.event_id) \
                and row.start_iso and self.cache.in_window(row.start_iso):
            gone = True
        if not gone:
            self._fail(stats, f"delete 실패 key={key} id={row.event_id} status={sc}")
            return
        if not self.dry_run:
            self.state.delete(key)
        if self.cache is not None:
            self.cache.discard(row.event_id)
        stats.deleted += 1
        self.log.info(f"DELETE(absent) {key} {row.event_id} {row.summary!r}")

    # -- main -----------------------------------------------------------
    def sync(
        self,
        events: Iterable[Event],
        *,
        source_complete: bool = True,
        source_window: Optional[tuple[object, object]] = None,
        absent_filter: Optional[Callable[[StateRow], bool]] = None,
    ) -> SyncStats:
        """소스 이벤트 목록을 토픽과 동기화하고 통계를 돌려준다.

        Args:
            events: 이번 run 소스가 말하는 전체(또는 window 범위) 이벤트. key 중복이면 ValueError.
            source_complete: 소스 일부라도 실패했으면 False → absent 처리 전체 스킵.
            source_window: (from, to). 주면 그 범위 안 start 를 가진 state 행만 absent 후보.
                봇이 "앞으로 N일"만 긁는다면 꼭 줘야 한다(없으면 과거 이벤트가 전부 absent 로 지워진다).
            absent_filter: StateRow → bool. True 인 행만 absent 후보(카테고리 한정 등 추가 조건).

        Raises:
            SyncAborted: TopicCache 로드 실패. 쓰기 0건.
        """
        events = list(events)
        stats = SyncStats(topic_id=self.topic_id, source_total=len(events), dry_run=self.dry_run)

        keys = [e.key for e in events]
        dup = {k for k in keys if keys.count(k) > 1}
        if dup:
            raise ValueError(f"Event.key 중복: {sorted(dup)[:5]}")

        cache = self._load_cache()
        existing = self.state.all()
        source_keys = set(keys)
        # 이번 run 소스에 있는 key 가 쥔 id + 이번 run _save 된 id. 입양 제외·absent 재연결 판정에 쓴다.
        self._claimed = {r.event_id for k, r in existing.items() if k in source_keys}
        unchanged_keys: list[str] = []

        for ev in events:
            h = ev.content_hash()
            row = existing.get(ev.key)
            try:
                if row is None:
                    # NEW — 토픽에 같은 (summary, start) 가 이미 있으면 입양(중복 생성 방지, state 이관 케이스)
                    adopt = self._lookup(ev)
                    if adopt:
                        self.log.info(f"ADOPT {ev.key} ← 기존 이벤트 {adopt}")
                        self._update(ev, adopt, stats, h, counter="adopted")
                        # 입양한 id 를 소스에 없는 옛 key 가 쥐고 있었으면 그 state 행을 지금 제거한다.
                        # absent 단계의 ABSENT_RELINKED 와 같은 뜻이지만, absent="keep"·source_window 밖·
                        # absent_filter 제외 행은 absent 단계에 안 오므로 여기서 해야 두 key 가 한 id 를 공유하지 않는다.
                        for okey, orow in list(existing.items()):
                            if orow.event_id == adopt and okey not in source_keys:
                                if not self.dry_run:
                                    self.state.delete(okey)
                                existing.pop(okey, None)
                                stats.relinked += 1
                                self.log.info(f"ADOPT_RELINKED {okey} → {ev.key} ({adopt}) state 행만 제거")
                    else:
                        self._create(ev, stats, h)
                    continue

                # 토픽 윈도우 안인데 id 가 사라졌으면 missing — UNCHANGED·CHANGED 둘 다 on_missing 정책(.65)
                if row.start_iso and cache.in_window(row.start_iso) and not cache.has_id(row.event_id):
                    if row.content_hash != h and self._recover_changed(ev, row, stats, h):
                        continue
                    self._missing(ev, row, stats, h)
                    continue

                if row.content_hash == h:
                    stats.unchanged += 1
                    unchanged_keys.append(ev.key)
                    continue

                # CHANGED
                self._update(ev, row.event_id, stats, h)
            except Exception as e:  # 한 건의 예외가 run 전체를 죽이지 않게
                self._fail(stats, f"예외 key={ev.key}: {e!r}")
                self.log.exception(f"sync 예외 key={ev.key}")

        if not self.dry_run:
            self.state.touch(unchanged_keys)

        # absent
        absent_rows = {k: r for k, r in existing.items() if k not in source_keys}
        if source_window is not None:
            lo, hi = _iso_z(source_window[0]), _iso_z(source_window[1])
            absent_rows = {
                k: r for k, r in absent_rows.items()
                if r.start_iso and (lo is None or r.start_iso >= lo) and (hi is None or r.start_iso < hi)
            }
        if absent_filter is not None:
            absent_rows = {k: r for k, r in absent_rows.items() if absent_filter(r)}
        limit = self._absent_limit(existing, source_window, absent_filter)

        if not absent_rows:
            pass
        elif self.absent == "keep":
            # keep 은 어차피 삭제가 없다 → source=0/incomplete 판정보다 먼저(보류 alert 소음 방지, .210)
            stats.absent_kept = len(absent_rows)
        elif len(events) == 0:
            stats.absent_skipped = "source=0"
            self.log.warning(f"소스 0건 → absent {len(absent_rows)}건 처리 스킵")
        elif not source_complete:
            stats.absent_skipped = "source_incomplete"
            self.log.warning(f"소스 일부 실패 → absent {len(absent_rows)}건 처리 스킵")
        elif limit is not None and len(absent_rows) > limit:
            stats.absent_skipped = "mass_absent"
            self.log.warning(f"absent 후보 {len(absent_rows)}건 > 한도 {limit} → 대량 소멸 의심, 삭제 전체 보류")
        else:
            for k, r in absent_rows.items():
                try:
                    self._delete(k, r, stats)
                except Exception as e:
                    self._fail(stats, f"delete 예외 key={k}: {e!r}")
                    self.log.exception(f"delete 예외 key={k}")

        self._alert_absent(stats, len(absent_rows))
        self.log.info(stats.summary())
        return stats

    def _absent_limit(self, existing: dict[str, StateRow], source_window, absent_filter) -> Optional[int]:
        """대량 absent 한도. 분모는 absent 판정 범위(창·필터) 안의 state 행 — 창 밖 과거 행이 분모를 부풀리지 않게."""
        if self.max_absent is not None:
            return self.max_absent
        if self.max_absent_ratio is None:
            return None
        rows = list(existing.values())
        if source_window is not None:
            lo, hi = _iso_z(source_window[0]), _iso_z(source_window[1])
            rows = [r for r in rows
                    if r.start_iso and (lo is None or r.start_iso >= lo) and (hi is None or r.start_iso < hi)]
        if absent_filter is not None:
            rows = [r for r in rows if absent_filter(r)]
        return max(10, int(self.max_absent_ratio * len(rows)))

    def _alert_absent(self, stats: SyncStats, n_absent: int) -> None:
        # 정상 소멸 삭제는 stats.summary() 로그로 충분하다 — HEALTH-ALERT 는 '삭제가 보류됐다'(불완전·대량)일 때만.
        # (관제탑 결정 2026-10-22: 매일 정상 삭제가 있는 봇이 health_check 에서 빨갛게 보이지 않게)
        if not self.alert_absent or not stats.absent_skipped:
            return
        tag = "[DRY-RUN] " if stats.dry_run else ""
        msg = f"{tag}sync[{self.topic_id}] absent {n_absent}건 처리 보류({stats.absent_skipped})"
        try:
            from .run import alert
            alert(msg)
        except Exception:
            self.log.warning(msg)
