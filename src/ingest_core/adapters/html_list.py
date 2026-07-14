"""HTML 게시판 목록 어댑터 — CSS 셀렉터 기반 범용 스크래퍼.

각 게시판마다 목록 페이지 구조가 달라 셀렉터를 설정으로 받는다.

    adapter = HtmlListAdapter(
        name="korail/공지",
        list_url="https://www.korail.com/...",
        row_selector="table.bbsList tbody tr",
        title_selector="td.subject a",
        link_selector="td.subject a",     # href 추출
        link_base="https://www.korail.com",
        tier="auto",
    )
    for item in adapter.poll():
        ...

본문(body)은 목록 단계에선 비워 둔다. 키워드 매치된 건만 상세 fetch(비용 절약).
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urljoin

from ..fetch import fetch_html
from ..models import RawItem


class HtmlListAdapter:
    def __init__(self, *, name: str, list_url: str, row_selector: str,
                 title_selector: Optional[str] = None,
                 link_selector: Optional[str] = None,
                 link_base: str = "", tier: str = "auto",
                 id_attr: str = "href"):
        self.name = name
        self.list_url = list_url
        self.row_selector = row_selector
        self.title_selector = title_selector
        self.link_selector = link_selector or title_selector
        self.link_base = link_base
        self.tier = tier
        self.id_attr = id_attr

    def poll(self) -> list[RawItem]:
        from bs4 import BeautifulSoup
        html = fetch_html(self.list_url, tier=self.tier)
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        out: list[RawItem] = []
        for row in soup.select(self.row_selector):
            title_el = row.select_one(self.title_selector) if self.title_selector else row
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if not title:
                continue
            link_el = row.select_one(self.link_selector) if self.link_selector else title_el
            href = ""
            if link_el is not None:
                href = link_el.get("href", "") or ""
            url = urljoin(self.link_base or self.list_url, href) if href else self.list_url
            # ext_id: href의 고유부분(글번호 등) 우선, 없으면 제목
            ext_id = href or title
            out.append(RawItem(
                source=self.name, ext_id=ext_id, title=title, url=url,
                raw={"list_url": self.list_url},
            ))
        return out

    def fetch_body(self, item: RawItem, body_selector: Optional[str] = None) -> str:
        """상세 페이지 본문 취득(키워드 매치된 건만 호출 권장)."""
        from bs4 import BeautifulSoup
        html = fetch_html(item.url, tier=self.tier)
        if not html:
            return ""
        soup = BeautifulSoup(html, "html.parser")
        if body_selector:
            el = soup.select_one(body_selector)
            text = el.get_text("\n", strip=True) if el else ""
        else:
            text = soup.get_text("\n", strip=True)
        item.body = text
        return text
