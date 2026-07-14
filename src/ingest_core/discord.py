"""Discord 경보 — 저confidence 수동확인 큐 / 오탐 / 봇 오류 알림.

환경변수 DALWIKI_DISCORD_WEBHOOK_URL(없으면 무동작).
dalwiki_client.discord_notify와 별개로, 봇 단의 능동 알림용.
"""
from __future__ import annotations

import os
from typing import Optional

import requests

WEBHOOK = os.environ.get("DALWIKI_DISCORD_WEBHOOK_URL", "").strip()
USERNAME = os.environ.get("DALWIKI_DISCORD_USERNAME", "dalbot4000")


def is_enabled() -> bool:
    return bool(WEBHOOK)


def alert(message: str, *, title: Optional[str] = None, url: Optional[str] = None) -> bool:
    """간단 경보 전송. 실패해도 예외 안 냄(봇 흐름 방해 금지)."""
    if not WEBHOOK:
        return False
    embed = {"description": message[:1900]}
    if title:
        embed["title"] = title[:250]
    if url:
        embed["url"] = url
    try:
        r = requests.post(WEBHOOK, json={"embeds": [embed], "username": USERNAME},
                          timeout=10)
        return r.status_code < 300
    except Exception:
        return False


def review_queue(events) -> None:
    """needs_review 이벤트 묶음을 한 번에 알림."""
    if not WEBHOOK or not events:
        return
    lines = []
    for e in events:
        d = e.date_start or "?"
        lines.append(f"• [{d}] {e.title} (conf {e.confidence:.2f})\n{e.link}")
    alert("\n\n".join(lines[:15]),
          title=f"🕵 수동확인 필요 {len(events)}건")
