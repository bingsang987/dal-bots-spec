"""botkit.replay — 유튜브 다시보기 채널 → 진실원본 일정에 영상 임베드 + 제목 맞추기 (pointfarm-fc3d).

VOD 봇(chzzk·aesther·cluez)이 VOD 단계 **다음, 미러 전에** 진실원본 이벤트 목록에 대해 부른다.
한 이벤트는 맡은 봇만 쓴다 — 이 모듈은 '무엇을 바꿀지'만 계산하고, 쓰기는 호출한 봇의 클라이언트가 한다.

    from botkit import replay

    replay.ensure_schema(conn)
    vids = replay.fetch_channel(conn, yt_channel_id, since_ms, log=log)        # 공식 YouTube Data API
    reps = replay.parse_videos(vids, live_only=False, strip=[...])
    actions = replay.plan(events, reps, now_ms=..., since_ms=..., skip=is_skip_event)
    for a in actions:
        if a.event is not None:
            desc = replay.merge_description(current_desc, a.video_ids)
            title = replay.next_title(conn, member, a.event["id"], current_title, a.title)  # None = 그대로
            ... 봇이 PATCH ...
            replay.record(conn, member, a.event["id"], a.video_ids, title or current_title, dry_run)
        else:
            ... 그 날짜 일정이 없음 → 봇이 새 일정 CREATE (a.start_ms, a.all_day) 후 record ...

짝짓기(plan):
    - 영상 날짜 = 제목의 날짜(형식 여러 가지), 없으면 생방송 기록의 시작 시각 날짜. 둘 다 없으면 버린다.
    - 생방송 기록(시작/종료 시각 있음)이 있는 날은 그날의 편집 업로드를 쓰지 않는다.
    - 일정 '유효 날짜'(06시 전 시작은 전날)가 같은 지난 일정에 붙인다. 휴방/외부 일정(skip)은 제외.
      그날 일정이 여럿이면: 시각 있는 영상은 시작이 가장 가까운 일정, 시각 없는 영상은 VOD 가 붙은 일정 → 가장 이른 일정.
    - 그날 일정이 없으면 새 일정 제안(시각 있는 영상이면 그 시작 시각, 없으면 종일).

제목(next_title): 유튜브 우선. 처음엔 다시보기 제목으로 바꾸고, 그 뒤엔 지금 제목이 봇이 마지막으로 쓴 제목과
같을 때만 바꾼다(사람이 고친 제목 보호).
본문(merge_description): 기존 본문 그대로 + 아직 없는 영상만 아래에 붙인다(카페 공지, VOD 링크 보존). 링크 줄 없음.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Optional

import requests

logger = logging.getLogger("botkit.replay")

KST = timezone(timedelta(hours=9))
API = "https://www.googleapis.com/youtube/v3/"
DAY_MS = 86_400_000
NEAR_MS = 6 * 3_600_000

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS yt_videos (
        yt_channel_id TEXT NOT NULL,
        video_id      TEXT NOT NULL,
        raw_title     TEXT NOT NULL,
        published_ms  INTEGER,
        length_s      INTEGER,
        start_ms      INTEGER,          -- 생방송 기록이면 실제 시작(UTC ms)
        end_ms        INTEGER,
        meta          INTEGER DEFAULT 0, -- 1 = videos.list 로 읽음
        PRIMARY KEY (yt_channel_id, video_id))""",
    """CREATE TABLE IF NOT EXISTS replay_applied (
        member     TEXT NOT NULL,
        event_id   TEXT NOT NULL,
        video_ids  TEXT NOT NULL,       -- 쉼표 구분
        bot_title  TEXT,                -- 봇이 마지막으로 쓴 제목(사람 수정 판별)
        updated_at TEXT,
        PRIMARY KEY (member, event_id))""",
)


ROSTER_PATH = os.path.join(os.path.dirname(__file__), "streamer_roster.json")


def load_roster(owner: Optional[str] = None) -> dict[str, dict]:
    """명단 {이름: 항목}. owner 를 주면 그 봇이 맡은 스트리머만."""
    import json
    with open(ROSTER_PATH, encoding="utf-8") as f:
        rows = json.load(f)["streamers"]
    return {r["name"]: r for r in rows if owner is None or r.get("owner") == owner}


def ensure_schema(conn: sqlite3.Connection) -> None:
    for s in SCHEMA:
        conn.execute(s)
    conn.commit()


# ---------------------------------------------------------------------------
# YouTube Data API
# ---------------------------------------------------------------------------
class ApiError(Exception):
    pass


def _api_key() -> Optional[str]:
    return os.environ.get("YOUTUBE_API_KEY")


def _get(path: str, params: dict, key: str) -> dict:
    """키는 헤더로만 보낸다(URL·예외 메시지·로그에 안 남게)."""
    try:
        r = requests.get(API + path, params=params, headers={"X-Goog-Api-Key": key}, timeout=20)
    except Exception as e:  # noqa: BLE001
        raise ApiError(f"{path} 요청 실패: {type(e).__name__}") from None
    if r.status_code != 200:
        try:
            msg = r.json()["error"]["message"]
        except Exception:  # noqa: BLE001
            msg = ""
        raise ApiError(f"{path} {r.status_code}: {msg[:120]}")
    return r.json()


def _iso_ms(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def _dur_s(s: Optional[str]) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + se


def fetch_channel(conn: sqlite3.Connection, yt_channel_id: str, since_ms: int, *,
                  log: logging.Logger = logger, max_pages: int = 6) -> Optional[list[dict]]:
    """채널 업로드 목록(최신순)을 since_ms 보다 오래된 게시물이 나올 때까지 읽고, 새 영상만 videos.list 로
    길이와 생방송 시각을 읽어 yt_videos 에 쌓는다. 반환: 표의 영상 전부(게시 since_ms 이후). 실패면 None.
    비용: 목록 50개당 1 + 새 영상 50개당 1 유닛."""
    key = _api_key()
    if not key:
        log.warning("YOUTUBE_API_KEY 없음 — 다시보기 건너뜀")
        return None
    uploads = "UU" + yt_channel_id[2:]
    listed: list[tuple[str, str, int]] = []
    page = None
    try:
        for _ in range(max_pages):
            p = {"part": "snippet", "playlistId": uploads, "maxResults": 50,
                 "fields": "nextPageToken,items(snippet(publishedAt,title,resourceId(videoId)))"}
            if page:
                p["pageToken"] = page
            j = _get("playlistItems", p, key)
            old = False
            for it in j.get("items", []):
                sn = it["snippet"]
                pub = _iso_ms(sn.get("publishedAt")) or 0
                listed.append((sn["resourceId"]["videoId"], sn.get("title") or "", pub))
                old = old or pub < since_ms
            page = j.get("nextPageToken")
            if old or not page:
                break
        have = {r[0]: r[1] for r in conn.execute(
            "SELECT video_id, meta FROM yt_videos WHERE yt_channel_id=?", (yt_channel_id,))}
        for vid, title, pub in listed:
            conn.execute(
                "INSERT INTO yt_videos (yt_channel_id, video_id, raw_title, published_ms) VALUES (?,?,?,?) "
                "ON CONFLICT(yt_channel_id, video_id) DO UPDATE SET raw_title=excluded.raw_title",
                (yt_channel_id, vid, title, pub))
        need = [vid for vid, _, pub in listed if not have.get(vid) and pub >= since_ms]
        for i in range(0, len(need), 50):
            j = _get("videos", {"part": "contentDetails,liveStreamingDetails", "id": ",".join(need[i:i + 50]),
                                "fields": "items(id,contentDetails(duration),"
                                          "liveStreamingDetails(actualStartTime,actualEndTime))"}, key)
            for it in j.get("items", []):
                ls = it.get("liveStreamingDetails") or {}
                st, en = _iso_ms(ls.get("actualStartTime")), _iso_ms(ls.get("actualEndTime"))
                if st and not en:
                    continue  # 아직 생방송 중 — 끝난 뒤 다시 읽는다
                conn.execute("UPDATE yt_videos SET length_s=?, start_ms=?, end_ms=?, meta=1 "
                             "WHERE yt_channel_id=? AND video_id=?",
                             (_dur_s((it.get("contentDetails") or {}).get("duration")), st, en,
                              yt_channel_id, it["id"]))
        conn.commit()
    except ApiError as e:
        conn.commit()
        log.warning("YouTube API 실패(%s): %s", yt_channel_id, e)
        return None
    cols = ("video_id", "raw_title", "published_ms", "length_s", "start_ms", "end_ms")
    return [dict(zip(cols, r)) for r in conn.execute(
        "SELECT video_id, raw_title, published_ms, length_s, start_ms, end_ms FROM yt_videos "
        "WHERE yt_channel_id=? AND meta=1 AND published_ms>=?", (yt_channel_id, since_ms))]


# ---------------------------------------------------------------------------
# 제목: 날짜 읽기, 꾸밈 지우기
# ---------------------------------------------------------------------------
_DATE_RES = (
    re.compile(r"(?P<y>20\d{2})\s*년\s*(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일"),
    re.compile(r"(?<!\d)(?P<y>20\d{2})\s*[./\-]\s*(?P<m>\d{1,2})\s*[./\-]\s*(?P<d>\d{1,2})(?!\d)"),
    re.compile(r"(?<!\d)(?P<y>20\d{2})\s+(?P<m>\d{1,2})\s+(?P<d>\d{1,2})(?!\d)"),
    re.compile(r"(?<![\d/])(?P<y>\d{2})\s*[./]\s*(?P<m>\d{1,2})\s*[./]\s*(?P<d>\d{1,2})(?![\d/])"),
    re.compile(r"^\s*(?P<y>\d{2})(?P<m>\d{2})(?P<d>\d{2})(?:\s*\)|(?=\s))"),   # 261114) / 261022 제목
)
_PART_RES = (
    re.compile(r"\(\s*\d+\s*/\s*\d+\s*\)"),          # (1/2)
    re.compile(r"(?<!\S)\d+\s*편(?!\S)"),              # 1편
    re.compile(r"\s*[-–]\s*\d+\s*부\s*$"),             # - 5부
)
_TAIL_RES = (
    re.compile(r"\s*[-–]\s*[^-–]*?\bFULL\s*다시\s*보기.*$", re.I),   # - 김진우 FULL 다시보기 #2
    re.compile(r"\s*[-–]\s*[^-–\s]*\s*다시\s*보기\s*$"),              # -이춘향 다시보기
    re.compile(r"\s*(?:FULL\s*)?다시\s*보기\s*[!！.~]*\s*$", re.I),   # 다시보기 !
)
_BRACKETS = "[]【】［］「」『』〔〕"


def parse_date(raw: str) -> Optional[tuple[str, str]]:
    """제목에서 가장 앞에 나오는 날짜 → (YYYY-MM-DD, 날짜를 뺀 나머지). 없으면 None."""
    best = None
    for rx in _DATE_RES:
        m = rx.search(raw or "")
        if m and (best is None or m.start() < best.start()):
            best = m
    if not best:
        return None
    y, mo, d = best.group("y"), int(best.group("m")), int(best.group("d"))
    y = int(y) + (2000 if len(y) == 2 else 0)
    try:
        date = datetime(y, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return None
    if not 2020 <= y <= 2099:
        return None
    return date, raw[:best.start()] + " " + raw[best.end():]


def _drop_symbols(t: str) -> str:
    """이모지(기호 So, 이음/변형 선택 문자) 전부 제거."""
    out = []
    for ch in t:
        cat = unicodedata.category(ch)
        if cat == "So" or ch in "‍︎️" or 0x1F3FB <= ord(ch) <= 0x1F3FF:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def clean_title(rest: str, strip: Iterable[str] = ()) -> str:
    """날짜 뺀 제목 → 일정 제목. 이모지, 공통 머리말/꼬리말(다시보기, 편 번호, 채널별 strip 정규식) 제거,
    괄호 글자만 지우고 안의 내용은 살린다."""
    t = _drop_symbols(rest or "")
    for rx in strip:
        t = re.sub(rx, " ", t)
    for rx in _TAIL_RES:
        t = rx.sub("", t)
    for rx in _PART_RES:
        t = rx.sub(" ", t)
    t = re.sub("[" + re.escape(_BRACKETS) + "]", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -–|/:·,")
    if len(t) >= 2 and t[0] == "(" and t[-1] == ")" and t.count("(") == 1:
        t = t[1:-1].strip()
    t = re.sub(r"\(\s*\)", "", t)
    return re.sub(r"\s+", " ", t).strip(" -–|/:,")


@dataclass
class Replay:
    video_id: str
    date: str               # 방송 날짜 YYYY-MM-DD (KST)
    title: str
    start_ms: Optional[int]  # 생방송 기록이면 실제 시작
    end_ms: Optional[int]
    published_ms: int
    length_s: int = 0


def effective_day(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000, KST)
    if dt.hour < 6:
        dt -= timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def parse_videos(videos: Iterable[dict], *, live_only: bool = False, strip: Iterable[str] = ()) -> list[Replay]:
    """yt_videos 행 → Replay. live_only 면 생방송 기록만(라이브 탭 채널). 날짜를 못 정하면 버린다."""
    strip = tuple(strip)
    out = []
    for v in videos:
        st = v.get("start_ms")
        if live_only and not st:
            continue
        p = parse_date(v.get("raw_title") or "")
        if p:
            date, rest = p
        elif st:
            date, rest = effective_day(st), v.get("raw_title") or ""
        else:
            continue
        out.append(Replay(v["video_id"], date, clean_title(rest, strip) or "방송", st, v.get("end_ms"),
                          v.get("published_ms") or 0, v.get("length_s") or 0))
    return out


def replay_title(reps: list["Replay"]) -> str:
    """하루 여러 편이면 쉼표로(같은 제목은 한 번)."""
    return ", ".join(dict.fromkeys(r.title for r in reps if r.title)) or "방송"


# ---------------------------------------------------------------------------
# 짝짓기
# ---------------------------------------------------------------------------
@dataclass
class Action:
    event: Optional[dict]        # None = 그 날짜 일정 없음 → 새로 만들기
    date: str
    replays: list[Replay] = field(default_factory=list)
    start_ms: Optional[int] = None   # 새로 만들 때 시작(시각 있는 영상), None 이면 종일
    all_day: bool = False

    @property
    def video_ids(self) -> list[str]:
        return [r.video_id for r in self.replays]

    @property
    def title(self) -> str:
        return replay_title(self.replays)


def _ev_ms(ev: dict) -> int:
    s = ev.get("start") or ""
    if isinstance(s, (int, float)):
        return int(s)
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def _day_shift(date: str, n: int) -> str:
    return (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def plan(events: Iterable[dict], replays: Iterable[Replay], *, now_ms: int, since_ms: int,
         skip: Callable[[dict], bool] = lambda ev: False,
         applied_ids: Optional[set[str]] = None, own_event_ids: Optional[set[str]] = None,
         durations: Optional[dict[str, int]] = None, length_tol: float = 0.12) -> list[Action]:
    """다시보기를 진실원본 일정에 짝짓는다(위 모듈 설명). 미래 일정, skip 일정은 대상 아님.
    종일 일정은 봇이 다시보기로 만든 것(own_event_ids)만 대상. applied_ids(이미 처리한 영상)만으로 이뤄진
    '새 일정' 제안은 내지 않는다 — 사람이 지운 일정을 다시 만들지 않게.
    durations {event_id: 그 일정에 붙은 VOD 길이 합(초)} 를 주면, 시각 없는 영상은 먼저 제목 날짜 앞뒤 하루 안에서
    VOD 길이가 영상 길이와 length_tol 안으로 가장 비슷한 일정에 붙인다(자정 넘긴 방송의 날짜 어긋남 대응)."""
    applied_ids = applied_ids or set()
    own_event_ids = own_event_ids or set()
    durations = durations or {}
    cands: dict[str, list[dict]] = {}
    for ev in events:
        if skip(ev) or (ev.get("allDay") and ev.get("id") not in own_event_ids):
            continue
        ms = _ev_ms(ev)
        if ms >= now_ms:
            continue
        cands.setdefault(effective_day(ms), []).append(ev)
    since_day = datetime.fromtimestamp(since_ms / 1000, KST).strftime("%Y-%m-%d")
    by_date: dict[str, list[Replay]] = {}
    for r in replays:
        if r.date >= since_day:
            by_date.setdefault(r.date, []).append(r)
    groups: dict[str, Action] = {}   # event id → Action
    out: list[Action] = []

    def put(ev: dict, date: str, r: Replay) -> None:
        groups.setdefault(ev["id"], Action(ev, date)).replays.append(r)

    for date, reps in sorted(by_date.items()):
        timed = [r for r in reps if r.start_ms]
        reps = sorted(timed or reps, key=lambda r: (r.start_ms or 0, r.published_ms))
        evs = sorted(cands.get(date, []), key=_ev_ms)
        rest: list[Replay] = []
        for r in reps:
            if r.start_ms or not r.length_s or not durations:
                rest.append(r)
                continue
            near = [e for d in (_day_shift(date, -1), date, _day_shift(date, 1)) for e in cands.get(d, [])
                    if durations.get(e.get("id"))]
            best = min(near, key=lambda e: abs(durations[e["id"]] - r.length_s), default=None)
            if best is not None and abs(durations[best["id"]] - r.length_s) <= length_tol * r.length_s:
                put(best, effective_day(_ev_ms(best)), r)
            else:
                rest.append(r)
        if not rest:
            continue
        if not evs:
            if all(r.video_id in applied_ids for r in rest):
                continue
            st = min((r.start_ms for r in rest if r.start_ms), default=None)
            out.append(Action(None, date, rest, st, st is None))
            continue
        if any(r.start_ms for r in rest) and len(evs) > 1:
            for r in rest:
                put(min(evs, key=lambda e: abs(_ev_ms(e) - (r.start_ms or _ev_ms(evs[0])))), date, r)
            continue
        if len(evs) > 1:
            with_vod = [e for e in evs if "chzzk.naver.com/video/" in (e.get("description") or "")]
            ev = (with_vod or evs)[0]
        else:
            ev = evs[0]
        for r in rest:
            put(ev, date, r)
    for a in groups.values():
        a.replays.sort(key=lambda r: (r.start_ms or 0, r.published_ms))
    return list(groups.values()) + out


# ---------------------------------------------------------------------------
# 본문, 제목, 기록
# ---------------------------------------------------------------------------
def youtube_block(video_id: str) -> str:
    """dal.wiki 편집기 유튜브 노드만. 링크 줄은 붙이지 않는다(사용자 지시)."""
    return ('<div data-youtube-video=""><iframe src="https://www.youtube-nocookie.com/embed/'
            f'{video_id}" width="640" height="360" allowfullscreen="true"></iframe></div>')


def merge_description(desc: Optional[str], video_ids: Iterable[str]) -> str:
    """기존 본문 그대로 + 아직 없는 영상만 아래에(멱등)."""
    out = desc or ""
    for vid in video_ids:
        if f"/embed/{vid}" not in out:
            out += youtube_block(vid)
    return out


_PLACEHOLDER_RE = re.compile(r"^\s*(?:방송\s*예정|예정|갠방|VOD\s*업로드\s*예정|갠방\s*\(VOD\s*업로드\s*예정\))\s*(?:[-–:]\s*.*)?$")


def is_placeholder_title(title: str) -> bool:
    """'방송 예정', '방송 예정 - 엘든링', '갠방', 'VOD 업로드 예정' 같은 자리표시 제목(맨 앞 '[이름] ' 은 무시)."""
    t = re.sub(r"^\s*\[[^\]]*\]\s*", "", title or "")
    return bool(_PLACEHOLDER_RE.match(t))


def last_bot_title(conn: sqlite3.Connection, member: str, event_id: str) -> Optional[str]:
    row = conn.execute("SELECT bot_title FROM replay_applied WHERE member=? AND event_id=?",
                       (member, event_id)).fetchone()
    return row[0] if row else None


def is_done(conn: sqlite3.Connection, member: str, event_id: str, video_ids: Iterable[str], current: str) -> bool:
    """이미 이 영상들을 붙였고 제목도 봇이 쓴 그대로면 True — 본문을 다시 읽지 않고 건너뛴다(조회 수 절약)."""
    row = conn.execute("SELECT video_ids, bot_title FROM replay_applied WHERE member=? AND event_id=?",
                       (member, event_id)).fetchone()
    if not row:
        return False
    done = {x for x in (row[0] or "").split(",") if x}
    return set(video_ids) <= done and row[1] == current


def next_title(conn: sqlite3.Connection, member: str, event_id: str, current: str, wanted: str) -> Optional[str]:
    """바꿀 제목(바꾸지 않으면 None). 처음엔 유튜브 우선, 그 뒤엔 봇이 마지막으로 쓴 제목일 때만."""
    if not wanted or current == wanted:
        return None
    row = conn.execute("SELECT bot_title FROM replay_applied WHERE member=? AND event_id=?",
                       (member, event_id)).fetchone()
    if row is None or row[0] is None or row[0] == current:
        return wanted
    return None  # 봇이 쓴 뒤 사람이 고침


def record(conn: sqlite3.Connection, member: str, event_id: str, video_ids: Iterable[str],
           title: str, dry_run: bool = False) -> None:
    if dry_run:
        return
    conn.execute(
        "INSERT INTO replay_applied (member, event_id, video_ids, bot_title, updated_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(member, event_id) DO UPDATE SET video_ids=excluded.video_ids, bot_title=excluded.bot_title, "
        "updated_at=excluded.updated_at",
        (member, event_id, ",".join(video_ids), title, datetime.now(KST).isoformat(timespec="seconds")))
    conn.commit()


def applied(conn: sqlite3.Connection, member: str) -> tuple[set[str], set[str]]:
    """(이 멤버에 대해 봇이 다시보기로 처리한 영상 id 전부, 그 이벤트 id 전부) — plan 의 applied_ids/own_event_ids."""
    vids: set[str] = set()
    evs: set[str] = set()
    for eid, ids in conn.execute("SELECT event_id, video_ids FROM replay_applied WHERE member=?", (member,)):
        evs.add(eid)
        vids.update(x for x in (ids or "").split(",") if x)
    return vids, evs
