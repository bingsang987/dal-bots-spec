"""botkit.replay — 다시보기 제목 날짜/정리, 짝짓기, 제목 보호 (pointfarm-fc3d)."""
import sqlite3
from datetime import datetime

import pytest

from botkit import replay
from botkit.replay import KST, Replay


@pytest.mark.parametrize("raw, date, title", [
    ("261114) 찡켓몬2 [다톡방,마인크래프트]", "2026-11-14", "찡켓몬2 다톡방,마인크래프트"),
    ("261022 독케익 데모게임천국", "2026-10-22", "독케익 데모게임천국"),
    ("2026-11-13 하트골드 비행 타입 - 5부", "2026-11-13", "하트골드 비행 타입"),
    ("26.11.15 (2/2) 사이코 슬루스 -완-", "2026-11-15", "사이코 슬루스 -완"),
    ("2026.11.15  떵겜 퐉퐉 먹기 ✨💚", "2026-11-15", "떵겜 퐉퐉 먹기"),
    ("【2026.11.15】수수께끼에 미친 사람들의 마을", "2026-11-15", "수수께끼에 미친 사람들의 마을"),
    ("[2026/11/15] 맨손 항아리게임 + 후열 롤 / 끄투 다시보기 !", "2026-11-15", "맨손 항아리게임 + 후열 롤 / 끄투"),
    ("[ 2026 / 11 / 16 ] 밥똥삼데이", "2026-11-16", "밥똥삼데이"),
    ("[ 26/11/06┆짜쳐짜쳐서버 ] 짜짜섭 마지막 날", "2026-11-06", "짜쳐짜쳐서버 짜짜섭 마지막 날"),
    ("2026 11 14 자낳대26 : 로블록스 DAY 3", "2026-11-14", "자낳대26 : 로블록스 DAY 3"),
    ("26/11/15 예비군 💙", "2026-11-15", "예비군"),
    ("퀸즈 도메인 2026-11-14", "2026-11-14", "퀸즈 도메인"),
    ("[2026/11/08 미믹 파티 - 김똘복]-이춘향 다시보기", "2026-11-08", "미믹 파티 - 김똘복"),
    ("[2026/11/12] Punch The Walls - 김진우 FULL 다시보기 #2", "2026-11-12", "Punch The Walls"),
])
def test_date_and_clean(raw, date, title):
    d, rest = replay.parse_date(raw)
    assert d == date
    assert replay.clean_title(rest) == title


def test_no_date():
    assert replay.parse_date("드디어 감을 찾은 명훈 [리그오브레전드]") is None


def test_strip_regex_and_paren_unwrap():
    d, rest = replay.parse_date("2026년 11월 15일 방송 (예비군 코리안 다크소울)")
    assert replay.clean_title(rest, [r"^\s*방송\s*"]) == "예비군 코리안 다크소울"


def test_placeholder():
    assert replay.is_placeholder_title("방송 예정 - 엘든링")
    assert replay.is_placeholder_title("[키유] 방송 예정")
    assert not replay.is_placeholder_title("엘든링 엔딩 야르")
    assert replay.is_placeholder_title("갠방")
    assert replay.is_placeholder_title("갠방(VOD 업로드 예정)")
    assert not replay.is_placeholder_title("갠방 - 엘든링")


def _ms(s):
    return int(datetime.fromisoformat(s).replace(tzinfo=KST).timestamp() * 1000)


def _ev(i, s, desc=""):
    return {"id": i, "start": datetime.fromisoformat(s).replace(tzinfo=KST).isoformat(), "allDay": False,
            "summary": "방송 예정", "description": desc}


def test_plan_length_beats_title_date():
    """자정 넘긴 방송: 제목 날짜(11/6)가 아니라 길이가 맞는 11/5 23시 일정에."""
    evs = [_ev("a", "2026-11-05T23:00"), _ev("b", "2026-11-06T18:00")]
    r = Replay("v1", "2026-11-06", "짜짜섭 2일차", None, None, 0, length_s=3 * 3600)
    acts = replay.plan(evs, [r], now_ms=_ms("2026-11-10T00:00"), since_ms=_ms("2026-11-01T00:00"),
                       durations={"a": 3 * 3600 + 120, "b": 6 * 3600})
    assert [(a.event["id"], a.video_ids) for a in acts] == [("a", ["v1"])]


def test_plan_length_prefers_title_date_within_tolerance():
    """길이가 둘 다 비슷하면(허용 범위 안) 제목 날짜 일정에 — 이웃 날 일정이 더 가까워도."""
    evs = [_ev("a", "2026-11-13T12:00"), _ev("b", "2026-11-14T16:00")]
    r = Replay("v1", "2026-11-13", "명조", None, None, 0, length_s=521 * 60)
    acts = replay.plan(evs, [r], now_ms=_ms("2026-11-16T00:00"), since_ms=_ms("2026-11-01T00:00"),
                       durations={"a": 500 * 60, "b": 522 * 60})
    assert [(a.event["id"], a.video_ids) for a in acts] == [("a", ["v1"])]


def test_plan_all_day_uses_calendar_date():
    """봇이 만든 종일 일정(00:00 시작)은 전날이 아니라 그 날짜로 본다."""
    ev = {"id": "d", "start": "2026-11-01T00:00:00+09:00", "allDay": True, "summary": "x", "description": ""}
    r = Replay("v2", "2026-11-01", "토크", None, None, 0)
    acts = replay.plan([ev], [r], now_ms=_ms("2026-11-10T00:00"), since_ms=_ms("2026-10-20T00:00"),
                       own_event_ids={"d"})
    assert [(a.event["id"], a.video_ids) for a in acts] == [("d", ["v2"])]


def test_plan_create_when_no_event_and_not_twice():
    r = Replay("v1", "2026-11-03", "토크", None, None, 0)
    kw = dict(now_ms=_ms("2026-11-10T00:00"), since_ms=_ms("2026-11-01T00:00"))
    acts = replay.plan([], [r], **kw)
    assert acts[0].event is None and acts[0].all_day
    assert replay.plan([], [r], applied_ids={"v1"}, **kw) == []


def _rec(o, c, t="엘든링"):
    return replay.LiveRec(_ms(o), _ms(c), t)


def test_plan_live_attaches_nearest_same_day():
    evs = [_ev("a", "2026-11-14T12:00"), _ev("b", "2026-11-14T20:00")]
    acts = replay.plan_live(evs, [_rec("2026-11-14T19:30", "2026-11-14T23:00"),
                                  _rec("2026-11-15T01:00", "2026-11-15T03:00", "토크")],
                            now_ms=_ms("2026-11-16T00:00"), since_ms=_ms("2026-11-01T00:00"))
    assert [(a.event["id"], a.title()) for a in acts] == [("b", "엘든링, 토크")]


def test_plan_live_create_once_and_not_on_busy_or_open():
    huibang = dict(_ev("h", "2026-11-03T18:00"), summary="휴방")
    later = _ev("f", "2026-11-15T20:00")          # 오늘 저녁 예정(아직 안 옴)
    other = _ev("o", "2026-11-10T20:00")          # 다른 날 지난 일정(일정을 챙기는 스트리머)
    recs = [_rec("2026-11-03T13:00", "2026-11-03T14:10"), _rec("2026-11-03T16:00", "2026-11-03T17:00", "토크"),
            _rec("2026-11-15T13:00", "2026-11-15T14:00"),               # 예정 있는 날 → 만들지 않음
            replay.LiveRec(_ms("2026-11-15T15:00"), 0, "")]             # 아직 방송 중(종료 없음)
    kw = dict(now_ms=_ms("2026-11-15T16:00"), since_ms=_ms("2026-11-01T00:00"),
              skip=lambda ev: ev["summary"] == "휴방")
    acts = replay.plan_live([huibang, later, other], recs, **kw)
    assert len(acts) == 1 and acts[0].event is None and acts[0].date == "2026-11-03"
    assert acts[0].start_ms == _ms("2026-11-03T13:00") and acts[0].title() == "엘든링, 토크"
    applied = set(acts[0].open_ms_list)
    assert replay.plan_live([huibang, later, other], recs, applied_opens=applied, **kw) == []


def test_plan_live_no_create_without_any_past_event():
    recs = [_rec("2026-11-03T13:00", "2026-11-03T14:10")]
    kw = dict(now_ms=_ms("2026-11-15T16:00"), since_ms=_ms("2026-11-01T00:00"))
    assert replay.plan_live([], recs, **kw) == []
    assert len(replay.plan_live([], recs, require_events=False, **kw)) == 1


def test_load_live_reads_broadcasts(tmp_path):
    p = tmp_path / "archive.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE broadcasts (channel_id TEXT, open_ms INTEGER, close_ms INTEGER, vod_title TEXT, "
              "vods TEXT, updated_at TEXT)")
    c.executemany("INSERT INTO broadcasts VALUES (?,?,?,?,'[]','x')",
                  [("c1", 100, 200, "a"), ("c1", 300, 400, None), ("c2", 300, 400, "b")])
    c.commit()
    c.close()
    assert replay.load_live("c1", 150, path=str(p)) == [replay.LiveRec(300, 400, "")]
    assert replay.load_live("c1", 0, path=str(tmp_path / "none.db")) is None


def test_next_title_protects_human_edit():
    conn = sqlite3.connect(":memory:")
    replay.ensure_schema(conn)
    assert replay.next_title(conn, "m", "e", "방송 예정", "엘든링") == "엘든링"
    replay.record(conn, "m", "e", ["v1"], "엘든링")
    assert replay.next_title(conn, "m", "e", "엘든링", "엘든링 엔딩") == "엘든링 엔딩"
    assert replay.next_title(conn, "m", "e", "사람이 고침", "엘든링 엔딩") is None
    assert replay.is_done(conn, "m", "e", ["v1"], "엘든링")


def test_is_done_follows_youtube_retitle():
    """붙인 영상은 그대로인데 유튜브 제목이 바뀌면 다시 처리(사람이 고친 제목은 next_title 이 지킨다)."""
    conn = sqlite3.connect(":memory:")
    replay.ensure_schema(conn)
    replay.record(conn, "m", "e", ["v1", "v2"], "퀸즈 도메인")
    assert replay.is_done(conn, "m", "e", ["v1", "v2"], "퀸즈 도메인", "퀸즈 도메인")
    assert not replay.is_done(conn, "m", "e", ["v1", "v2"], "퀸즈 도메인", "여왕의 영지 1화")
    assert replay.next_title(conn, "m", "e", "퀸즈 도메인", "여왕의 영지 1화") == "여왕의 영지 1화"
    assert replay.is_done(conn, "m", "e", ["v1"], "퀸즈 도메인", "v1 제목만")        # 일부만 잡힌 run — 그대로
    assert replay.is_done(conn, "m", "e", [], "퀸즈 도메인", "VOD 제목")             # VOD 제목 단계 — 그대로
    assert replay.next_title(conn, "m", "e", "사람이 고침", "여왕의 영지 1화") is None


def test_merge_description_idempotent():
    d = replay.merge_description("<p>카페</p>", ["abc"])
    assert d.startswith("<p>카페</p>") and "/embed/abc" in d
    assert replay.merge_description(d, ["abc"]) == d


def test_merge_titles():
    """하루 여러 편: 같은 내용은 첫 편만, 다른 내용은 쉼표로, 길면 뒤쪽을 통째로 뺀다."""
    assert replay.merge_titles(["사이코 슬루스 3화", "사이코 슬루스 3화"]) == "사이코 슬루스 3화"
    assert replay.merge_titles(["사이코 슬루스 6화", "사이코 슬루스 -완"]) == "사이코 슬루스 6화"
    assert replay.merge_titles(["짜짜썹 2일차", "3만분의1 : 버츄얼 생존 시뮬레이터"]) == "짜짜썹 2일차, 3만분의1 : 버츄얼 생존 시뮬레이터"
    assert replay.merge_titles(["롤 렁 근판", "롤 내전"]) == "롤 렁 근판, 롤 내전"
    long = ["가" * 50, "나" * 30, "다" * 30]
    assert replay.merge_titles(long) == "가" * 50 + ", " + "나" * 30
    assert replay.merge_titles(["가" * 120]) == "가" * 120


def test_clean_title_part_markers_and_tag_strip():
    assert replay.clean_title("(1/2) 사이코 슬루스 3화 / 전편") == "사이코 슬루스 3화"
    assert replay.clean_title("찡켓몬2 3일차 [다톡방,마인크래프트]", [r"\s*\[[^\]]*\]\s*$"]) == "찡켓몬2 3일차"
