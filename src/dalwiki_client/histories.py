"""dal.wiki 이벤트 변경 이력(audit log) 조회 + Tombstone 관리.

dal.wiki API `POST /topics/histories` 는 각 이벤트의 CREATE/UPDATE/DELETE 이력을
`isBot`, `userSlug`, `type`, `version` 등으로 반환한다. 이를 활용해 "사람이 한 번
지운 자리에는 봇이 다시 만들지 않는다" 패턴(tombstone)을 구현한다.

사용 예 (봇이 만든 매핑이 dal.wiki에서 사라졌을 때 사용자 의사 확인):

    from dalwiki_client.histories import TombstoneStore, get_latest_action

    tomb = TombstoneStore(sqlite_conn, scope="main")

    # 실행 시작 시: 사라진 매핑 점검 → 사람 DELETE 면 tombstone
    tomb.sweep_from_mapping(
        topic_id=MAIN_TOPIC_ID,
        mapping={ind_id: main_id, ...},
        current_event_ids={ev["id"] for ev in current_main_events},
        on_pop=lambda ind_id: db_pop_main_map(ind_id),
    )

    # CREATE 직전 체크
    if tomb.is_tombstoned(category_id, start_iso):
        log.info("SKIP_TOMBSTONED %s @ %s", category_id, start_iso)
        continue

    # 사람이 손으로 수정한 이벤트는 봇이 덮어쓰지 않게 보호
    if was_human_edited(topic_id, event_id):
        log.info("HUMAN_EDITED → PATCH skip")
        continue

TombstoneStore 는 호출자가 넘긴 sqlite3.Connection 위에 `tombstones` 테이블을
자동 생성한다. 각 봇이 자기 DB 안에 두는 것이 권장 패턴. `scope` 인자로 같은
DB 안에서 main/indiv 영역을 분리할 수 있다.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

from .client import _post

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Histories API 래퍼
# ---------------------------------------------------------------------------
def list_histories(
    topic_id: str,
    event_id: Optional[str] = None,
    take: int = 25,
    skip: int = 0,
) -> list[dict]:
    """`/topics/histories` 호출. event_id 없으면 토픽 전체, 있으면 단일 이벤트.

    Returns:
        list[dict] — 각 항목: {eventId, version, type, summary, start, end,
        categoryId, categoryPath, createdAt, userName, userSlug, isBot, ipAddress}
        실패 시 빈 리스트.
    """
    body: dict = {"topicId": topic_id, "take": min(take, 100), "skip": skip}
    if event_id:
        body["eventId"] = event_id
    resp = _post("/topics/histories", body)
    if resp is None or resp.get("status") != 200:
        return []
    data = resp.get("body")
    return data if isinstance(data, list) else []


def get_latest_action(topic_id: str, event_id: str) -> Optional[dict]:
    """단일 이벤트의 가장 최근 액션 1건. 없으면 None."""
    hist = list_histories(topic_id, event_id=event_id, take=1)
    return hist[0] if hist else None


def was_human_deleted(topic_id: str, event_id: str) -> bool:
    """가장 최근 액션이 사람의 DELETE 인지.

    "봇이 만든 이벤트가 dal.wiki에서 사라졌다 → 사람이 지운 것인지 봇이 지운 것인지"
    구분에 사용. dedup_clean 같은 자동화 봇이 지운 케이스를 사용자 의사로 오인하지
    않도록 isBot=false 만 True 로 반환한다.
    """
    latest = get_latest_action(topic_id, event_id)
    return bool(
        latest
        and latest.get("type") == "DELETE"
        and not latest.get("isBot", True)
    )


def was_human_edited(topic_id: str, event_id: str, lookback: int = 10) -> bool:
    """이벤트 이력에 사람의 UPDATE 가 한 번이라도 있었는지.

    봇이 PATCH 로 덮어쓰기 전에 호출하여 "사람이 손으로 수정한 일정은 보존" 패턴에
    사용. 메인 캘린더의 description 수동 추가분(카페 공지 링크 등) 보호에 유용.
    """
    hist = list_histories(topic_id, event_id=event_id, take=lookback)
    return any(
        h.get("type") == "UPDATE" and not h.get("isBot", True) for h in hist
    )


def deletion_actor(topic_id: str, event_id: str) -> Optional[dict]:
    """이벤트의 가장 최근 DELETE 액션 객체 반환 (사람/봇 모두 포함). 없으면 None.

    tombstone 추가 시 사람 정보를 기록하거나 진단용으로 사용.
    """
    latest = get_latest_action(topic_id, event_id)
    if latest and latest.get("type") == "DELETE":
        return latest
    return None


# ---------------------------------------------------------------------------
# TombstoneStore
# ---------------------------------------------------------------------------
class TombstoneStore:
    """사람이 지운 (categoryId, start_minute) 위치를 영구 기록.

    각 봇이 자기 sqlite3.Connection 을 넘겨 사용. 같은 DB 안에서 `scope` 인자로
    영역을 나눌 수 있다 (예: "main", "indiv").

    DB 스키마는 init 시 자동 생성. 호출자가 별도로 init_db 할 필요 없음.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS tombstones (
        scope            TEXT NOT NULL,
        category_id      TEXT NOT NULL,
        start_iso_minute TEXT NOT NULL,
        topic_id         TEXT,
        event_id         TEXT,
        deleted_by       TEXT,
        deleted_at       TEXT,
        created_at       TEXT NOT NULL,
        PRIMARY KEY (scope, category_id, start_iso_minute)
    );
    """

    def __init__(self, conn: sqlite3.Connection, scope: str = "default"):
        self.conn = conn
        self.scope = scope
        conn.executescript(self._SCHEMA)
        conn.commit()

    @staticmethod
    def _normalize_key(start_iso: str) -> str:
        """ISO datetime → 분 단위 truncate. UTC Z 또는 +09:00 둘 다 받아들이고
        UTC 기준 'YYYY-MM-DDTHH:MM' 로 정규화."""
        if not start_iso:
            return ""
        try:
            dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")
        except (ValueError, TypeError):
            # 파싱 실패 시 앞 16자 그대로
            return start_iso[:16]

    def is_tombstoned(self, category_id: str, start_iso: str) -> bool:
        """해당 (category_id, start_minute) 에 tombstone 이 있는지."""
        key = self._normalize_key(start_iso)
        if not category_id or not key:
            return False
        row = self.conn.execute(
            "SELECT 1 FROM tombstones WHERE scope=? AND category_id=? AND start_iso_minute=? LIMIT 1",
            (self.scope, category_id, key),
        ).fetchone()
        return row is not None

    def add(
        self,
        category_id: str,
        start_iso: str,
        *,
        topic_id: Optional[str] = None,
        event_id: Optional[str] = None,
        deleted_by: Optional[str] = None,
        deleted_at: Optional[str] = None,
    ) -> bool:
        """tombstone 추가. 이미 있으면 무시. 추가됐으면 True."""
        key = self._normalize_key(start_iso)
        if not category_id or not key:
            return False
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO tombstones "
            "(scope, category_id, start_iso_minute, topic_id, event_id, deleted_by, deleted_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (self.scope, category_id, key, topic_id, event_id, deleted_by, deleted_at, now),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def remove(self, category_id: str, start_iso: str) -> bool:
        """tombstone 제거 (CLI revive 용). 제거됐으면 True."""
        key = self._normalize_key(start_iso)
        cur = self.conn.execute(
            "DELETE FROM tombstones WHERE scope=? AND category_id=? AND start_iso_minute=?",
            (self.scope, category_id, key),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def list_all(self) -> list[dict]:
        """현재 scope 의 모든 tombstone. 진단/CLI 출력용."""
        rows = self.conn.execute(
            "SELECT category_id, start_iso_minute, topic_id, event_id, deleted_by, deleted_at, created_at "
            "FROM tombstones WHERE scope=? ORDER BY created_at DESC",
            (self.scope,),
        ).fetchall()
        return [
            {
                "category_id": r[0],
                "start_iso_minute": r[1],
                "topic_id": r[2],
                "event_id": r[3],
                "deleted_by": r[4],
                "deleted_at": r[5],
                "created_at": r[6],
            }
            for r in rows
        ]

    def sweep_from_mapping(
        self,
        topic_id: str,
        mapping: dict[str, str],
        current_event_ids: Iterable[str],
        on_pop: Optional[Callable[[str], None]] = None,
    ) -> list[str]:
        """봇 매핑(local_key → dal.wiki event_id) 중 dal.wiki 에 사라진 항목을 점검.

        사라진 매핑 각각에 대해 `/topics/histories` 로 마지막 액션을 확인하고,
        사람(isBot=false) 의 DELETE 면 tombstone 에 추가한다. 봇의 DELETE 면 (예:
        dedup_clean) tombstone 은 추가하지 않고 매핑만 제거한다.

        Args:
            topic_id: 메인 또는 개별 토픽 ID.
            mapping: 봇이 관리하는 매핑. key 는 자유 (보통 indiv_id 또는 ind 측 자연키),
                value 는 dal.wiki 의 event_id.
            current_event_ids: 현재 dal.wiki 에 살아있는 이벤트 ID 집합.
            on_pop: DELETE 가 확정된 매핑의 key 를 받는 callback. 봇 측에서 매핑 DB
                에서 제거할 때 사용. 호출 실패는 warn 로그만 남기고 계속 진행.

        Returns:
            새로 tombstone 에 추가된 event_id 리스트 (사람 DELETE 만).
        """
        current = set(current_event_ids)
        added: list[str] = []
        for key, ev_id in list(mapping.items()):
            if not ev_id or ev_id in current:
                continue
            latest = get_latest_action(topic_id, ev_id)
            if not latest:
                # 이력 없음 — 보존 만료 가능성. 보수적으로 매핑/tombstone 모두 건드리지 않음.
                continue
            if latest.get("type") != "DELETE":
                # 마지막이 UPDATE/CREATE 인데 current_event_ids 에 없다는 건 fetch 누락 가능성.
                continue
            # DELETE 확정 → 매핑 제거
            if on_pop:
                try:
                    on_pop(key)
                except Exception as e:
                    logger.warning("on_pop failed for %s: %s", key, e)
            if not latest.get("isBot", True):
                # 사람 DELETE → tombstone 추가
                if self.add(
                    category_id=latest.get("categoryId", ""),
                    start_iso=latest.get("start", ""),
                    topic_id=topic_id,
                    event_id=ev_id,
                    deleted_by=latest.get("userSlug") or latest.get("userName"),
                    deleted_at=latest.get("createdAt"),
                ):
                    added.append(ev_id)
        return added
