"""botkit.vod — liveOpenDate 기반 VOD 시작 시각·분할편 묶음(W9, dalbots-bmi.233)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from botkit import vod

H = 3600 * 1000


def _ms(s: str) -> int:
    """'YYYY-MM-DD HH:MM:SS' KST → ms (테스트 기대값 계산용, 구현과 독립적으로 UTC 로 환산)."""
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000) - 9 * H


def _v(no, title, pub, dur_s):
    return {"videoNo": str(no), "videoTitle": title, "publishDateAt": pub, "duration": dur_s}


# 명예훈장 10-21 실측: 1부 61200s 공개 10-22 14:16:12, 2부 3174s 공개 10-22 15:21:04, liveOpen 10-21 20:59:41
LIVE_1021 = _ms("2026-10-21 20:59:41")
P1 = _v(15802003, "2026년 10월 21일 1부 글클메 정식 오픈", 1792646172145, 61200)
P2 = _v(15802356, "2026년 10월 21일 2부 글클메 정식 오픈", 1792650064096, 3174)


class Fetch:
    def __init__(self, table):
        self.table = table
        self.calls: list[str] = []

    def __call__(self, vno):
        self.calls.append(vno)
        return self.table.get(vno)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


# -- 파싱 -----------------------------------------------------------------------
def test_parse_live_open_kst_to_utc_ms():
    assert vod.parse_live_open("2026-10-21 20:59:41") == LIVE_1021
    # KST 20:59:41 = UTC 11:59:41
    assert datetime.fromtimestamp(LIVE_1021 / 1000, tz=timezone.utc).strftime("%H:%M:%S") == "11:59:41"
    assert vod.parse_live_open("") is None
    assert vod.parse_live_open(None) is None
    assert vod.parse_live_open("2026/10/21 20:59") is None


def test_fetch_live_open_parses_content(monkeypatch):
    class R:
        def __init__(self, sc, body):
            self.status_code, self._b = sc, body

        def json(self):
            return self._b

    bodies = {
        "1": R(200, {"code": 200, "content": {"liveOpenDate": "2026-10-21 20:59:41"}}),
        "2": R(200, {"code": 200, "content": {"liveOpenDate": None, "videoType": "UPLOAD"}}),
        "3": R(500, None),
        "4": R(200, {"code": 404, "content": None}),
    }
    monkeypatch.setattr(vod.requests, "get", lambda url, **kw: bodies[url.rsplit("/", 1)[1]])
    assert vod.fetch_live_open("1") == LIVE_1021
    assert vod.fetch_live_open("2") == 0          # 상세 OK, 라이브 아님
    assert vod.fetch_live_open("3") is None       # 실패
    assert vod.fetch_live_open("4") is None


def test_split_title_helpers():
    assert vod.split_part(P1["videoTitle"]) == 1
    assert vod.split_part(P2["videoTitle"]) == 2
    assert vod.split_part("글클메 51 스피어맨") is None
    assert vod.strip_split_prefix("2026년 10월 21일 2부 글클메 정식 오픈") == "글클메 정식 오픈"
    assert vod.strip_split_prefix("일반 방송") == "일반 방송"
    assert vod.strip_split_prefix("2026년 10월 21일 2부 ") == "2026년 10월 21일 2부"   # 본제 없으면 원문


# -- resolve_starts / 캐시 ----------------------------------------------------------
def test_resolve_starts_uses_live_open_and_caches(conn):
    meta = vod.VodMeta(conn)
    f = Fetch({"15802003": LIVE_1021, "15802356": LIVE_1021})
    vods = [dict(P1), dict(P2)]
    assert vod.resolve_starts(vods, meta, f, sleep=0) == 0
    assert f.calls == ["15802003", "15802356"]
    for v in vods:
        assert v["start_ms"] == v["live_open_ms"] == LIVE_1021
        assert v["_start_source"] == "live_open"
    # 옛 추정은 2부가 17h 뒤로 밀려 있었다
    assert vods[1]["_est_start_ms"] - LIVE_1021 > 17 * H

    # 캐시 히트 → 상세 호출 0 (새 인스턴스 = 다음 run)
    meta2 = vod.VodMeta(conn)
    f2 = Fetch({})
    vods2 = [dict(P1), dict(P2)]
    assert vod.resolve_starts(vods2, meta2, f2, sleep=0) == 0
    assert f2.calls == []
    assert meta2.hits == 2 and meta2.fetched == 0
    assert all(v["start_ms"] == LIVE_1021 for v in vods2)


def test_resolve_starts_no_live_uses_estimate_and_is_cached(conn):
    meta = vod.VodMeta(conn)
    up = _v(9, "업로드 영상", 1_000_000_000_000, 600)
    f = Fetch({"9": 0})
    assert vod.resolve_starts([up], meta, f, sleep=0) == 0
    assert up["_start_source"] == "no_live" and "live_open_ms" not in up
    assert up["start_ms"] == 1_000_000_000_000 - 600_000
    f2 = Fetch({})
    vod.resolve_starts([dict(up)], vod.VodMeta(conn), f2, sleep=0)
    assert f2.calls == []                          # 0 도 캐시


def test_resolve_starts_failure_falls_back_and_counts(conn):
    meta = vod.VodMeta(conn)
    vods = [dict(P1), dict(P2)]
    assert vod.resolve_starts(vods, meta, Fetch({"15802003": LIVE_1021}), sleep=0) == 1
    assert vods[0]["_start_source"] == "live_open"
    assert vods[1]["_start_source"] == "estimated"
    assert vods[1]["start_ms"] == vods[1]["_est_start_ms"]
    assert "live_open_ms" not in vods[1]
    # 실패는 캐시가 아니다 → 다음 run 재시도, 연속 실패 누적
    for run in (2, 3):
        m = vod.VodMeta(conn)
        f = Fetch({})
        assert vod.resolve_starts([dict(P2)], m, f, sleep=0) == 1
        assert f.calls == ["15802356"]
    assert vod.VodMeta(conn).failing(3) == [("15802356", 3)]
    assert vod.VodMeta(conn).failing(3, video_nos=["1"]) == []
    # 성공하면 실패 기록 해제
    vod.resolve_starts([dict(P2)], vod.VodMeta(conn), Fetch({"15802356": LIVE_1021}), sleep=0)
    assert vod.VodMeta(conn).failing(1) == []


def test_resolve_starts_fetch_none_injection(conn):
    vods = [dict(P1)]
    assert vod.resolve_starts(vods, vod.VodMeta(conn), None) == 1
    assert vods[0]["_start_source"] == "estimated"


def test_resolve_starts_fetch_raising_is_failure(conn):
    def boom(_):
        raise RuntimeError("x")
    vods = [dict(P1)]
    assert vod.resolve_starts(vods, vod.VodMeta(conn), boom, sleep=0) == 1
    assert vods[0]["_start_source"] == "estimated"


def test_vodmeta_dry_run_writes_nothing(conn):
    meta = vod.VodMeta(conn, dry_run=True)
    f = Fetch({"15802003": LIVE_1021})
    vod.resolve_starts([dict(P1)], meta, f, sleep=0)
    vod.resolve_starts([dict(P1)], meta, f, sleep=0)
    assert f.calls == ["15802003"]                 # 같은 프로세스 안에서는 메모리 캐시
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='vod_meta'").fetchone() is None
    assert not conn.in_transaction


# -- group_by_live ----------------------------------------------------------------
def test_group_by_live_part2_published_first_head_is_part1(conn):
    """2부가 1부보다 먼저 공개(목록 앞)여도 같은 live_open_ms → 1부가 머리."""
    p1 = dict(P1, publishDateAt=P2["publishDateAt"] + 2 * H)   # 1부 공개가 더 늦음
    p2 = dict(P2)
    vods = [p2, p1]
    vod.resolve_starts(vods, vod.VodMeta(conn), Fetch({"15802003": LIVE_1021, "15802356": LIVE_1021}), sleep=0)
    assert vod.group_by_live(vods) == 1
    assert p2["_series_part"] and p2["_series_head"] == "15802003"
    assert "_series_head" not in p1
    assert p1["start_ms"] == p2["start_ms"] == LIVE_1021
    assert p2["_orig_est_start_ms"] == p2["_est_start_ms"]


def test_group_by_live_only_part2_known_is_its_own_head(conn):
    p2 = dict(P2)
    vods = [p2]
    vod.resolve_starts(vods, vod.VodMeta(conn), Fetch({"15802356": LIVE_1021}), sleep=0)
    assert vod.group_by_live(vods) == 0
    assert "_series_head" not in p2 and p2["start_ms"] == LIVE_1021


def test_group_by_live_late_publish_9h_still_same_series(conn):
    """윤가놈 09-21형: 2부 공개가 1부 공개 +25h(17h 방송 + 9h 지연)라도 같은 방송."""
    live = _ms("2026-09-21 02:06:52")
    a = _v(15307272, "2026년 09월 21일 1부 위더런", live + 17 * H + 9 * H, 61200)
    b = _v(15319391, "2026년 09월 21일 2부 위더런", live + 34 * H + 9 * H, 61200)
    other = _v(15300000, "다음날 방송", live + 30 * H, 3 * 3600)   # 별개 방송(liveOpen 다름)
    vods = [b, other, a]
    vod.resolve_starts(vods, vod.VodMeta(conn),
                       Fetch({"15307272": live, "15319391": live, "15300000": live + 27 * H}), sleep=0)
    assert b["_est_start_ms"] - live >= 26 * H                  # 옛 추정이면 gap 60m·tolerance 6h 둘 다 깨짐
    assert vod.group_by_live(vods) == 1
    assert b["_series_head"] == "15307272"
    assert "_series_head" not in other
    assert [v["videoNo"] for v in vods] == ["15307272", "15319391", "15300000"]   # (live_open, publish) 정렬


def test_group_by_live_fallback_gap_only_among_estimated(conn):
    base = 1_800_000_000_000
    # live_open 없는 둘: 1부 off-air(publish) 뒤 30분에 2부 시작 → 폴백 gap 으로 묶임
    e1 = _v(1, "방송 1", base, 3600)                      # est start base-1h, end base
    e2 = _v(2, "방송 2", base + 90 * 60 * 1000, 3600)     # est start base+30m
    lv = _v(3, "라이브", base + 10 * 60 * 1000, 600)      # live_open 있음 — 폴백에 안 섞임
    vods = [e1, e2, lv]
    vod.resolve_starts(vods, vod.VodMeta(conn), Fetch({"3": base - 5 * 60 * 1000}), sleep=0)
    assert e1["_start_source"] == e2["_start_source"] == "estimated"
    assert vod.group_by_live(vods) == 1
    assert e2["_series_head"] == "1" and e2["start_ms"] == e1["start_ms"]
    assert e2["_orig_est_start_ms"] == base + 30 * 60 * 1000
    assert "_series_head" not in lv
    assert vod.group_by_live([dict(e1), dict(e2, start_ms=base + 30 * 60 * 1000)], max_gap_minutes=0) == 0


def test_group_by_live_without_resolve_uses_estimate():
    vods = [_v(1, "a", 2_000_000, 1)]
    assert vod.group_by_live(vods) == 0
    assert vods[0]["start_ms"] == 1_999_000
