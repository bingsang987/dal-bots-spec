"""
dal.wiki API v2 클라이언트 템플릿
=====================================
새 API (preprod-api.dal.wiki) 기준으로 작성된 공통 클라이언트.
프로덕션 전환 시 각 봇의 dalwiki.py / dalwiki_client.py를 이 파일로 교체한다.

변경 사항 요약 (구 API → 신 API):
  - 모든 HTTP 메서드가 POST로 통일 (GET/PATCH/DELETE 없음)
  - 이벤트 ID: URL 경로 → request body {"id": "..."}
  - from/to: Unix ms 정수 → ISO 8601 문자열 (래퍼 내부에서 자동 변환)
  - PATCH → partial update (id만 필수, 나머지 선택)
  - 토픽 이름 조회: GET /topics?name= → POST /legacy/resolveTopicByName
  - startTimezone 기본값: 명시 없으면 서버 측 "UTC" 적용 → 반드시 "Asia/Seoul" 명시
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── 설정 ────────────────────────────────────────────────────────────────────
import os

API_BASE = os.environ.get("DALWIKI_API_BASE", "https://api.dal.wiki/api-reference")
API_KEY  = os.environ.get("DALWIKI_API_KEY", "")
TIMEOUT  = int(os.environ.get("DALWIKI_TIMEOUT", "20"))
DRY_RUN  = os.environ.get("DRY_RUN", "false").lower() == "true"

_session: Optional[requests.Session] = None
_dry_run_counter = 0


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"Content-Type": "application/json"})
        if API_KEY:
            _session.headers.update({"Authorization": f"Bearer {API_KEY}"})
    return _session


# ── 내부 유틸 ────────────────────────────────────────────────────────────────

def _ms_to_iso(ms: Optional[int]) -> Optional[str]:
    """Unix milliseconds → ISO 8601 문자열 변환 (구 API 호환용)"""
    if ms is None:
        return None
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _post(path: str, body: dict, retries: int = 3) -> Optional[dict]:
    """모든 API 호출의 공통 POST 래퍼."""
    if DRY_RUN:
        global _dry_run_counter
        _dry_run_counter += 1
        logger.info(f"[DRY-RUN] POST {path} {body}")
        return {"id": f"dry-{int(time.time())}-{_dry_run_counter}"}

    url = f"{API_BASE}{path}"
    session = _get_session()
    last_exc = None

    for attempt in range(1, retries + 1):
        try:
            resp = session.post(url, json=body, timeout=TIMEOUT)
            if resp.status_code in (200, 201, 204):
                try:
                    return resp.json()
                except Exception:
                    return {}
            elif resp.status_code in (429, 502, 503, 504):
                wait = 2 ** (attempt - 1)  # 1s, 2s, 4s
                logger.warning(f"POST {path} → {resp.status_code}, retry {attempt}/{retries} in {wait}s")
                time.sleep(wait)
            else:
                logger.error(f"POST {path} → {resp.status_code}: {resp.text[:200]}")
                return None
        except requests.RequestException as e:
            last_exc = e
            logger.warning(f"POST {path} 네트워크 오류 (attempt {attempt}): {e}")
            time.sleep(2 ** (attempt - 1))

    logger.error(f"POST {path} 최대 재시도 초과. 마지막 오류: {last_exc}")
    return None


def _extract_id(resp: Optional[dict]) -> Optional[str]:
    """응답에서 이벤트 ID 추출 (id / eventId 키 모두 시도)."""
    if not resp:
        return None
    return resp.get("id") or resp.get("eventId")


# ── Topics ───────────────────────────────────────────────────────────────────

_topic_cache: dict[str, str] = {}


def resolve_topic_by_name(topic_name: str) -> Optional[str]:
    """토픽 이름으로 ID 조회 (캐시 적용).
    구 API: GET /topics?name= 또는 GET /topics/{name}
    신 API: POST /legacy/resolveTopicByName
    """
    if topic_name in _topic_cache:
        return _topic_cache[topic_name]

    resp = _post("/legacy/resolveTopicByName", {"topicName": topic_name})
    topic_id = _extract_id(resp) or (resp or {}).get("topicId")
    if topic_id:
        _topic_cache[topic_name] = topic_id
        logger.info(f"토픽 '{topic_name}' → {topic_id}")
    else:
        logger.error(f"토픽 '{topic_name}' ID 조회 실패: {resp}")
    return topic_id


def create_topic(name: str, description: str, thumbnail: Optional[str] = None) -> Optional[str]:
    """토픽 생성.
    구 API: POST /topics
    신 API: POST /topics/create
    """
    body = {"name": name, "description": description}
    if thumbnail:
        body["thumbnail"] = thumbnail
    resp = _post("/topics/create", body)
    return _extract_id(resp)


# ── Events ───────────────────────────────────────────────────────────────────

def list_events(
    topic_id: str,
    from_ms: Optional[int] = None,
    to_ms: Optional[int] = None,
    take: int = 500,
) -> list:
    """이벤트 목록 조회.
    구 API: GET /events?topic_id=&from=ms&to=ms
    신 API: POST /events/list  (from/to가 ISO 8601 문자열로 변경)

    from_ms, to_ms는 기존 봇과의 호환성을 위해 Unix ms를 그대로 받아 내부에서 변환.
    """
    body: dict = {"topicId": topic_id, "take": take}
    if from_ms is not None:
        body["from"] = _ms_to_iso(from_ms)
    if to_ms is not None:
        body["to"] = _ms_to_iso(to_ms)

    resp = _post("/events/list", body)
    if resp is None:
        return []
    # 응답이 리스트인 경우와 {"events": [...]} 래핑인 경우 모두 처리
    if isinstance(resp, list):
        return resp
    return resp.get("events") or resp.get("items") or []


def create_event(
    topic_id: str,
    summary: str,
    start: str,
    end: str,
    all_day: bool,
    start_timezone: str = "Asia/Seoul",
    end_timezone: str = "Asia/Seoul",
    description: Optional[str] = None,
    link: Optional[str] = None,
    location: Optional[str] = None,
    category_id: Optional[str] = None,
    rrule: Optional[str] = None,
) -> Optional[str]:
    """이벤트 생성. 성공 시 이벤트 UUID 반환.
    구 API: POST /events
    신 API: POST /events/create

    주의: startTimezone 미설정 시 서버 기본값이 "UTC"이므로 반드시 명시.
    """
    # summary 길이 제한
    if len(summary) > 100:
        summary = summary[:97] + "..."

    body: dict = {
        "topicId": topic_id,
        "summary": summary,
        "start": start,
        "end": end,
        "allDay": all_day,
        "startTimezone": start_timezone,
        "endTimezone": end_timezone,
    }

    # 선택 필드: 빈값/None이면 키 자체를 생략
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

    resp = _post("/events/create", body)
    event_id = _extract_id(resp)
    if event_id:
        logger.info(f"이벤트 생성: {summary[:40]} → {event_id}")
    else:
        logger.error(f"이벤트 생성 실패: {summary[:40]} / resp={resp}")
    return event_id


def update_event(
    event_id: str,
    summary: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    all_day: Optional[bool] = None,
    start_timezone: Optional[str] = None,
    end_timezone: Optional[str] = None,
    description: Optional[str] = None,
    link: Optional[str] = None,
    location: Optional[str] = None,
    category_id: Optional[str] = None,
    rrule: Optional[str] = None,
) -> bool:
    """이벤트 업데이트 (partial update). 변경할 필드만 전달하면 됨.
    구 API: PATCH /events/{id}  → 전체 필드 필수
    신 API: POST /events/update → id만 필수, 나머지 선택

    필드를 명시적으로 null로 지우려면 해당 파라미터에 None 대신 null sentinel 사용 필요.
    (현재 구현에서는 None = 변경 안 함, null 지우기는 별도 처리)
    """
    body: dict = {"id": event_id}

    if summary is not None:
        if len(summary) > 100:
            summary = summary[:97] + "..."
        body["summary"] = summary
    if start is not None:
        body["start"] = start
    if end is not None:
        body["end"] = end
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
    ok = resp is not None
    if ok:
        logger.info(f"이벤트 업데이트: {event_id}")
    else:
        logger.error(f"이벤트 업데이트 실패: {event_id}")
    return ok


def delete_event(event_id: str) -> bool:
    """이벤트 삭제.
    구 API: DELETE /events/{id}
    신 API: POST /events/delete  body: {"id": "..."}
    """
    resp = _post("/events/delete", {"id": event_id})
    ok = resp is not None
    if ok:
        logger.info(f"이벤트 삭제: {event_id}")
    else:
        logger.error(f"이벤트 삭제 실패: {event_id}")
    return ok
