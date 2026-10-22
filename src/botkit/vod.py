"""botkit.vod — 치지직 VOD/미러 계열 봇(chzzk·stellive·aesther·cluez·streamersync) 공통 조각.

이 봇들은 per-day reconciliation·series 병합·분류 규칙을 각자 갖고 있어 재구축하지 않는다
(W4 계획서 §0). 여기 있는 건 다섯 벌로 복붙돼 있던 부분과 감사 결함 수정용 부품뿐이다.

    from botkit import vod

    events, complete = vod.fetch_events_chunked(MAIN_TOPIC, from_ms, to_ms)
    if not complete:
        alert("[봇] dal.wiki 조회 불완전 — 삭제·생성 보류")   # prune/DAY_WIPE/orphan 생성/sweep 스킵
    vod.hydrate_descriptions(events)                          # 실패한 건 ev["_desc_failed"]=True
    desc = vod.get_description(eid)                           # None = 실패 → 그 이벤트 건드리지 말 것
    new_desc = vod.append_vod(desc, video_no)                 # 이미 있으면 그대로

    guard = vod.MissingGuard(conn, scope="indiv")
    if complete:
        guard.sweep({main_id: indiv_id, ...}, present_ids={e["id"] for e in indiv_events})
    if guard.is_missing(main_id):
        ...  # 사람이 지운 자리 — 재생성 금지

VOD 블록 index 규약: 0 → '치지직 VOD 링크', n≥1 → '치지직 VOD 링크-{n+1}'(chzzk/aesther/cluez 방식).
stellive `build_vod_h3` 는 1-base 였으므로 어댑터에서 index-1 로 넘긴다.
rel 은 `noopener noreferrer ugc` 고정(dal.wiki sanitizer 가 ugc 를 덧붙이므로 미리 넣어 멱등).
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Iterable, Mapping, Optional

from dalwiki_client import client as dw
from dalwiki_client.client import ListFailed

logger = logging.getLogger("botkit.vod")

VOD_VIDEO_NO_RE = re.compile(r"chzzk\.naver\.com/video/(\d+)")
VOD_REL = "noopener noreferrer ugc"
DAY_MS = 24 * 60 * 60 * 1000
SUMMARY_MAX = 100
SUMMARY_MIN = 2


# ---------------------------------------------------------------------------
# VOD 블록
# ---------------------------------------------------------------------------
def build_vod_block(video_no: str, index: int = 0) -> str:
    """표준 VOD h3 블록 1벌. index=0 → '치지직 VOD 링크', n≥1 → '치지직 VOD 링크-{n+1}'."""
    label = "치지직 VOD 링크" if index == 0 else f"치지직 VOD 링크-{index + 1}"
    url = f"https://chzzk.naver.com/video/{video_no}"
    return (
        '<h3 class="editor-heading-h3">'
        f'<span>{label} : </span>'
        f'<a href="{url}" target="_blank" rel="{VOD_REL}" class="editor-link">'
        f'<span>{url}</span></a>'
        '</h3>'
    )


def extract_video_nos(description_html: Optional[str]) -> list[str]:
    """본문에 있는 치지직 videoNo 목록(등장 순서, 중복 제거)."""
    return list(dict.fromkeys(VOD_VIDEO_NO_RE.findall(description_html or "")))


def append_vod(description_html: Optional[str], video_no: str) -> str:
    """기존 본문 끝에 VOD 블록을 붙인 새 본문. 같은 videoNo 가 이미 있으면 원문 그대로(멱등).

    기존 내용(카페 공지 블록 = cafe.naver.com 링크가 든 블록, 이전 VOD 블록)은 한 글자도
    바꾸지 않고 그 아래에 붙인다. 라벨 번호는 이미 있는 VOD 수로 정한다.
    """
    existing = description_html or ""
    vno = str(video_no)
    nos = extract_video_nos(existing)
    if vno in nos:
        return existing
    return existing + build_vod_block(vno, len(nos))


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------
def safe_summary(s: Optional[str], fallback: Optional[str] = None) -> str:
    """dal.wiki summary 규칙(2~100자)에 맞춘 제목.

    strip 후 2자 미만이면 fallback(보통 원문 — 예: '갠방 - 롤' 의 '롤')으로, 그것도 2자 미만이면 '방송'.
    100자 초과는 100자에서 자르고 끝 공백을 지운다 — 안 지우면 client `_guard_summary` 가 strip 해 99자로
    저장되고, 다음 run 비교에서 매번 불일치 → 영구 PATCH(.89).
    """
    t = (s or "").strip()
    if len(t) < SUMMARY_MIN:
        fb = (fallback or "").strip()
        t = fb if len(fb) >= SUMMARY_MIN else "방송"
    return t[:SUMMARY_MAX].rstrip()


# ---------------------------------------------------------------------------
# dal.wiki 조회
# ---------------------------------------------------------------------------
def fetch_events_chunked(
    topic_id: str,
    from_ms: int,
    to_ms: int,
    chunk_days: int = 3,
    *,
    category_ids: Optional[Iterable[str]] = None,
) -> tuple[list[dict], bool]:
    """[from_ms, to_ms) 를 chunk_days 단위로 events/list. 반환 (events, complete).

    청크 하나라도 ListFailed/예외면 complete=False — 그 청크는 비어 있는 게 아니라 **모르는** 것이다.
    호출자는 complete=False 인 run 에서 prune/DAY_WIPE/orphan 생성/MissingGuard.sweep 을 하지 않는다.
    성공한 청크의 이벤트는 그대로 돌려준다(기존 매핑 PATCH 는 해도 된다). id 로 중복 제거.
    """
    chunk_ms = max(1, int(chunk_days * DAY_MS))
    cats = list(category_ids) if category_ids else None
    seen: dict[str, dict] = {}
    no_id: list[dict] = []
    complete = True
    cur = int(from_ms)
    while cur < to_ms:
        nxt = min(cur + chunk_ms, int(to_ms))
        try:
            chunk = dw.list_events(topic_id, from_=cur, to=nxt, category_ids=cats)
        except ListFailed as e:
            complete = False
            logger.warning(f"fetch_events_chunked 청크 실패 → 불완전: {e}")
            chunk = []
        except Exception as e:  # 네트워크/파싱 등 — 마찬가지로 '모름'
            complete = False
            logger.warning(f"fetch_events_chunked 청크 예외 topic={topic_id} {cur}~{nxt} → 불완전: {e!r}")
            chunk = []
        for e in chunk or []:
            if not isinstance(e, dict):
                continue
            eid = e.get("id")
            if eid:
                seen.setdefault(eid, e)
            else:
                no_id.append(e)
        cur = nxt
    return list(seen.values()) + no_id, complete


def get_description(event_id: str, *, attempts: int = 3, backoff: float = 1.5) -> Optional[str]:
    """events/get 으로 실제 description. events/list 는 description 을 주지 않는다.

    Returns:
        str — 실제 본문("" = 정말 비어 있음)
        None — 취득 실패(재시도 소진·4xx). 호출자는 이번 run 에 그 이벤트 본문을 건드리지 말 것
               (""로 폴백해 append/PATCH 하면 카페 공지 블록이 날아간다).
    4xx 는 재시도해도 같으니 즉시 중단, 그 외(5xx·네트워크)는 backoff×1, ×2 초 쉬고 재시도.
    조회 전용이라 DRY_RUN 에서도 실제 호출된다.
    """
    if not event_id:
        return None
    last = None
    for attempt in range(attempts):
        try:
            resp = dw._post("/events/get", {"id": event_id}, retries=1)
        except Exception as e:
            resp, last = None, repr(e)
        if resp is not None:
            sc = resp.get("status")
            if sc == 200:
                body = resp.get("body")
                return (body.get("description") or "") if isinstance(body, dict) else ""
            last = f"HTTP {sc}"
            if sc is not None and 400 <= sc < 500:
                break
        elif last is None:
            last = "네트워크 재시도 소진"
        if attempt < attempts - 1:
            time.sleep(backoff * (attempt + 1))
    logger.warning(f"get_description {event_id} 실패: {last}")
    return None


def hydrate_descriptions(events: Iterable[dict]) -> int:
    """events(list 결과 dict) 각각에 events/get 본문을 채운다(in-place). 실패 건수 반환.

    성공: ev["description"]=본문, ev["_desc_loaded"]=True, ev["_desc_failed"]=False
    실패: 기존 ev["description"] 은 그대로 두고 ev["_desc_failed"]=True(_desc_loaded 는 세우지 않음 →
          다음 호출에서 재시도). 실패한 이벤트의 본문은 이번 run 에 쓰면 안 된다.
    이미 _desc_loaded 인 건 건너뛴다.
    """
    failed = 0
    for ev in events:
        if ev.get("_desc_loaded") or not ev.get("id"):
            continue
        d = get_description(ev["id"])
        if d is None:
            ev["_desc_failed"] = True
            failed += 1
            continue
        ev["description"] = d
        ev["_desc_loaded"] = True
        ev["_desc_failed"] = False
    return failed


# ---------------------------------------------------------------------------
# MissingGuard (Tombstone 대체)
# ---------------------------------------------------------------------------
class MissingGuard:
    """'봇이 매핑해 둔 이벤트가 토픽에서 사라졌다' = 사람이 지운 자리 → 그 키로는 다시 만들지 않는다.

    TombstoneStore(카테고리+시각 자리 기준)를 대체한다. 키는 봇의 매핑 키
    (chzzk: indiv_id / aesther·cluez: main_id / stellive: stellight_id·indiv_id / streamersync: src_id).

        guard = MissingGuard(conn, scope="indiv")
        if complete:                                   # ⚠ 불완전 조회로 '없음' 오판 금지
            guard.sweep(mapping, present_ids)
        if guard.is_missing(key): skip create
        guard.forget(key)                              # 소스에서 그 키가 사라졌을 때

    dry_run=True 면 DB 에 쓰지 않고 이번 프로세스 메모리에만 기록한다(is_missing 은 둘 다 본다).
    sweep(verify=True, 기본)은 새로 기록하기 전에 events/get 404 를 확인하고, 새 기록이 있으면 alert 한 줄.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS missing_keys (
        scope           TEXT NOT NULL,
        key             TEXT NOT NULL,
        event_id        TEXT,
        seen_missing_at TEXT NOT NULL,
        PRIMARY KEY (scope, key)
    );
    """

    def __init__(self, conn: sqlite3.Connection, scope: str, *, dry_run: bool = False):
        self.conn = conn
        self.scope = scope
        self.dry_run = dry_run
        self._mem: dict[str, str] = {}
        conn.executescript(self._SCHEMA)
        conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def sweep(self, mapping: Mapping[str, str], present_ids: Iterable[str], *, verify: bool = True) -> list[str]:
        """mapping(key→event_id) 중 event_id 가 present_ids(완전 조회한 토픽 id 집합)에 없는 키를 기록하고 반환.

        반환은 이번 sweep 에서 없다고 확인된 키 전부(이미 기록돼 있던 것 포함, 정렬).
        present_ids 에 다시 보이는 키가 기록돼 있으면 기록을 지운다(일시 오판 복구).
        호출자는 present_ids 가 매핑된 이벤트의 시간 범위를 빠짐없이 덮는지 책임진다
        (윈도우 밖 매핑은 mapping 에서 빼고 넘길 것).

        verify=True(기본): **새로** 기록할 키는 먼저 `events/get` 으로 확인한다 — 404 면 기록, 200 이면 살아 있는
        이벤트(조회 창 경계 오판)라 기록하지 않고 반환에서도 뺀다, 그 외(네트워크·5xx)는 판단 보류로 이번 프로세스
        메모리에만 둔다(이번 run 은 재생성 금지, 다음 run 재확인). 새로 기록한 키가 있으면 alert 한 줄.
        """
        present = set(present_ids)
        missing: list[str] = []
        recorded: list[str] = []
        for key, eid in mapping.items():
            if not key or not eid:
                continue
            if eid in present:
                if self.is_missing(key):
                    self.forget(key)
                continue
            if self.is_missing(key):
                missing.append(key)
                continue
            if verify:
                status = self._get_status(eid)
                if status == 200:
                    logger.info(f"MISSING[{self.scope}] {key} → {eid} 조회엔 없지만 events/get 200 → 기록 안 함")
                    continue
                if status != 404:
                    logger.warning(f"MISSING[{self.scope}] {key} → {eid} events/get status={status} → 이번 run 만 보류")
                    self._mem[key] = eid
                    missing.append(key)
                    continue
            missing.append(key)
            recorded.append(key)
            logger.info(f"MISSING[{self.scope}] {key} → {eid} 토픽에 없음(사람 삭제로 간주, 재생성 금지)")
            if self.dry_run:
                self._mem[key] = eid
            else:
                self.conn.execute(
                    "INSERT OR IGNORE INTO missing_keys (scope, key, event_id, seen_missing_at) VALUES (?,?,?,?)",
                    (self.scope, key, eid, self._now()),
                )
        if not self.dry_run:
            self.conn.commit()
        if recorded:
            msg = (f"MissingGuard[{self.scope}] 사람 삭제 추정 {len(recorded)}건 기록(재생성 금지): "
                   + ", ".join(sorted(recorded)[:5]) + (" …" if len(recorded) > 5 else ""))
            try:
                from .run import alert
                alert(("[DRY-RUN] " if self.dry_run else "") + msg)
            except Exception:
                logger.warning(msg)
        return sorted(missing)

    @staticmethod
    def _get_status(event_id: str) -> Optional[int]:
        """events/get status(200/404/…). 네트워크 재시도 소진이면 None."""
        try:
            resp = dw._post("/events/get", {"id": event_id})
        except Exception as e:
            logger.warning(f"events/get {event_id} 예외: {e!r}")
            return None
        return resp.get("status") if resp else None

    def is_missing(self, key: str) -> bool:
        if key in self._mem:
            return True
        row = self.conn.execute(
            "SELECT 1 FROM missing_keys WHERE scope=? AND key=?", (self.scope, key)
        ).fetchone()
        return row is not None

    def forget(self, key: str) -> None:
        self._mem.pop(key, None)
        if self.dry_run:
            return
        self.conn.execute("DELETE FROM missing_keys WHERE scope=? AND key=?", (self.scope, key))
        self.conn.commit()

    def keys(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT key FROM missing_keys WHERE scope=? ORDER BY key", (self.scope,)
        ).fetchall()
        return sorted({r[0] for r in rows} | set(self._mem))


def drop_tombstones(conn: sqlite3.Connection) -> None:
    """옛 TombstoneStore 테이블 제거(R-B). 없으면 no-op."""
    conn.execute("DROP TABLE IF EXISTS tombstones")
    conn.commit()


def vod_stats_line(bot: str, **counts) -> str:
    """'[bot] k=v k=v' 한 줄(0 이 아닌 값만, 순서 유지). 로그 TOTAL 줄 통일용."""
    parts = " ".join(f"{k}={v}" for k, v in counts.items() if v)
    return f"[{bot}] {parts or 'no-op'}"
