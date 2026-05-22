"""dal.wiki API v2 공통 클라이언트.

봇들은 다음과 같이 쓴다:

    from dalwiki_client import (
        list_events, create_event, update_event, delete_event,
        resolve_topic_by_name, create_topic,
    )

환경변수:
    DALWIKI_API_BASE   기본 https://api.dal.wiki/api-reference
    DALWIKI_API_KEY    필수
    DALWIKI_TIMEOUT    기본 20 (초)
    DRY_RUN            "true"면 실제 호출 안 함, 가짜 id 반환
"""

from .client import (
    API_BASE,
    create_event,
    create_topic,
    delete_event,
    list_events,
    resolve_topic_by_name,
    update_event,
    update_or_create_event,
)

__all__ = [
    "API_BASE",
    "create_event",
    "create_topic",
    "delete_event",
    "list_events",
    "resolve_topic_by_name",
    "update_event",
    "update_or_create_event",
]
