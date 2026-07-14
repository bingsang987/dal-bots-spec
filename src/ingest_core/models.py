"""ingest_core 공용 데이터 모델."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class RawItem:
    """수집기가 뱉는 원자료 한 건 (RSS 엔트리 / 게시판 글)."""
    source: str                       # 소스 라벨 예: "korea.kr/문체부"
    ext_id: str                       # 소스 고유 id (rss guid, 게시글 번호/url)
    title: str
    url: str
    published: Optional[datetime] = None
    body: str = ""                    # 본문(필요 시 fetch로 채움)
    raw: dict = field(default_factory=dict)

    def text(self) -> str:
        return f"{self.title}\n{self.body}".strip()


@dataclass
class StructuredEvent:
    """LLM(또는 규칙)이 원자료에서 뽑아낸 캘린더용 구조화 결과."""
    title: str
    date_start: Optional[str] = None   # ISO 'YYYY-MM-DD' 또는 datetime ISO
    date_end: Optional[str] = None
    time_note: str = ""                # "10:00 발급" 등 시각 텍스트
    target: str = ""                   # 대상/조건 요약
    link: str = ""
    route: Optional[str] = None        # 라우팅 키(어느 캘린더/카테고리)
    confidence: float = 0.0            # 0~1
    needs_review: bool = False         # confidence 낮음 → 수동 승인 큐
    raw_item: Optional[RawItem] = None
