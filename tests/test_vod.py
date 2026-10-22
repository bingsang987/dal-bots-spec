"""botkit.vod — VOD 블록/조회/MissingGuard/summary 가드."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from botkit import vod
from dalwiki_client import client as dw

TOPIC = "T1"
CAFE = ('<p class="editor-paragraph"><a href="https://cafe.naver.com/x/1" target="_blank" '
        'rel="noopener noreferrer ugc" class="editor-link"><span>공지</span></a></p>')


# -- VOD 블록 ---------------------------------------------------------------
def test_build_vod_block_index_rule_and_rel():
    b0 = vod.build_vod_block("111", 0)
    b1 = vod.build_vod_block("222", 1)
    assert "<span>치지직 VOD 링크 : </span>" in b0
    assert "치지직 VOD 링크-2 : " in b1
    assert 'rel="noopener noreferrer ugc"' in b0 and "nofollow" not in b0
    assert 'href="https://chzzk.naver.com/video/111"' in b0


def test_append_vod_idempotent_and_numbering():
    d1 = vod.append_vod("", "111")
    assert vod.extract_video_nos(d1) == ["111"]
    assert vod.append_vod(d1, "111") == d1               # 같은 videoNo → no-op
    d2 = vod.append_vod(d1, "222")
    assert vod.extract_video_nos(d2) == ["111", "222"]
    assert "치지직 VOD 링크-2 : " in d2
    assert vod.append_vod(d2, "222") == d2
    assert vod.append_vod(None, "9").startswith('<h3 class="editor-heading-h3">')


def test_append_vod_preserves_cafe_block_above():
    d = vod.append_vod(CAFE, "333")
    assert d.startswith(CAFE)                            # 카페 공지 블록 그대로, 그 아래 append
    assert d[len(CAFE):] == vod.build_vod_block("333", 0)


def test_extract_video_nos_dedup():
    html = vod.build_vod_block("1", 0) + vod.build_vod_block("1", 0) + vod.build_vod_block("2", 1)
    assert vod.extract_video_nos(html) == ["1", "2"]
    assert vod.extract_video_nos(None) == []


# -- safe_summary -------------------------------------------------------------
def test_safe_summary():
    assert vod.safe_summary("  롤  ", fallback="갠방 - 롤") == "갠방 - 롤"
    assert vod.safe_summary("롤") == "방송"
    assert vod.safe_summary("", fallback="x") == "방송"
    assert vod.safe_summary("  저챗 ") == "저챗"
    assert vod.safe_summary("가" * 150) == "가" * 100


# -- fetch_events_chunked -------------------------------------------------------
def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def test_fetch_events_chunked_complete_and_dedup(fake_dal, soon):
    a = fake_dal.add_event(TOPIC, "A", soon)
    b = fake_dal.add_event(TOPIC, "B", soon + timedelta(days=4))
    fake_dal.add_event("OTHER", "C", soon)
    evs, complete = vod.fetch_events_chunked(TOPIC, _ms(soon - timedelta(days=1)), _ms(soon + timedelta(days=8)))
    assert complete is True
    assert sorted(e["id"] for e in evs) == sorted([a, b])
    assert len(fake_dal.paths("/events/list")) == 3          # 9일 / 3일 청크


def test_fetch_events_chunked_partial_failure(fake_dal, soon, monkeypatch):
    fake_dal.add_event(TOPIC, "A", soon)
    real = fake_dal.post
    n = {"list": 0}

    def flaky(path, body, retries=3):
        if path == "/events/list":
            n["list"] += 1
            if n["list"] == 2:                                # 두 번째 청크만 500
                return {"status": 500, "body": None}
        return real(path, body, retries)

    monkeypatch.setattr(dw, "_post", flaky)
    evs, complete = vod.fetch_events_chunked(TOPIC, _ms(soon - timedelta(hours=1)), _ms(soon + timedelta(days=8)))
    assert complete is False                                  # 절대 [] 로 삼키지 않는다
    assert [e["summary"] for e in evs] == ["A"]               # 성공 청크 결과는 유지


def test_fetch_events_chunked_exception_is_incomplete(fake_dal, soon, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("socket")
    monkeypatch.setattr(dw, "list_events", boom)
    evs, complete = vod.fetch_events_chunked(TOPIC, _ms(soon), _ms(soon + timedelta(days=1)))
    assert (evs, complete) == ([], False)


# -- get_description / hydrate ----------------------------------------------------
def test_get_description_ok_empty_and_404(fake_dal, soon):
    a = fake_dal.add_event(TOPIC, "A", soon, description="<p>본문</p>")
    b = fake_dal.add_event(TOPIC, "B", soon)
    assert vod.get_description(a) == "<p>본문</p>"
    assert vod.get_description(b) == ""
    n0 = len(fake_dal.paths("/events/get"))
    assert vod.get_description("nope") is None
    assert len(fake_dal.paths("/events/get")) == n0 + 1       # 4xx 즉시 중단(재시도 없음)


def test_get_description_retries_5xx_then_none(fake_dal, monkeypatch):
    calls = []
    monkeypatch.setattr(dw, "_post", lambda p, b, retries=3: calls.append(p) or {"status": 503, "body": None})
    monkeypatch.setattr(vod.time, "sleep", lambda s: None)
    assert vod.get_description("x") is None
    assert len(calls) == 3


def test_hydrate_descriptions_flags(fake_dal, soon, monkeypatch):
    a = fake_dal.add_event(TOPIC, "A", soon, description="<p>공지</p>")
    evs = [{"id": a}, {"id": "gone", "description": "옛값"}]
    monkeypatch.setattr(vod.time, "sleep", lambda s: None)
    failed = vod.hydrate_descriptions(evs)
    assert failed == 1
    assert evs[0]["description"] == "<p>공지</p>" and evs[0]["_desc_loaded"] and not evs[0]["_desc_failed"]
    assert evs[1]["description"] == "옛값" and evs[1]["_desc_failed"] and not evs[1].get("_desc_loaded")
    n = len(fake_dal.paths("/events/get"))
    vod.hydrate_descriptions(evs[:1])                          # 이미 로드된 건 재호출 안 함
    assert len(fake_dal.paths("/events/get")) == n


# -- MissingGuard ------------------------------------------------------------------
@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


def test_missing_guard_sweep_is_missing_forget(conn):
    g = vod.MissingGuard(conn, scope="indiv")
    mapping = {"m1": "e1", "m2": "e2", "m3": "e3"}
    assert g.sweep(mapping, present_ids={"e1", "e3"}) == ["m2"]
    assert g.is_missing("m2") and not g.is_missing("m1")
    assert g.sweep(mapping, present_ids={"e1", "e3"}) == ["m2"]   # 재호출 멱등
    assert g.keys() == ["m2"]
    g.forget("m2")
    assert not g.is_missing("m2")


def test_missing_guard_scope_isolation_and_persistence(conn):
    vod.MissingGuard(conn, scope="indiv").sweep({"k": "e"}, present_ids=set())
    assert not vod.MissingGuard(conn, scope="group").is_missing("k")
    assert vod.MissingGuard(conn, scope="indiv").is_missing("k")     # 같은 DB 재오픈해도 유지


def test_missing_guard_clears_when_present_again(conn):
    g = vod.MissingGuard(conn, scope="s")
    g.sweep({"k": "e"}, present_ids=set())
    g.sweep({"k": "e"}, present_ids={"e"})
    assert not g.is_missing("k")


def test_missing_guard_dry_run_writes_nothing(conn):
    g = vod.MissingGuard(conn, scope="s", dry_run=True)
    assert g.sweep({"k": "e"}, present_ids=set()) == ["k"]
    assert g.is_missing("k")
    assert conn.execute("SELECT COUNT(*) FROM missing_keys").fetchone()[0] == 0


def test_drop_tombstones(conn):
    conn.execute("CREATE TABLE tombstones (x)")
    vod.drop_tombstones(conn)
    vod.drop_tombstones(conn)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='tombstones'").fetchone() is None


def test_vod_stats_line():
    assert vod.vod_stats_line("cluez", vod_new=2, wipe=0) == "[cluez] vod_new=2"
    assert vod.vod_stats_line("cluez") == "[cluez] no-op"
