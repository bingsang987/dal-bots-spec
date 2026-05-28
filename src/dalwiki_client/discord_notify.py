"""Discord webhook notification hook for dal.wiki write activity.

Fires a Discord webhook whenever the shared client successfully completes
`/events/create`, `/events/update`, or `/events/delete`. Inert when
`DALWIKI_DISCORD_WEBHOOK_URL` is unset, so this module imposes zero cost
on bots that do not opt in.

Design:
- Worker thread + queue: notifications never block the bot's foreground
  POST call (bots are unaffected by Discord latency or downtime).
- 5s batching window, up to 10 embeds per Discord message (the per-message
  embed cap). Keeps message volume low when stellivebot/aesthervodbot
  produce bursts of dozens of writes per run.
- 429 retry-after honored once; all other webhook failures are swallowed.
- Topic name resolved lazily via `/topics/get` and cached per process.
- atexit handler drains the queue with a bounded timeout so the last
  notifications survive bot exit on short-lived scripts.
"""
from __future__ import annotations

import atexit
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import requests

logger = logging.getLogger(__name__)

WEBHOOK_URL = os.environ.get("DALWIKI_DISCORD_WEBHOOK_URL", "").strip()
WEBHOOK_USERNAME = os.environ.get("DALWIKI_DISCORD_USERNAME", "dalbot4000")
BATCH_WINDOW_SEC = float(os.environ.get("DALWIKI_DISCORD_BATCH_WINDOW", "5.0"))
BATCH_MAX = 10  # Discord cap: 10 embeds per webhook message
QUEUE_MAX = 1000
DRAIN_TIMEOUT_SEC = 10.0

_NOTIFY_PATHS = {
    "/events/create": "CREATE",
    "/events/update": "UPDATE",
    "/events/delete": "DELETE",
}

_COLOR = {"CREATE": 0x57F287, "UPDATE": 0xFEE75C, "DELETE": 0xED4245}
_EMOJI = {"CREATE": "🆕", "UPDATE": "✏️", "DELETE": "🗑️"}

_queue: "queue.Queue[dict]" = queue.Queue(maxsize=QUEUE_MAX)
_worker: Optional[threading.Thread] = None
_worker_lock = threading.Lock()
_shutdown = threading.Event()
_topic_name_cache: dict[str, str] = {}
_event_topic_cache: dict[str, str] = {}  # eventId → topicId, populated lazily for UPDATE link resolution


def is_enabled() -> bool:
    return bool(WEBHOOK_URL)


def enqueue(path: str, body: dict, resp_body) -> None:
    """Public entry — call from client._post() after a 2xx response.

    Inert if webhook not configured. Never raises.
    """
    if not WEBHOOK_URL:
        return
    action = _NOTIFY_PATHS.get(path)
    if action is None:
        return
    _ensure_worker()
    try:
        _queue.put_nowait({
            "action": action,
            "body": dict(body) if isinstance(body, dict) else {},
            "resp_body": dict(resp_body) if isinstance(resp_body, dict) else {},
            "ts": datetime.now(timezone.utc),
        })
    except queue.Full:
        # Drop on overflow rather than block the bot.
        logger.warning("dalwiki discord_notify queue full, dropping %s", action)


def _ensure_worker() -> None:
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(
                target=_run, name="dalwiki-discord-notify", daemon=True
            )
            _worker.start()
            atexit.register(_drain_on_exit)


def _run() -> None:
    while not _shutdown.is_set():
        try:
            first = _queue.get(timeout=1.0)
        except queue.Empty:
            continue
        batch = [first]
        deadline = time.monotonic() + BATCH_WINDOW_SEC
        while len(batch) < BATCH_MAX:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                batch.append(_queue.get(timeout=remaining))
            except queue.Empty:
                break
        _flush(batch)


def _flush(items: list[dict]) -> None:
    if not items or not WEBHOOK_URL:
        return
    embeds = [_to_embed(it) for it in items]
    payload = {"embeds": embeds, "username": WEBHOOK_USERNAME}
    try:
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=10)
    except Exception as e:
        logger.warning("discord webhook failed: %s", e)
        return
    if resp.status_code == 429:
        retry = 2.0
        try:
            retry = float(resp.json().get("retry_after", retry))
        except Exception:
            pass
        time.sleep(min(retry, 30.0))
        try:
            requests.post(WEBHOOK_URL, json=payload, timeout=10)
        except Exception as e:
            logger.warning("discord webhook retry failed: %s", e)
    elif resp.status_code >= 400:
        logger.warning(
            "discord webhook %s: %s", resp.status_code, resp.text[:200]
        )


def _to_embed(item: dict) -> dict:
    action = item["action"]
    body = item.get("body") or {}
    resp_body = item.get("resp_body") or {}

    topic_id = body.get("topicId") or ""
    summary = (body.get("summary") or "").strip()
    start = body.get("start") or ""
    event_id = body.get("id") or resp_body.get("id") or resp_body.get("eventId") or ""

    if summary:
        title = f"{_EMOJI[action]} {summary}"
    else:
        title = f"{_EMOJI[action]} {action} {event_id}"

    # CREATE의 경우 (topicId, eventId) 매핑을 캐시에 저장 — 같은 프로세스에서 이 이벤트가
    # 나중에 UPDATE 받으면 무료 lookup으로 link 부착 가능.
    if action == "CREATE" and event_id and topic_id:
        _event_topic_cache[event_id] = topic_id

    # 이벤트 deep link: https://dal.wiki/t/{topicId}/e/{eventId}
    # CREATE는 body+resp_body에 둘 다 있어 곧장 부착. UPDATE는 봇이 보통 partial PATCH로
    # topicId를 안 보내므로 캐시 → /events/get 순서로 resolve. DELETE는 이미 사라진
    # 이벤트라 link 부착 안 함 (/events/get도 404 반환).
    event_url = ""
    if action != "DELETE" and event_id:
        if not topic_id:
            topic_id = _event_topic_cache.get(event_id) or _resolve_topic_for_event(event_id) or ""
            if topic_id:
                _event_topic_cache[event_id] = topic_id
        if topic_id:
            event_url = f"https://dal.wiki/t/{topic_id}/e/{event_id}"

    fields = []
    if topic_id:
        name = _resolve_topic_name(topic_id)
        fields.append({"name": "토픽", "value": name, "inline": True})
    if start:
        fields.append({"name": "시작", "value": _fmt_time(start), "inline": True})
    if action == "UPDATE":
        changed = [k for k in body.keys() if k not in ("id",)]
        if changed:
            fields.append({
                "name": "변경 필드",
                "value": ", ".join(changed)[:1024],
                "inline": False,
            })

    embed = {
        "title": title[:256],
        "color": _COLOR[action],
        "fields": fields[:10],
        "footer": {"text": f"{action} · {event_id}"[:2048]},
        "timestamp": item["ts"].isoformat(),
    }
    if event_url:
        embed["url"] = event_url
    return embed


def _fmt_time(value: str) -> str:
    """Render ISO Z as KST for human readers."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        kst = dt.astimezone(timezone(_KST_OFFSET))
        return kst.strftime("%m-%d %H:%M KST")
    except Exception:
        return value[:64]


# Avoid importing zoneinfo just for KST offset
from datetime import timedelta as _td
_KST_OFFSET = _td(hours=9)


def _resolve_topic_for_event(event_id: str) -> Optional[str]:
    """eventId → topicId 단발 조회 (/events/get). 결과는 호출자가 _event_topic_cache에 캐싱.

    UPDATE 알림에 deep link 부착하기 위함. 봇이 partial PATCH로 topicId를 안 보내는
    경우가 많은데, eventId만으로도 토픽을 알 수 있어야 클릭 가능 링크 만든다.
    실패(404, 네트워크) 시 None — 호출자는 그냥 링크 없이 진행.
    """
    if not event_id:
        return None
    try:
        from .client import API_BASE, _get_session
        url = f"{API_BASE}/events/get"
        resp = _get_session().post(url, json={"id": event_id}, timeout=10)
        if resp.status_code == 200:
            data = resp.json() or {}
            # 응답 스키마: { ..., "topic": {"id": "...", "name": "..."} }
            # topicId가 top-level에 없으므로 nested 객체에서 추출.
            topic = data.get("topic") or {}
            topic_id = topic.get("id")
            topic_name = topic.get("name")
            # 토픽 이름도 함께 캐싱 — 어차피 받았으니 무료.
            if topic_id and topic_name and topic_id not in _topic_name_cache:
                _topic_name_cache[topic_id] = topic_name
            return topic_id or None
    except Exception as e:
        logger.debug("event→topic lookup failed for %s: %s", event_id, e)
    return None


def _resolve_topic_name(topic_id: str) -> str:
    """Lazy /topics/get lookup, cached per process. Returns topic_id on failure."""
    cached = _topic_name_cache.get(topic_id)
    if cached is not None:
        return cached
    # Import here to avoid circular import at module load
    try:
        from .client import API_BASE, _get_session
        url = f"{API_BASE}/topics/get"
        resp = _get_session().post(url, json={"id": topic_id}, timeout=10)
        if resp.status_code == 200:
            data = resp.json() or {}
            name = (data.get("name") or topic_id).strip() or topic_id
            _topic_name_cache[topic_id] = name
            return name
    except Exception as e:
        logger.debug("topic name lookup failed for %s: %s", topic_id, e)
    _topic_name_cache[topic_id] = topic_id
    return topic_id


def _drain_on_exit() -> None:
    """Best-effort flush of pending notifications when the process exits."""
    if not WEBHOOK_URL:
        return
    deadline = time.monotonic() + DRAIN_TIMEOUT_SEC
    while time.monotonic() < deadline:
        if _queue.empty():
            break
        time.sleep(0.2)
    _shutdown.set()
    # Give the worker a moment to flush its current batch
    if _worker is not None:
        _worker.join(timeout=2.0)
