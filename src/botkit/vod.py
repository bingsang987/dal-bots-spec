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

VOD 시작 시각(W9 — 17h 초과 자동 분할 VOD 대응):

    meta = vod.VodMeta(conn, dry_run=dry_run)               # vod_meta 캐시(videoNo → liveOpenDate)
    n_fail = vod.resolve_starts(vods, meta)                   # v["start_ms"], v["live_open_ms"], v["_start_source"]
    vod.group_by_live(vods)                                   # 같은 live_open_ms = 한 방송(_series_head = 1부)
    held = [v for v in vods if v["_start_source"] == "estimated"]   # 상세 실패 → 이번 run 매칭·생성 보류
    if meta.failing(3): alert(...)                            # 같은 VOD 3 run 연속 상세 실패

VOD 블록 index 규약: 0 → '치지직 VOD 링크', n≥1 → '치지직 VOD 링크-{n+1}'(chzzk/aesther/cluez 방식).
stellive `build_vod_h3` 는 1-base 였으므로 어댑터에서 index-1 로 넘긴다.
rel 은 `noopener noreferrer ugc` 고정(dal.wiki sanitizer 가 ugc 를 덧붙이므로 미리 넣어 멱등).
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Mapping, Optional

import requests

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
    dry-run 에선 테이블도 만들지 않고, 생성 DDL 은 호출자 conn 의 미커밋 트랜잭션을 커밋하지 않는다(.219).
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
        # executescript 는 실행 전에 호출자의 미커밋 트랜잭션을 COMMIT 한다 → 단일 execute 로.
        self._table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='missing_keys'"
        ).fetchone() is not None
        if not self._table and not dry_run:
            pending = conn.in_transaction
            conn.execute(self._SCHEMA)
            if not pending:
                conn.commit()
            self._table = True

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
        if not self._table:
            return False
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
        ).fetchall() if self._table else []
        return sorted({r[0] for r in rows} | set(self._mem))


def drop_tombstones(conn: sqlite3.Connection) -> None:
    """옛 TombstoneStore 테이블 제거(R-B). 없으면 no-op."""
    conn.execute("DROP TABLE IF EXISTS tombstones")
    conn.commit()


def vod_stats_line(bot: str, **counts) -> str:
    """'[bot] k=v k=v' 한 줄(0 이 아닌 값만, 순서 유지). 로그 TOTAL 줄 통일용."""
    parts = " ".join(f"{k}={v}" for k, v in counts.items() if v)
    return f"[{bot}] {parts or 'no-op'}"


# ---------------------------------------------------------------------------
# VOD 시작 시각 — 치지직 liveOpenDate (W9, dalbots-bmi.233)
# ---------------------------------------------------------------------------
# 치지직은 17h 초과 방송을 'YYYY년 MM월 DD일 N부 …' 로 자동 분할하고, 분할편의 publishDateAt 은 그 부의
# 방송 종료가 아니라 인코딩·공개 시각이라 수 시간 늦다 → publishDateAt − duration 추정이 깨진다.
# 상세 API(v2/videos/{no})의 liveOpenDate 는 같은 라이브의 모든 분할편에 같은 값이라 시작 시각·묶음 키로 쓴다.
CHZZK_VIDEO_API = "https://api.chzzk.naver.com/service/v2/videos"
KST = timezone(timedelta(hours=9))
SPLIT_TITLE_RE = re.compile(r"^\s*\d{4}년\s*\d{1,2}월\s*\d{1,2}일\s*(\d+)부\s*")
_UA = "Mozilla/5.0 (dalwiki-botkit)"


def parse_live_open(raw: Optional[str]) -> Optional[int]:
    """'YYYY-MM-DD HH:MM:SS'(KST) → epoch ms. 비었거나 형식이 다르면 None."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        dt = datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    except ValueError:
        return None
    return int(dt.timestamp() * 1000)


def fetch_live_open(video_no, *, timeout: float = 10, session=None) -> Optional[int]:
    """치지직 상세 API → liveOpenDate epoch ms.

    Returns:
        int > 0 — 라이브 시작 시각
        0       — 상세는 받았지만 liveOpenDate 가 없음(UPLOAD 등 라이브 다시보기가 아님) → 추정값을 써도 된다
        None    — 실패(네트워크·비200·code≠200·형태 이상). 다음 run 재시도
    """
    url = f"{CHZZK_VIDEO_API}/{video_no}"
    try:
        r = (session or requests).get(url, headers={"User-Agent": _UA}, timeout=timeout)
        if r.status_code != 200:
            logger.warning(f"fetch_live_open {video_no}: HTTP {r.status_code}")
            return None
        data = r.json()
    except Exception as e:
        logger.warning(f"fetch_live_open {video_no} 예외: {e!r}")
        return None
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, dict) or data.get("code") not in (None, 200):
        code = data.get("code") if isinstance(data, dict) else type(data).__name__
        logger.warning(f"fetch_live_open {video_no}: 응답 형태 이상(code={code})")
        return None
    raw = content.get("liveOpenDate")
    if not raw:
        return 0
    ms = parse_live_open(raw)
    if ms is None:
        logger.warning(f"fetch_live_open {video_no}: liveOpenDate 형식 이상 {raw!r}")
    return ms


def split_part(title: Optional[str]) -> Optional[int]:
    """'2026년 10월 21일 2부 …' → 2. 분할 접두가 없으면 None."""
    m = SPLIT_TITLE_RE.match(title or "")
    return int(m.group(1)) if m else None


def strip_split_prefix(title: Optional[str]) -> str:
    """분할 접두('YYYY년 MM월 DD일 N부 ')를 뗀 본제. 떼고 나서 비면 원문 그대로(strip)."""
    t = (title or "").strip()
    body = SPLIT_TITLE_RE.sub("", t, count=1).strip()
    return body or t


class VodMeta:
    """vod_meta 캐시 — videoNo 하나당 상세 API 한 번.

        vod_meta(video_no PK, live_open_ms, title, duration, fetched_at, fail_count)
        live_open_ms: >0 라이브 시작 / 0 라이브 아님(UPLOAD) / NULL 상세 실패(fail_count 누적, 다음 run 재시도)

    dry_run=True 면 테이블을 만들지 않고 DB 에 쓰지 않는다(테이블이 있으면 읽기는 한다, 새 값은 메모리).
    hits/fetched/failed 는 이 인스턴스의 누계(로그용 — 상세 호출 수 = fetched + failed).
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS vod_meta (
        video_no     TEXT PRIMARY KEY,
        live_open_ms INTEGER,
        title        TEXT,
        duration     INTEGER,
        fetched_at   TEXT NOT NULL,
        fail_count   INTEGER NOT NULL DEFAULT 0
    )
    """

    def __init__(self, conn: sqlite3.Connection, *, dry_run: bool = False):
        self.conn = conn
        self.dry_run = dry_run
        self._mem: dict[str, int] = {}
        self._mem_fail: dict[str, int] = {}
        self.hits = self.fetched = self.failed = 0
        self._table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vod_meta'"
        ).fetchone() is not None
        if not self._table and not dry_run:
            pending = conn.in_transaction
            conn.execute(self._SCHEMA)
            if not pending:
                conn.commit()
            self._table = True

    def get(self, video_no) -> Optional[int]:
        """캐시된 live_open_ms(0 = 라이브 아님). 없거나 실패 기록만 있으면 None(= 상세 조회 필요)."""
        vno = str(video_no)
        if vno in self._mem:
            return self._mem[vno]
        if not self._table:
            return None
        row = self.conn.execute("SELECT live_open_ms FROM vod_meta WHERE video_no=?", (vno,)).fetchone()
        return row[0] if row and row[0] is not None else None

    def put(self, video_no, live_open_ms: int, title: str = "", duration: int = 0) -> None:
        vno = str(video_no)
        self._mem[vno] = int(live_open_ms)
        self._mem_fail.pop(vno, None)
        if self.dry_run:
            return
        self.conn.execute(
            "INSERT OR REPLACE INTO vod_meta (video_no, live_open_ms, title, duration, fetched_at, fail_count)"
            " VALUES (?,?,?,?,?,0)",
            (vno, int(live_open_ms), title or "", int(duration or 0), MissingGuard._now()),
        )
        self.conn.commit()

    def record_failure(self, video_no, title: str = "", duration: int = 0) -> int:
        """상세 실패 1회 기록. 누적 연속 실패 수 반환(성공하면 put 이 0 으로 되돌린다)."""
        vno = str(video_no)
        n = self._fail_count(vno) + 1
        self._mem_fail[vno] = n
        if not self.dry_run:
            self.conn.execute(
                "INSERT INTO vod_meta (video_no, live_open_ms, title, duration, fetched_at, fail_count)"
                " VALUES (?,NULL,?,?,?,1)"
                " ON CONFLICT(video_no) DO UPDATE SET fail_count=fail_count+1, fetched_at=excluded.fetched_at",
                (vno, title or "", int(duration or 0), MissingGuard._now()),
            )
            self.conn.commit()
        return n

    def _fail_count(self, vno: str) -> int:
        if vno in self._mem_fail:
            return self._mem_fail[vno]
        if not self._table:
            return 0
        row = self.conn.execute(
            "SELECT fail_count FROM vod_meta WHERE video_no=? AND live_open_ms IS NULL", (vno,)
        ).fetchone()
        return int(row[0]) if row else 0

    def failing(self, min_count: int = 3, video_nos: Optional[Iterable] = None) -> list[tuple[str, int]]:
        """연속 상세 실패가 min_count 이상인 (videoNo, 횟수). video_nos 를 주면 그 안에서만."""
        counts: dict[str, int] = {}
        if self._table:
            for vno, n in self.conn.execute(
                "SELECT video_no, fail_count FROM vod_meta WHERE live_open_ms IS NULL"
            ):
                counts[vno] = int(n)
        counts.update(self._mem_fail)
        for vno in self._mem:
            counts.pop(vno, None)
        only = {str(x) for x in video_nos} if video_nos is not None else None
        return sorted((k, n) for k, n in counts.items() if n >= min_count and (only is None or k in only))


def _est_start_ms(v: dict) -> int:
    """옛 추정: publishDateAt − duration."""
    return int(v.get("publishDateAt") or 0) - int(v.get("duration") or 0) * 1000


def resolve_starts(
    vods: list[dict],
    meta: VodMeta,
    fetch: Optional[Callable[[str], Optional[int]]] = fetch_live_open,
    *,
    sleep: float = 0.1,
) -> int:
    """각 VOD dict(목록 API 항목: videoNo·publishDateAt·duration)에 시작 시각을 정한다(in-place). 상세 실패 수 반환.

        v["start_ms"]       — live_open_ms 가 있으면 그것, 없으면 publishDateAt − duration(옛 추정)
        v["live_open_ms"]   — 라이브 시작(있을 때만 키가 생긴다)
        v["_est_start_ms"]  — 옛 추정값(로그·비교용)
        v["_start_source"]  — "live_open" | "no_live"(상세 OK, liveOpenDate 없음 = UPLOAD 등 → 추정값이 맞다) |
                              "estimated"(상세 실패 → 호출자는 이번 run 매칭·생성 보류, 다음 run 재시도)

    캐시(meta)에 있는 videoNo 는 상세를 부르지 않는다. 실패는 meta.record_failure 로 누적한다.
    fetch=None 이면 상세를 부르지 않고 캐시 미스를 전부 실패로 친다(장애 시뮬레이션용).
    """
    failed = 0
    for v in vods:
        vno = str(v.get("videoNo") or "")
        est = _est_start_ms(v)
        v["_est_start_ms"] = est
        v.pop("live_open_ms", None)
        lo = meta.get(vno) if vno else None
        if lo is not None:
            meta.hits += 1
        elif vno:
            try:
                lo = fetch(vno) if fetch is not None else None
            except Exception as e:  # 주입된 fetch 가 던져도 실패로
                logger.warning(f"resolve_starts {vno} 예외: {e!r}")
                lo = None
            if lo is None:
                failed += 1
                meta.failed += 1
                meta.record_failure(vno, v.get("videoTitle", ""), v.get("duration") or 0)
            else:
                meta.fetched += 1
                meta.put(vno, lo, v.get("videoTitle", ""), v.get("duration") or 0)
            if fetch is not None and sleep:
                time.sleep(sleep)
        if lo:
            v["live_open_ms"] = int(lo)
            v["start_ms"] = int(lo)
            v["_start_source"] = "live_open"
        else:
            v["start_ms"] = est
            v["_start_source"] = "no_live" if lo == 0 else "estimated"
    return failed


def group_by_live(vods: list[dict], max_gap_minutes: int = 60) -> int:
    """같은 live_open_ms 인 VOD = 한 방송(분할편). in-place 정렬 + series 메타. 분할편(머리 제외) 수 반환.

    정렬: (live_open_ms 또는 start_ms, publishDateAt). 묶음 머리(head)는 제목의 'N부' 최솟값,
    없으면 publishDateAt 최솟값 — 2부가 1부보다 먼저 공개돼도 1부가 머리다(1부가 아직 없으면 2부가 머리).
    머리 아닌 분할편: _series_part=True, _series_head=머리 videoNo, _orig_est_start_ms=옛 추정(기존 메타와 호환).
    start_ms 는 resolve_starts 가 이미 live_open_ms 로 맞춰 두었다.

    live_open_ms 가 없는 VOD 끼리만 옛 gap 휴리스틱(off-air 간격 ±max_gap_minutes)을 폴백으로 적용하고,
    그 분할편의 start_ms 는 묶음 첫 VOD 의 start_ms 로 덮는다. max_gap_minutes ≤ 0 이면 폴백 안 함.
    """
    if not vods:
        return 0
    for v in vods:
        if "start_ms" not in v:
            v["start_ms"] = v.get("live_open_ms") or _est_start_ms(v)
    vods.sort(key=lambda v: (v.get("live_open_ms") or v["start_ms"], v.get("publishDateAt") or 0))
    grouped = 0

    by_live: dict[int, list[dict]] = {}
    for v in vods:
        if v.get("live_open_ms"):
            by_live.setdefault(v["live_open_ms"], []).append(v)
    for parts in by_live.values():
        if len(parts) < 2:
            continue
        head = min(parts, key=lambda v: (split_part(v.get("videoTitle")) or 10 ** 6, v.get("publishDateAt") or 0))
        for v in parts:
            if v is head:
                continue
            v["_orig_est_start_ms"] = v.get("_est_start_ms", _est_start_ms(v))
            v["_series_part"] = True
            v["_series_head"] = head.get("videoNo")
            grouped += 1

    rest = [v for v in vods if not v.get("live_open_ms")]
    if rest and max_gap_minutes > 0:
        max_gap_ms = max_gap_minutes * 60 * 1000
        rest.sort(key=lambda v: v["start_ms"])
        s_start = rest[0]["start_ms"]
        s_end = rest[0].get("publishDateAt") or s_start
        s_head = rest[0].get("videoNo")
        for v in rest[1:]:
            v_start = v["start_ms"]
            gap = v_start - s_end
            if -max_gap_ms <= gap <= max_gap_ms:
                v["_orig_est_start_ms"] = v_start
                v["_series_part"] = True
                v["_series_head"] = s_head
                v["start_ms"] = s_start
                grouped += 1
                s_end = max(s_end, v.get("publishDateAt") or v_start)
            else:
                s_start, s_end, s_head = v_start, v.get("publishDateAt") or v_start, v.get("videoNo")
    return grouped
