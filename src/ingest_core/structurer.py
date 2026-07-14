"""LLM 구조화 — 원자료 → StructuredEvent (Haiku + 시스템 프롬프트 캐시).

A안(비용 최소화):
  - 모델 Haiku(claude-haiku-4-5). Sonnet 아님.
  - 시스템 프롬프트(공유 지시부)에 cache_control → 반복 호출 시 캐시 히트.
  - 키워드 필터 통과분만 여기 온다(호출 자체가 이미 소수).
  - confidence < threshold → needs_review=True (자동등록 대신 Discord 큐).

mock=True면 API 호출 없이 정규식으로 대충 날짜만 뽑아 반환(개발/드라이런, 비용 0).
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from .models import RawItem, StructuredEvent

MODEL = "claude-haiku-4-5-20251001"

# 공유 시스템 프롬프트(캐시 대상). 봇별 세부는 user 메시지의 extra로.
SYSTEM_PROMPT = """너는 한국 공공/정부 보도자료·공지에서 '행동 데드라인형 일정'을 뽑아 JSON으로 구조화하는 도우미다.

반드시 아래 JSON 스키마 하나만 출력한다(설명·코드블록 금지):
{
  "is_event": true|false,        // 날짜가 있는 실제 신청/예매/발급/마감 일정인가
  "title": "간결한 일정 제목(대괄호 카테고리 금지)",
  "date_start": "YYYY-MM-DD 또는 null",   // 시작/개시일
  "date_end": "YYYY-MM-DD 또는 null",     // 마감일(있으면)
  "time_note": "발급/오픈 시각 텍스트 예: '10:00 오픈' 또는 ''",
  "target": "대상·조건 한 줄 요약",
  "confidence": 0.0~1.0          // 일정 정보의 확실성
}

규칙:
- 날짜가 명시 안 됐거나 '추후 공지'면 is_event=false 또는 confidence 낮게.
- 연도가 본문에 없으면 게시일 기준으로 추론하되 확신 없으면 confidence를 낮춘다.
- 여러 일정이 섞였으면 가장 핵심(신청 시작 또는 마감) 하나만.
"""

_DATE_RE = re.compile(r"(20\d{2})[.\-/년\s]+(\d{1,2})[.\-/월\s]+(\d{1,2})")


def _mock_structure(item: RawItem, route: Optional[str]) -> StructuredEvent:
    m = _DATE_RE.search(item.text())
    ds = None
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        ds = f"{y:04d}-{mo:02d}-{d:02d}"
    return StructuredEvent(
        title=item.title[:80], date_start=ds, link=item.url, route=route,
        confidence=0.3 if ds else 0.1, needs_review=True, raw_item=item,
    )


class Structurer:
    def __init__(self, *, api_key: Optional[str] = None, model: str = MODEL,
                 mock: bool = False, review_threshold: float = 0.6,
                 max_tokens: int = 600):
        self.model = model
        self.mock = mock
        self.review_threshold = review_threshold
        self.max_tokens = max_tokens
        self._client = None
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    def _client_lazy(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def structure(self, item: RawItem, *, route: Optional[str] = None,
                  extra: str = "") -> Optional[StructuredEvent]:
        if self.mock:
            return _mock_structure(item, route)
        try:
            client = self._client_lazy()
            resp = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=[{
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{
                    "role": "user",
                    "content": (f"[출처] {item.source}\n[제목] {item.title}\n"
                                f"[본문]\n{item.body[:4000]}\n\n"
                                + (f"[추가지시] {extra}\n" if extra else "")
                                + "위 내용을 스키마 JSON으로 구조화해라."),
                }],
            )
            raw = resp.content[0].text.strip()
            mobj = re.search(r"\{.*\}", raw, re.DOTALL)
            if not mobj:
                return None
            data = json.loads(mobj.group())
        except Exception:
            return None

        if not data.get("is_event"):
            return None
        conf = float(data.get("confidence", 0) or 0)
        return StructuredEvent(
            title=data.get("title", item.title)[:90],
            date_start=data.get("date_start"),
            date_end=data.get("date_end"),
            time_note=data.get("time_note", "") or "",
            target=data.get("target", "") or "",
            link=item.url,
            route=route,
            confidence=conf,
            needs_review=conf < self.review_threshold,
            raw_item=item,
        )
