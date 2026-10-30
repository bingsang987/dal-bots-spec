"""dal.wiki API v2 client — production-grade.

dalwiki_client_v2.py (migration template) 기반으로 다음 기능을 추가했다:
- list_events: 응답이 take cap을 채우면 시간 윈도우를 절반으로 쪼개 재귀 호출 (truncation 자동 분할)
- update_event: 404 시 자동 fallback 함수 update_or_create_event 제공
- categoryIds, tz 같은 신 API 파라미터 노출
- DRY_RUN 분기(쓰기만 차단, 조회 POST는 실제 호출), X-Api-Key 자동 첨부
- summary 100자 자동 클립 + 2자 미만 가드, description 2자 미만 omit, location 64자 절단
- list_events 실패(400/5xx/재시도 소진) 시 ListFailed 예외 — 호출자가 "일부 누락"을 반드시 인지

모든 datetime 입력은 ISO 8601 UTC Z 포맷 (`2026-05-28T03:00:00.000Z`)으로 정규화된다.
aware datetime / Unix ms / ISO 문자열(Z, +09:00, 밀리초 유무 무관) 모두 `_iso_z`가
`.000Z`로 맞춘다. KST/+09:00 오프셋 포맷을 그대로 보내면 dal.wiki가 400을 던지므로
문자열도 반드시 이 함수를 거친다.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import date, datetime, timezone
from typing import Iterable, Optional

import requests

logger = logging.getLogger(__name__)

API_BASE = os.environ.get("DALWIKI_API_BASE", "https://api.dal.wiki/api-reference")
API_KEY = os.environ.get("DALWIKI_API_KEY", "")
TIMEOUT = int(os.environ.get("DALWIKI_TIMEOUT", "20"))
# import 시점 스냅샷(구 호환). 실제 판정은 _is_dry_run()이 환경변수를 매 호출 다시 읽는다 —
# botkit.run이 --dry-run 처리로 os.environ["DRY_RUN"]을 import 이후에 세팅하기 때문.
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

# events/list take 상한 (dal.wiki API v2)
MAX_TAKE = 1000
# events/list from~to 창 상한(서버 'window must be ≤ 50 days'). 긴 창은 이 단위로 자동 분할.
MAX_WINDOW_DAYS = 49

# DRY_RUN 중에도 실제로 호출하는 조회 전용 엔드포인트(pointfarm-cb4).
# 드라이런에서 가짜 id/빈 목록을 돌려주면 "중복 생성/삭제 없음"을 검증할 수 없다.
_READ_ONLY_PATHS = frozenset({
    "/events/list",
    "/events/get",
    "/topics/get",
    "/topics/histories",
    "/categories/list",
    "/categories/get",
    "/categories/tree",      # 실제 카테고리 조회 엔드포인트(POST). dalbots-bmi.209
    "/legacy/resolveTopicByName",
})

_session: Optional[requests.Session] = None
_topic_cache: dict[str, str] = {}
_dry_run_counter = 0


class DalWikiError(Exception):
    """dal.wiki 클라이언트 공통 예외 베이스."""


class ListFailed(DalWikiError):
    """events/list 가 실패했다(400/5xx/네트워크 재시도 소진).

    예전엔 조용히 `[]`를 돌려줘서 호출자가 "이 범위에 이벤트가 없다"로 오인하고
    prune/DAY_WIPE/CREATE를 폭주시켰다(dalbots-bmi.12). 이제 기본으로 이 예외를 던진다.
    `list_events(..., raise_on_fail=False)`로 구 동작을 유지할 수 있다.
    """

    def __init__(self, topic_id: str, status: Optional[int], from_iso=None, to_iso=None,
                 detail: str = ""):
        self.topic_id = topic_id
        self.status = status
        self.from_iso = from_iso
        self.to_iso = to_iso
        self.detail = detail
        rng = f" {from_iso}~{to_iso}" if (from_iso or to_iso) else ""
        st = f"HTTP {status}" if status else "네트워크 재시도 소진"
        super().__init__(f"events/list 실패 topic={topic_id}{rng}: {st} {detail}".rstrip())


def _is_dry_run() -> bool:
    """DRY_RUN 판정. 모듈 상수(import 시점) 또는 현재 환경변수 둘 중 하나라도 true면 true."""
    return DRY_RUN or os.environ.get("DRY_RUN", "false").lower() == "true"


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"Content-Type": "application/json"})
        key = API_KEY or os.environ.get("DALWIKI_API_KEY", "")
        if key:
            _session.headers["X-Api-Key"] = key
    return _session


def _post(path: str, body: dict, retries: int = 3):
    """공통 POST. 200/201/204 OK. 429/5xx는 exponential backoff. 404는 응답에 status를
    표시해 호출자가 처리할 수 있게 한다.

    DRY_RUN이면 쓰기 엔드포인트만 가짜 응답(`dry-…` id)을 돌려주고, `_READ_ONLY_PATHS`에
    속한 조회 엔드포인트는 실제로 호출한다.

    Returns:
        ({"status": int, "body": <json|None>}) on completion.
        None if all retries exhausted (network error).
    """
    if _is_dry_run() and path not in _READ_ONLY_PATHS:
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
                    resp_body = resp.json()
                except Exception:
                    resp_body = None
                try:
                    from . import discord_notify
                    discord_notify.enqueue(path, body, resp_body)
                except Exception as _e:
                    logger.debug("discord_notify enqueue failed: %s", _e)
                return {"status": sc, "body": resp_body}
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

_Z_FMT = "%Y-%m-%dT%H:%M:%S.000Z"


def _iso_z(value) -> Optional[str]:
    """어떤 입력이든 dal.wiki 서버 포맷 `YYYY-MM-DDTHH:MM:SS.000Z`(UTC, 밀리초 000)로 정규화.

    - aware datetime → UTC 변환
    - naive datetime → ValueError (tz 모호성은 호출자가 해소)
    - int/float → Unix ms
    - str → ISO 8601 파싱 후 재포맷. `Z`, `+09:00`, 밀리초 있음/없음, 날짜만(`YYYY-MM-DD`)
      모두 허용. tz 정보가 없는 문자열은 UTC로 간주(경고 로그). 파싱 불가 → ValueError.
      (figurebot/onlinegamebot이 `T00:00:00Z`를 보내 TopicCache 키와 어긋나 404 복구가
      무력화됐던 사고의 재발 방지.)
    - None → None

    서브초는 버린다(`.000`) — 서버가 돌려주는 start 와 문자열 비교가 성립해야 한다.
    """
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.endswith(("Z", "z")):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError as e:
            raise ValueError(f"ISO 8601 문자열이 아니다: {value!r}") from e
        if dt.tzinfo is None:
            logger.warning(f"_iso_z: tz 없는 문자열 {value!r} → UTC로 간주")
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime(_Z_FMT)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("dal.wiki API는 naive datetime을 받지 않는다 — tzinfo 명시 필수")
        return value.astimezone(timezone.utc).strftime(_Z_FMT)
    if isinstance(value, date):
        # date → 그날 00:00 UTC. (KST 종일 이벤트는 호출자가 aware datetime으로 넘길 것)
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).strftime(_Z_FMT)
    if isinstance(value, bool):
        raise TypeError("bool은 datetime 값이 아니다")
    if isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        return dt.strftime(_Z_FMT)
    raise TypeError(f"unsupported datetime value: {type(value)}")


# ── 필드 가드 (dal.wiki 스키마 제약을 클라이언트에서 일괄 처리) ────────────────

SUMMARY_MAX = 100
SUMMARY_MIN = 2
DESCRIPTION_MIN = 2
LOCATION_MAX = 64
LOCATION_MIN = 2
# events/update 가 null 로 지울 수 있는 필드(OpenAPI: description/location/link/rrule 은 string|null).
# 빈 문자열은 서버 400(minLength 2 / uri) → 지울 땐 반드시 null. dalbots-bmi.216
CLEARABLE_FIELDS = {"description": "description", "location": "location", "link": "link", "rrule": "rrule"}


def _guard_summary(summary) -> str:
    """summary: 공백 정리 → 2자 미만이면 보정(빈 값 '(제목 없음)', 1자는 ' (일정)' 덧붙임)
    → 100자 초과면 '...'로 클립."""
    s = (summary or "").strip() if isinstance(summary, str) else str(summary or "").strip()
    if len(s) == 0:
        logger.warning("summary 가 비어 있어 '(제목 없음)' 으로 대체")
        s = "(제목 없음)"
    elif len(s) < SUMMARY_MIN:
        logger.warning(f"summary {s!r} 가 {SUMMARY_MIN}자 미만 → ' (일정)' 덧붙임")
        s = f"{s} (일정)"
    if len(s) > SUMMARY_MAX:
        s = s[: SUMMARY_MAX - 3] + "..."
    return s


def _guard_description(description) -> Optional[str]:
    """description: 공백만/2자 미만이면 None(전송 생략)."""
    if description is None:
        return None
    d = str(description)
    if len(d.strip()) < DESCRIPTION_MIN:
        return None
    return d


def _guard_location(location) -> Optional[str]:
    """location: 빈 값 None, 64자 초과면 63자 + '…'."""
    if location is None:
        return None
    loc = str(location).strip()
    if len(loc) < LOCATION_MIN:          # OpenAPI: location minLength 2 → 1자는 400. 생략(dalbots-bmi.231)
        return None
    if len(loc) > LOCATION_MAX:
        logger.info(f"location {LOCATION_MAX}자 초과 → 절단: {loc[:20]}…")
        loc = loc[: LOCATION_MAX - 1] + "…"
    return loc


# ── 이벤트 규칙 경고 (spec/04-cross-cutting.md §11) ───────────────────────────
# 지금은 경고만 한다 — 막거나 고치지 않는다. 로그에서 `RULE-WARN` 으로 grep 해 위반 봇을 찾고,
# 위반이 사라지면 자동 교정/차단으로 올린다(dalbots-fx8).

_SUMMARY_PREFIX_RE = re.compile(r"^\s*\[[^\]]+\]")
_ANCHOR_RE = re.compile(r"<a\b[^>]*>", re.IGNORECASE)
_TARGET_BLANK_RE = re.compile(r"""target\s*=\s*["']?_blank""", re.IGNORECASE)


def _warn_rule_violations(where: str, ref: str, *, summary=None, start=None, end=None,
                          all_day=None, description=None, category_id=None,
                          require_category: bool = False) -> list[str]:
    """규칙 위반을 찾아 `RULE-WARN` 경고 로그를 남기고 위반 이름 목록을 돌려준다.

    None 인 필드는 검사하지 않는다(update 의 '서버 값 유지'). 검사 자체가 실패해도 쓰기는 막지 않는다.
    """
    found: list[str] = []
    try:
        if require_category and not category_id:
            found.append("categoryId 누락")
        if isinstance(summary, str) and _SUMMARY_PREFIX_RE.match(summary):
            found.append("summary [카테고리] prefix")
        if all_day and start is not None and end is not None and _iso_z(start) == _iso_z(end):
            found.append("종일 start==end")
        if isinstance(description, str):
            if "출처" in description:
                found.append("본문 출처 표기")
            if any(not _TARGET_BLANK_RE.search(a) for a in _ANCHOR_RE.findall(description)):
                found.append("링크 target=_blank 누락")
    except Exception as e:  # 경고 검사는 절대 쓰기를 깨뜨리면 안 된다
        logger.debug(f"_warn_rule_violations 검사 실패: {e}")
        return found
    for rule in found:
        logger.warning(f"RULE-WARN {where} {ref}: {rule} — summary={summary!r}")
    return found


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
    raise_on_fail: bool = True,
) -> list[dict]:
    """events/list. take cap에 걸리면 시간 윈도우를 절반으로 쪼개 재귀 호출.

    Args:
        from_, to: ISO Z 문자열 / datetime / Unix ms 정수 / None
        take: 1~1000 (clamped). 응답이 정확히 take면 truncation으로 간주.
            ⚠️ from/to 둘 중 하나라도 없으면 truncation 분할이 꺼진다(쪼갤 윈도우가 없음).
        category_ids, per_date_limit, tz: 신 API 신규 파라미터
        raise_on_fail: True(기본)면 400/5xx/재시도 소진 시 ListFailed 예외.
            False면 구 동작(빈 리스트 반환 + error 로그) — "없음"과 "실패"를 구분 못 하므로
            prune/삭제 판단에 쓰는 호출에서는 절대 False로 두지 말 것.

    Raises:
        ListFailed: raise_on_fail=True 이고 호출이 실패했을 때.
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

    # 서버 제약: window ≤ 50일. 더 긴 from~to 는 49일 조각으로 나눠 호출하고 id 로 합친다(dalbots-bmi.223).
    if from_iso and to_iso and _depth == 0:
        from datetime import timedelta as _td
        f_dt = datetime.fromisoformat(from_iso.replace("Z", "+00:00"))
        t_dt = datetime.fromisoformat(to_iso.replace("Z", "+00:00"))
        if t_dt - f_dt > _td(days=MAX_WINDOW_DAYS):
            merged: dict[str, dict] = {}
            cur = f_dt
            while cur < t_dt:
                nxt = min(cur + _td(days=MAX_WINDOW_DAYS), t_dt)
                for e in list_events(topic_id, cur, nxt, take, category_ids, per_date_limit, tz,
                                     _depth=1, _max_depth=_max_depth, raise_on_fail=raise_on_fail):
                    if e.get("id"):
                        merged[e["id"]] = e
                cur = nxt
            return list(merged.values())

    resp = _post("/events/list", body)
    if not resp or resp.get("status") not in (200, 201):
        status = resp.get("status") if resp else None
        err = ListFailed(topic_id, status, from_iso, to_iso)
        if raise_on_fail:
            raise err
        logger.error(f"{err} (raise_on_fail=False → [] 반환)")
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
                per_date_limit, tz, _depth + 1, _max_depth, raise_on_fail,
            )
            right = list_events(
                topic_id, mid_iso, to_iso, take, category_ids,
                per_date_limit, tz, _depth + 1, _max_depth, raise_on_fail,
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
    """events/create. 성공 시 event id.

    필드 가드(클라이언트 일괄): summary 2자 미만 보정·100자 클립, description 2자 미만
    생략, location 64자 절단. start/end는 `_iso_z`로 `.000Z` 정규화.

    color는 OpenAPI 스키마에 없지만 봇 일부가 쓰던 옛 필드라 호환성 위해 전달 가능.
    """
    _warn_rule_violations(
        "create", f"topic={topic_id}", summary=summary, start=start, end=end, all_day=all_day,
        description=description, category_id=category_id, require_category=True,
    )
    summary = _guard_summary(summary)
    description = _guard_description(description)
    location = _guard_location(location)
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
    *,
    clear_empty: Iterable[str] = (),
) -> tuple[bool, int]:
    """events/update (partial). 신 API는 id만 필수. (성공여부, status_code) 반환.

    None 인 필드는 보내지 않는다(서버 값 유지). description 2자 미만·빈 location 도 생략된다.

    clear_empty: 지울 수 있는 필드 이름("description"·"location"·"link"·"rrule") 중, 이번 값이 None/빈 값
        (가드 후)이면 **null 을 보내 서버 값을 지울** 필드. 기본 ()= 구 동작(빈 값은 생략 → 서버 값 잔존).
        소스에서 비운 본문·장소·링크를 미러에서도 지워야 하는 호출자만 쓴다(.216).

    404 처리: 이 함수는 (False, 404)만 돌려준다. 메인·공유 토픽 쓰기에서 404 는 보통 사람이 지운
    이벤트라 **실패로 다뤄야 한다**(W8 규칙 — 되살리지 않음, .217). 새로 만들어도 되는 경우(봇 전용 개별
    미러 등)에만 호출자가 create 하거나 update_or_create_event 를 쓴다.
    """
    unknown = set(clear_empty) - set(CLEARABLE_FIELDS)
    if unknown:
        raise ValueError(f"clear_empty 는 {sorted(CLEARABLE_FIELDS)} 중에서: {sorted(unknown)}")
    _warn_rule_violations(
        "update", f"id={event_id}", summary=summary, start=start, end=end, all_day=all_day,
        description=description,
    )
    body: dict = {"id": event_id}
    if summary is not None:
        body["summary"] = _guard_summary(summary)
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
        # 2자 미만(공백만 등)은 서버 400 → 전송 생략. 비우고 싶으면 호출자가 명시적으로 처리.
        d = _guard_description(description)
        if d is not None:
            body["description"] = d
    if link is not None:
        body["link"] = link
    if location is not None:
        loc = _guard_location(location)
        if loc is not None:
            body["location"] = loc
    if category_id is not None:
        body["categoryId"] = category_id
    if rrule is not None:
        body["rrule"] = rrule
    for name in clear_empty:
        api = CLEARABLE_FIELDS[name]
        if api not in body:
            body[api] = None

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
    create_on_404: bool = True,
) -> Optional[str]:
    """id 있으면 update 시도, 404면 자동 처리. 없으면 바로 POST.

    v1→v2 마이그레이션 후 v1 UUID event_id 잔존 케이스 대응용.

    cache가 주어지면 **2단계 fallback**:
      1) update_event(event_id) → 200 OK → 반환
      2) 404 → cache 에서 같은 (summary, start) 의 다른 id 찾기. category_id 를 주면 같은 카테고리 →
         카테고리 없는 이벤트 순으로만 본다(공유 토픽에서 다른 멤버의 같은 제목·시각 '휴방'을 덮지 않게, .217).
         발견 시 그 id로 다시 update_event → 200 OK → 반환
      3) cache miss or 재시도 실패 → create_on_404 면 create_event → 새 id 반환 (cache에 추가)

    cache 미주어지면 **1단계 fallback**: 404 → 바로 create_event (중복 위험 있음).

    create_on_404: False 면 3) 에서 create 하지 않고 None(실패) — 메인·공유 토픽처럼 404 = 사람 삭제로
        봐야 하는 쓰기(W8 규칙). 기본 True(구 동작: 개별 미러 등 되살려도 되는 쓰기).

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
            if category_id:
                v2_id = (cache.lookup(summary, start_iso, category_id=category_id, exclude=[event_id])
                         or cache.lookup(summary, start_iso, category_id=None, exclude=[event_id]))
            else:
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
        if not create_on_404:
            logger.warning(f"events/update {event_id} → 404, create_on_404=False → 실패(되살리지 않음)")
            return None
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
        cache.add(summary, _iso_z(start), new_id, category_id)
    return new_id


def delete_event(event_id: str) -> bool:
    ok, _ = delete_event_status(event_id)
    return ok


def delete_event_status(event_id: str) -> tuple[bool, Optional[int]]:
    """delete_event 와 같지만 (ok, status) 를 돌려준다. status None = 네트워크 재시도 소진.
    404(이미 없음)를 실패와 구분해야 하는 호출자(SyncEngine absent 삭제)용."""
    resp = _post("/events/delete", {"id": event_id})
    if not resp:
        return False, None
    return resp["status"] in (200, 204), resp["status"]


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


# ── 카테고리 ──────────────────────────────────────────────────────────────

def categories_tree(topic_id: str) -> list[dict]:
    """/categories/tree — 토픽의 카테고리 목록(조회 전용, DRY_RUN 에서도 실제 호출).

    실패(네트워크 소진·HTTP≠200)는 DalWikiError 로 올린다 — '카테고리 없음'과 '조회 실패'를
    구분해야 봇이 카테고리를 중복 생성하거나 categoryId 없이 등록하지 않는다(dalbots-bmi.209).
    """
    resp = _post("/categories/tree", {"topicId": topic_id})
    if not resp or resp.get("status") != 200:
        raise DalWikiError(f"categories/tree 실패 topic={topic_id}: "
                           f"{'HTTP ' + str(resp.get('status')) if resp else '네트워크 재시도 소진'}")
    body = resp.get("body")
    if isinstance(body, dict):
        body = body.get("categories") or body.get("items") or body.get("tree") or []
    return [c for c in (body or []) if isinstance(c, dict)]


# ── TopicCache: (summary, start) → id 자연 키 캐시 ──────────────────────────

_ANY = object()   # TopicCache.lookup: category_id 미지정(구 동작) 표식


class TopicCache:
    """토픽 전체 이벤트의 (summary, start) → id 매핑 캐시.

    v1→v2 마이그레이션 후 봇 DB에 잔존하는 옛 UUID event_id가 PATCH 시 404를
    받으면, 같은 (summary, start)로 dal.wiki에 이미 등록된 v2 id를 찾아 복구
    한다. 이걸 안 하면 fallback POST가 중복 이벤트를 만든다.

    사용:
        cache = TopicCache(topic_id)
        cache.load(days_back=60, days_forward=365)
        update_or_create_event(old_id, topic_id=..., ..., cache=cache)

    같은 (summary, start) 가 여러 건이면(공유 토픽의 카테고리별 '휴방' 등) `_map` 은 마지막 것만
    가리키지만 `_multi` 엔 전부 남는다. `lookup(..., category_id=X)` 는 그중 카테고리 X 인 것만 본다.
    """

    def __init__(self, topic_id: str):
        self.topic_id = topic_id
        self._map: dict[tuple[str, str], str] = {}
        self._multi: dict[tuple[str, str], list[str]] = {}   # 같은 키의 모든 id(로드 순)
        self._cat: dict[str, Optional[str]] = {}               # id → categoryId
        self.ids: set[str] = set()            # 윈도우 안에 살아있는 모든 event id
        self.events: dict[str, dict] = {}     # id → events/list 원본 dict
        self.complete: bool = False           # 마지막 load가 청크 누락 없이 끝났는가
        self.failed_chunks: list[tuple[str, str]] = []
        self.window: Optional[tuple[datetime, datetime]] = None

    def load(self, days_back: int = 60, days_forward: int = 365,
             chunk_days: int = 49, strict: bool = True) -> int:
        """토픽 이벤트를 시간 윈도우로 청크 분할 fetch 후 (summary, start) → id 맵 구축.

        list_events 자체에 truncation 자동 분할이 있지만, days_back+days_forward가
        클 때 명시적 청크가 더 효율적이라 자체 청크.

        Args:
            strict: True(기본)면 청크 하나라도 ListFailed 면 그대로 재던진다 — 캐시가
                불완전한 채로 404 복구/absent 판정을 하면 중복 생성·오삭제가 난다.
                False면 구 동작(경고 로그 후 계속, `self.complete=False`, `failed_chunks` 기록).

        Raises:
            ListFailed: strict=True 이고 어느 청크든 조회가 실패했을 때.
        """
        from datetime import timedelta
        now = datetime.now(tz=timezone.utc)
        cur = now - timedelta(days=days_back)
        end_t = now + timedelta(days=days_forward)
        self.window = (cur, end_t)
        m: dict[tuple[str, str], str] = {}
        multi: dict[tuple[str, str], list[str]] = {}
        cats: dict[str, Optional[str]] = {}
        ids: set[str] = set()
        events: dict[str, dict] = {}
        failed: list[tuple[str, str]] = []
        while cur < end_t:
            nxt = min(cur + timedelta(days=chunk_days), end_t)
            try:
                evs = list_events(self.topic_id, from_=cur, to=nxt, take=1000)
            except ListFailed as ex:
                if strict:
                    logger.error(f"TopicCache.load {cur.date()}~{nxt.date()} 실패 → 중단: {ex}")
                    raise
                logger.warning(f"TopicCache.load {cur.date()}~{nxt.date()} 실패(비엄격): {ex}")
                failed.append((_iso_z(cur), _iso_z(nxt)))
                evs = []
            for e in evs:
                eid = e.get("id")
                if not eid:
                    continue
                ids.add(eid)
                events[eid] = e
                cats[eid] = e.get("categoryId") or None
                summary = (e.get("summary") or "").strip()
                start = e.get("start") or ""
                if summary and start:
                    m[(summary, start)] = eid
                    lst = multi.setdefault((summary, start), [])
                    if eid not in lst:
                        lst.append(eid)
            cur = nxt
        self._map = m
        self._multi = multi
        self._cat = cats
        self.ids = ids
        self.events = events
        self.failed_chunks = failed
        self.complete = not failed
        logger.info(
            f"TopicCache[{self.topic_id}] loaded: {len(m)} entries"
            + (f", {len(failed)} chunk(s) FAILED" if failed else "")
        )
        return len(m)

    @staticmethod
    def _key(summary: str, start_iso) -> Optional[tuple[str, str]]:
        if not start_iso:
            return None
        try:
            start_iso = _iso_z(start_iso)
        except (ValueError, TypeError):
            pass
        return (_guard_summary(summary), start_iso)

    def lookup(self, summary: str, start_iso: Optional[str], *,
               category_id=_ANY, exclude: Optional[Iterable[str]] = None) -> Optional[str]:
        """(summary, start) 자연 키로 v2 id 조회. load()되지 않았으면 빈 맵에서 None.
        start_iso는 `_iso_z`로 정규화해 비교한다.

        category_id/exclude 를 안 주면 구 동작(같은 키 중 마지막 것). category_id 를 주면(None 포함 —
        카테고리 없는 이벤트) 그 카테고리 후보 중 exclude 에 없는 첫 id."""
        key = self._key(summary, start_iso)
        if key is None:
            return None
        if category_id is _ANY and exclude is None:
            return self._map.get(key)
        for eid in self.lookup_all(summary, start_iso, category_id=category_id, exclude=exclude):
            return eid
        return None

    def lookup_all(self, summary: str, start_iso: Optional[str], *,
                   category_id=_ANY, exclude: Optional[Iterable[str]] = None) -> list[str]:
        """같은 (summary, start) 의 모든 id(로드 순). category_id/exclude 로 좁힌다."""
        key = self._key(summary, start_iso)
        if key is None:
            return []
        ex = set(exclude or ())
        want = category_id or None
        return [
            eid for eid in self._multi.get(key, [])
            if eid not in ex and (category_id is _ANY or (self._cat.get(eid) or None) == want)
        ]

    def category_of(self, event_id: str) -> Optional[str]:
        return self._cat.get(event_id)

    def has_id(self, event_id: str) -> bool:
        """마지막 load 윈도우 안에서 그 id가 살아있었는가."""
        return event_id in self.ids

    def in_window(self, start) -> bool:
        """start 가 마지막 load 윈도우 안인가(윈도우 밖은 '없다'고 단정할 수 없다)."""
        if self.window is None:
            return False
        iso = _iso_z(start)
        if not iso:
            return False
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return self.window[0] <= dt < self.window[1]

    def add(self, summary: str, start_iso: Optional[str], event_id: str,
            category_id: Optional[str] = None) -> None:
        """POST 직후 호출해서 캐시 갱신. start_iso None이면 무시."""
        key = self._key(summary, start_iso)
        if key is None:
            return
        self._map[key] = event_id
        lst = self._multi.setdefault(key, [])
        if event_id not in lst:
            lst.append(event_id)
        self._cat[event_id] = category_id or None
        self.ids.add(event_id)

    def discard(self, event_id: str) -> None:
        """DELETE 직후 호출해서 캐시에서 제거."""
        self.ids.discard(event_id)
        self.events.pop(event_id, None)
        self._cat.pop(event_id, None)
        for key, lst in list(self._multi.items()):
            if event_id in lst:
                lst.remove(event_id)
                if not lst:
                    del self._multi[key]
                if self._map.get(key) == event_id:
                    if lst:
                        self._map[key] = lst[-1]
                    else:
                        self._map.pop(key, None)

    def __len__(self) -> int:
        return len(self._map)

    def __contains__(self, key: tuple[str, str]) -> bool:
        return key in self._map
