"""dal.wiki API v2 client — production-grade.

dalwiki_client_v2.py (migration template) 기반으로 다음 기능을 추가했다:
- list_events: 응답이 take cap을 채우면 시간 윈도우를 절반으로 쪼개 재귀 호출 (truncation 자동 분할)
- update_event: 404 시 자동 fallback 함수 update_or_create_event 제공
- categoryIds, tz 같은 신 API 파라미터 노출
- DRY_RUN 분기, X-Api-Key 자동 첨부
- summary 100자 자동 클립

모든 datetime 입력은 ISO 8601 UTC Z 포맷 (`2026-05-28T03:00:00.000Z`) — KST/+09:00 오프셋
포맷을 보내면 dal.wiki가 400을 던지므로 호출자가 변환 책임을 진다.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Iterable, Optional

import requests

logger = logging.getLogger(__name__)

API_BASE = os.environ.get("DALWIKI_API_BASE", "https://api.dal.wiki/api-reference")
API_KEY = os.environ.get("DALWIKI_API_KEY", "")
TIMEOUT = int(os.environ.get("DALWIKI_TIMEOUT", "20"))
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

# events/list take 상한 (dal.wiki API v2)
MAX_TAKE = 1000

_session: Optional[requests.Session] = None
_topic_cache: dict[str, str] = {}
_dry_run_counter = 0


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"Content-Type": "application/json"})
        if API_KEY:
            _session.headers["X-Api-Key"] = API_KEY
    return _session


def _post(path: str, body: dict, retries: int = 3):
    """공통 POST. 200/201/204 OK. 429/5xx는 exponential backoff. 404는 응답에 status를
    표시해 호출자가 처리할 수 있게 한다.

    Returns:
        ({"status": int, "body": <json|None>}) on completion.
        None if all retries exhausted (network error).
    """
    if DRY_RUN:
        global _dry_run_counter
        _dry_run_counter += 1
        logger.info(f"[DRY-RUN] POST {path} {body}")
        return {"status": 200, "body": {"id": f"dry-{int(time.time())}-{_dry_run_counter}"}}

    url = f"{API_BASE}{path}"
    session = _get_session()

    for attempt in range(1, retries + 1):
        try:
            resp = session.post(url, json=body, timeout=TIMEOUT)
            sc = resp.status_code
            if sc in (200, 201, 204):
                try:
                    return {"status": sc, "body": resp.json()}
                except Exception:
                    return {"status": sc, "body": None}
            if sc == 404:
                return {"status": 404, "body": None}
            if sc in (429, 502, 503, 504):
                wait = 2 ** (attempt - 1)
                logger.warning(f"POST {path} → {sc}, retry {attempt}/{retries} in {wait}s")
                time.sleep(wait)
                continue
            logger.error(f"POST {path} → {sc}: {resp.text[:200]}")
            return {"status": sc, "body": None}
        except requests.RequestException as e:
            logger.warning(f"POST {path} 네트워크 오류 (attempt {attempt}): {e}")
            time.sleep(2 ** (attempt - 1))

    logger.error(f"POST {path} 최대 재시도 초과")
    return None


def _extract_id(resp_body) -> Optional[str]:
    if not resp_body:
        return None
    if isinstance(resp_body, dict):
        return resp_body.get("id") or resp_body.get("eventId")
    return None


# ── 토픽 ──────────────────────────────────────────────────────────────────

def resolve_topic_by_name(topic_name: str) -> Optional[str]:
    """legacy/resolveTopicByName 호출. 동일 이름 캐시."""
    if topic_name in _topic_cache:
        return _topic_cache[topic_name]
    resp = _post("/legacy/resolveTopicByName", {"topicName": topic_name})
    if not resp:
        return None
    body = resp["body"]
    if isinstance(body, list):
        body = body[0] if body else {}
    topic_id = _extract_id(body) or (body or {}).get("topicId")
    if topic_id:
        _topic_cache[topic_name] = topic_id
    return topic_id


def create_topic(name: str, description: str, thumbnail: str) -> Optional[str]:
    """topics/create. thumbnail은 API 필수(min 10자 URL)."""
    resp = _post("/topics/create", {
        "name": name,
        "description": description,
        "thumbnail": thumbnail,
    })
    return _extract_id(resp["body"]) if resp else None


# ── 이벤트 ────────────────────────────────────────────────────────────────

def _iso_z(value) -> Optional[str]:
    """datetime/aware → '...Z', str(이미 ISO) → 그대로, ms int → UTC Z, None → None."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("dal.wiki API는 naive datetime을 받지 않는다 — tzinfo 명시 필수")
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    raise TypeError(f"unsupported datetime value: {type(value)}")


def list_events(
    topic_id: str,
    from_=None,
    to=None,
    take: int = 500,
    category_ids: Optional[Iterable[str]] = None,
    per_date_limit: Optional[int] = None,
    tz: Optional[str] = None,
    _depth: int = 0,
    _max_depth: int = 7,
) -> list[dict]:
    """events/list. take cap에 걸리면 시간 윈도우를 절반으로 쪼개 재귀 호출.

    Args:
        from_, to: ISO Z 문자열 / datetime / Unix ms 정수 / None
        take: 1~1000 (clamped). 응답이 정확히 take면 truncation으로 간주.
        category_ids, per_date_limit, tz: 신 API 신규 파라미터
    """
    take = max(1, min(take, MAX_TAKE))
    body: dict = {"topicId": topic_id, "take": take}
    from_iso = _iso_z(from_)
    to_iso = _iso_z(to)
    if from_iso:
        body["from"] = from_iso
    if to_iso:
        body["to"] = to_iso
    if category_ids:
        body["categoryIds"] = list(category_ids)
    if per_date_limit is not None:
        body["perDateLimit"] = per_date_limit
    if tz:
        body["tz"] = tz

    resp = _post("/events/list", body)
    if not resp:
        return []
    raw = resp["body"]
    if isinstance(raw, list):
        events = raw
    elif isinstance(raw, dict):
        events = raw.get("events") or raw.get("items") or []
    else:
        events = []

    # truncation 자동 분할: 응답이 take를 채웠고 from/to 둘 다 있으면 윈도우 반으로
    if (
        len(events) >= take
        and from_iso
        and to_iso
        and _depth < _max_depth
    ):
        from_dt = datetime.fromisoformat(from_iso.replace("Z", "+00:00"))
        to_dt = datetime.fromisoformat(to_iso.replace("Z", "+00:00"))
        if (to_dt - from_dt).total_seconds() > 60:
            mid = from_dt + (to_dt - from_dt) / 2
            mid_iso = mid.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            logger.info(
                f"events/list truncated at {take} for {topic_id} "
                f"{from_iso}~{to_iso} — splitting"
            )
            left = list_events(
                topic_id, from_iso, mid_iso, take, category_ids,
                per_date_limit, tz, _depth + 1, _max_depth,
            )
            right = list_events(
                topic_id, mid_iso, to_iso, take, category_ids,
                per_date_limit, tz, _depth + 1, _max_depth,
            )
            seen = {e.get("id"): e for e in left + right if e.get("id")}
            return list(seen.values())
    return events


def create_event(
    topic_id: str,
    summary: str,
    start,
    end,
    all_day: bool = False,
    start_timezone: str = "Asia/Seoul",
    end_timezone: str = "Asia/Seoul",
    description: Optional[str] = None,
    link: Optional[str] = None,
    location: Optional[str] = None,
    category_id: Optional[str] = None,
    rrule: Optional[str] = None,
    color: Optional[str] = None,
) -> Optional[str]:
    """events/create. summary가 100자 넘으면 ...로 자름. 성공 시 event id.

    color는 OpenAPI 스키마에 없지만 봇 일부가 쓰던 옛 필드라 호환성 위해 전달 가능.
    """
    if len(summary) > 100:
        summary = summary[:97] + "..."
    body: dict = {
        "topicId": topic_id,
        "summary": summary,
        "start": _iso_z(start),
        "end": _iso_z(end),
        "allDay": all_day,
        "startTimezone": start_timezone,
        "endTimezone": end_timezone,
    }
    if description:
        body["description"] = description
    if link:
        body["link"] = link
    if location:
        body["location"] = location
    if category_id:
        body["categoryId"] = category_id
    if rrule:
        body["rrule"] = rrule
    if color:
        body["color"] = color

    resp = _post("/events/create", body)
    if not resp:
        return None
    return _extract_id(resp["body"])


def update_event(
    event_id: str,
    summary: Optional[str] = None,
    start=None,
    end=None,
    all_day: Optional[bool] = None,
    start_timezone: Optional[str] = None,
    end_timezone: Optional[str] = None,
    description: Optional[str] = None,
    link: Optional[str] = None,
    location: Optional[str] = None,
    category_id: Optional[str] = None,
    rrule: Optional[str] = None,
) -> tuple[bool, int]:
    """events/update (partial). 신 API는 id만 필수. (성공여부, status_code) 반환.

    status_code == 404면 호출자가 fallback POST를 해야 한다.
    update_or_create_event를 쓰면 자동 처리.
    """
    body: dict = {"id": event_id}
    if summary is not None:
        if len(summary) > 100:
            summary = summary[:97] + "..."
        body["summary"] = summary
    if start is not None:
        body["start"] = _iso_z(start)
    if end is not None:
        body["end"] = _iso_z(end)
    if all_day is not None:
        body["allDay"] = all_day
    if start_timezone is not None:
        body["startTimezone"] = start_timezone
    if end_timezone is not None:
        body["endTimezone"] = end_timezone
    if description is not None:
        body["description"] = description
    if link is not None:
        body["link"] = link
    if location is not None:
        body["location"] = location
    if category_id is not None:
        body["categoryId"] = category_id
    if rrule is not None:
        body["rrule"] = rrule

    resp = _post("/events/update", body)
    if not resp:
        return False, 0
    sc = resp["status"]
    return sc in (200, 201, 204), sc


def update_or_create_event(
    event_id: Optional[str],
    *,
    topic_id: str,
    summary: str,
    start,
    end,
    all_day: bool = False,
    start_timezone: str = "Asia/Seoul",
    end_timezone: str = "Asia/Seoul",
    description: Optional[str] = None,
    link: Optional[str] = None,
    location: Optional[str] = None,
    category_id: Optional[str] = None,
    rrule: Optional[str] = None,
    cache: Optional["TopicCache"] = None,
) -> Optional[str]:
    """id 있으면 update 시도, 404면 자동 처리. 없으면 바로 POST.

    v1→v2 마이그레이션 후 v1 UUID event_id 잔존 케이스 대응용.

    cache가 주어지면 **2단계 fallback**:
      1) update_event(event_id) → 200 OK → 반환
      2) 404 → cache.lookup(summary, start)로 v2 id 찾기
         발견 시 그 id로 다시 update_event → 200 OK → 반환 (cache도 새 id로 갱신 안 함, 이미 그 id임)
      3) cache miss or 재시도 실패 → create_event → 새 id 반환 (cache에 추가)

    cache 미주어지면 **1단계 fallback**: 404 → 바로 create_event (중복 위험 있음).

    Returns: 최종 event_id (POST 시 새 id) 또는 None.
    """
    if event_id:
        ok, sc = update_event(
            event_id,
            summary=summary,
            start=start,
            end=end,
            all_day=all_day,
            start_timezone=start_timezone,
            end_timezone=end_timezone,
            description=description,
            link=link,
            location=location,
            category_id=category_id,
            rrule=rrule,
        )
        if ok:
            return event_id
        if sc != 404:
            return None
        # 404 분기
        if cache is not None:
            start_iso = _iso_z(start)
            v2_id = cache.lookup(summary, start_iso)
            if v2_id and v2_id != event_id:
                ok2, _ = update_event(
                    v2_id,
                    summary=summary,
                    start=start,
                    end=end,
                    all_day=all_day,
                    start_timezone=start_timezone,
                    end_timezone=end_timezone,
                    description=description,
                    link=link,
                    location=location,
                    category_id=category_id,
                    rrule=rrule,
                )
                if ok2:
                    logger.info(f"id 복구: {event_id} → {v2_id}")
                    return v2_id
        logger.warning(f"events/update {event_id} → 404, fallback to create")
    new_id = create_event(
        topic_id=topic_id,
        summary=summary,
        start=start,
        end=end,
        all_day=all_day,
        start_timezone=start_timezone,
        end_timezone=end_timezone,
        description=description,
        link=link,
        location=location,
        category_id=category_id,
        rrule=rrule,
    )
    if new_id and cache is not None:
        cache.add(summary, _iso_z(start), new_id)
    return new_id


def delete_event(event_id: str) -> bool:
    resp = _post("/events/delete", {"id": event_id})
    if not resp:
        return False
    return resp["status"] in (200, 204)


def get_event(event_id: str) -> Optional[dict]:
    """단일 이벤트 전체 조회 (description 포함).

    ⚠️ events/list 는 description 을 응답에 포함하지 않는다. description 까지
    필요하면 이 함수를 호출해야 한다. 메인 sync 시 description 까지 미러링이
    필요한 봇은 list_events 로 ID 만 얻은 뒤 이 함수로 보충한다.

    Returns:
        이벤트 dict (id, summary, start, end, description, ... 포함) 또는 None.
    """
    resp = _post("/events/get", {"id": event_id})
    if not resp or resp.get("status") != 200:
        return None
    body = resp.get("body")
    return body if isinstance(body, dict) else None


# ── TopicCache: (summary, start) → id 자연 키 캐시 ──────────────────────────

class TopicCache:
    """토픽 전체 이벤트의 (summary, start) → id 매핑 캐시.

    v1→v2 마이그레이션 후 봇 DB에 잔존하는 옛 UUID event_id가 PATCH 시 404를
    받으면, 같은 (summary, start)로 dal.wiki에 이미 등록된 v2 id를 찾아 복구
    한다. 이걸 안 하면 fallback POST가 중복 이벤트를 만든다.

    사용:
        cache = TopicCache(topic_id)
        cache.load(days_back=60, days_forward=365)
        update_or_create_event(old_id, topic_id=..., ..., cache=cache)
    """

    def __init__(self, topic_id: str):
        self.topic_id = topic_id
        self._map: dict[tuple[str, str], str] = {}

    def load(self, days_back: int = 60, days_forward: int = 365,
             chunk_days: int = 49) -> int:
        """토픽 이벤트를 시간 윈도우로 청크 분할 fetch 후 (summary, start) → id 맵 구축.

        list_events 자체에 truncation 자동 분할이 있지만, days_back+days_forward가
        클 때 명시적 청크가 더 효율적이라 자체 청크.
        """
        from datetime import datetime, timedelta, timezone
        now = datetime.now(tz=timezone.utc)
        cur = now - timedelta(days=days_back)
        end_t = now + timedelta(days=days_forward)
        m: dict[tuple[str, str], str] = {}
        while cur < end_t:
            nxt = min(cur + timedelta(days=chunk_days), end_t)
            try:
                evs = list_events(self.topic_id, from_=cur, to=nxt, take=1000)
                for e in evs:
                    eid = e.get("id")
                    summary = (e.get("summary") or "").strip()
                    start = e.get("start") or ""
                    if eid and summary and start:
                        m[(summary, start)] = eid
            except Exception as ex:
                logger.warning(f"TopicCache.load {cur.date()}~{nxt.date()} 실패: {ex}")
            cur = nxt
        self._map = m
        logger.info(f"TopicCache[{self.topic_id}] loaded: {len(m)} entries")
        return len(m)

    def lookup(self, summary: str, start_iso: Optional[str]) -> Optional[str]:
        """(summary, start) 자연 키로 v2 id 조회. load()되지 않았으면 빈 맵에서 None."""
        if not start_iso:
            return None
        return self._map.get((summary.strip(), start_iso))

    def add(self, summary: str, start_iso: Optional[str], event_id: str) -> None:
        """POST 직후 호출해서 캐시 갱신. start_iso None이면 무시."""
        if not start_iso:
            return
        self._map[(summary.strip(), start_iso)] = event_id

    def __len__(self) -> int:
        return len(self._map)

    def __contains__(self, key: tuple[str, str]) -> bool:
        return key in self._map
