"""RSS 어댑터 — feedparser 기반. 정책브리핑(korea.kr) 부처별 RSS 등.

    adapter = RSSAdapter({"문체부": "https://.../rss", "농식품부": "..."})
    for item in adapter.poll():
        ...
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import feedparser

from ..models import RawItem


def _parse_dt(entry) -> Optional[datetime]:
    for key in ("published_parsed", "updated_parsed"):
        t = getattr(entry, key, None) or entry.get(key) if hasattr(entry, "get") else None
        t = entry.get(key) if hasattr(entry, "get") else getattr(entry, key, None)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


class RSSAdapter:
    def __init__(self, feeds: dict[str, str], source_prefix: str = ""):
        self.feeds = feeds
        self.source_prefix = source_prefix

    def poll(self) -> list[RawItem]:
        items: list[RawItem] = []
        for label, url in self.feeds.items():
            src = f"{self.source_prefix}{label}" if self.source_prefix else label
            try:
                feed = feedparser.parse(url)
            except Exception:
                continue
            for e in feed.entries:
                ext_id = getattr(e, "id", "") or getattr(e, "link", "") or e.get("title", "")
                summary = ""
                if hasattr(e, "summary"):
                    summary = e.summary
                elif e.get("description"):
                    summary = e.get("description")
                items.append(RawItem(
                    source=src,
                    ext_id=ext_id,
                    title=getattr(e, "title", "") or e.get("title", ""),
                    url=getattr(e, "link", "") or e.get("link", ""),
                    published=_parse_dt(e),
                    body=summary,
                    raw={"feed_label": label},
                ))
        return items
