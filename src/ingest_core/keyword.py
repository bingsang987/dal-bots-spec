"""키워드 1차 필터 + 라우팅 (LLM 전에 무료로 볼륨을 줄인다)."""
from __future__ import annotations

from typing import Iterable, Optional


class KeywordRouter:
    """route_key → 포함 키워드 목록. 텍스트를 어느 캘린더로 보낼지 결정.

        router = KeywordRouter(
            routes={"숙박": ["숙박세일", "야놀자"], "영화": ["영화 할인권"]},
            exclude=["채용", "인사발령"],
        )
        router.route(text)  # -> "숙박" | None
    """

    def __init__(self, routes: dict[str, Iterable[str]],
                 exclude: Optional[Iterable[str]] = None):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.exclude = list(exclude or [])

    def is_excluded(self, text: str) -> bool:
        return any(x in text for x in self.exclude)

    def route(self, text: str) -> Optional[str]:
        """가장 먼저 매치되는 route_key. 없으면 None. exclude 걸리면 None."""
        if self.is_excluded(text):
            return None
        for key, kws in self.routes.items():
            if any(kw in text for kw in kws):
                return key
        return None

    def routes_all(self, text: str) -> list[str]:
        """매치되는 모든 route_key (다중 라우팅용)."""
        if self.is_excluded(text):
            return []
        return [k for k, kws in self.routes.items() if any(kw in text for kw in kws)]

    def passes(self, text: str) -> bool:
        return self.route(text) is not None
