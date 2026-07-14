"""HTTP 취득 — 3-tier 우아한 degrade.

tier1: requests (기본, 대부분의 정부/공공 게시판)
tier2: Scrapling StealthyFetcher (설치돼 있으면, JS/봇차단 사이트)
tier3: Playwright (설치돼 있으면, 최후)

설치 안 된 tier는 자동 스킵. 반환은 항상 HTML 문자열(실패 시 None).
"""
from __future__ import annotations

from typing import Optional

import requests

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _tier1(url: str, timeout: int, headers: dict) -> Optional[str]:
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200 and r.text:
            r.encoding = r.apparent_encoding or r.encoding
            return r.text
    except Exception:
        return None
    return None


def _tier2_scrapling(url: str) -> Optional[str]:
    try:
        from scrapling.fetchers import StealthyFetcher  # type: ignore
    except Exception:
        return None
    try:
        page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
        return getattr(page, "html_content", None) or getattr(page, "body", None)
    except Exception:
        return None


def _tier3_playwright(url: str, timeout: int) -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        return None
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            pg = b.new_page(user_agent=DEFAULT_UA)
            pg.goto(url, timeout=timeout * 1000, wait_until="networkidle")
            html = pg.content()
            b.close()
            return html
    except Exception:
        return None


def fetch_html(url: str, *, timeout: int = 20, tier: str = "auto",
               headers: Optional[dict] = None) -> Optional[str]:
    """HTML 취득. tier='auto'면 1→2→3 순서로 시도.
    tier='1'|'2'|'3'이면 해당 tier만.
    """
    hdr = {"User-Agent": DEFAULT_UA, **(headers or {})}
    if tier in ("auto", "1"):
        html = _tier1(url, timeout, hdr)
        if html or tier == "1":
            return html
    if tier in ("auto", "2"):
        html = _tier2_scrapling(url)
        if html or tier == "2":
            return html
    if tier in ("auto", "3"):
        return _tier3_playwright(url, timeout)
    return None


def get_soup(url: str, **kw):
    """bs4 BeautifulSoup 반환(설치 필요). 실패 시 None."""
    from bs4 import BeautifulSoup
    html = fetch_html(url, **kw)
    if not html:
        return None
    return BeautifulSoup(html, "html.parser")
