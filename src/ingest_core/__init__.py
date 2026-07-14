"""ingest_core — dal.wiki 데드라인 캘린더 공용 수집 파이프라인.

    소스 폴링 → 키워드 필터 → LLM 구조화(confidence) → 캘린더 라우팅

1-A(press-monitor, RSS)와 1-B(notice-differ, HTML목록 diff)를 하나의 코어로 통합.
소스 타입만 어댑터로 교체한다.

    from ingest_core import RSSAdapter, HtmlListAdapter, KeywordRouter, Structurer, SeenStore
    from ingest_core.models import RawItem, StructuredEvent
"""
from .models import RawItem, StructuredEvent
from .keyword import KeywordRouter
from .state import SeenStore
from .structurer import Structurer
from .adapters import RSSAdapter, HtmlListAdapter
from . import discord
from . import fetch

__all__ = [
    "RawItem", "StructuredEvent",
    "KeywordRouter", "SeenStore", "Structurer",
    "RSSAdapter", "HtmlListAdapter",
    "discord", "fetch",
]
