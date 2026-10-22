"""dalwiki_client.client — _iso_z 정규화, 필드 가드, ListFailed, DRY_RUN 조회 실호출."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from dalwiki_client import ListFailed, TopicCache, client as dw

KST = timezone(timedelta(hours=9))


# ── _iso_z ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("value, expected", [
    ("2026-01-01T00:00:00Z", "2026-01-01T00:00:00.000Z"),           # 밀리초 없음 → .000Z
    ("2026-01-01T00:00:00.000Z", "2026-01-01T00:00:00.000Z"),       # 이미 정규형
    ("2026-01-01T00:00:00.123456Z", "2026-01-01T00:00:00.000Z"),    # 서브초 버림
    ("2026-01-01T09:00:00+09:00", "2026-01-01T00:00:00.000Z"),      # KST → UTC
    ("2026-01-01", "2026-01-01T00:00:00.000Z"),                     # 날짜만
    ("2026-01-01T00:00:00", "2026-01-01T00:00:00.000Z"),            # tz 없음 → UTC 간주
    (datetime(2026, 1, 1, 9, 0, tzinfo=KST), "2026-01-01T00:00:00.000Z"),
    (1767225600000, "2026-01-01T00:00:00.000Z"),                    # Unix ms
    (date(2026, 1, 1), "2026-01-01T00:00:00.000Z"),
    (None, None),
    ("", None),
])
def test_iso_z_normalizes(value, expected):
    assert dw._iso_z(value) == expected


def test_iso_z_rejects_naive_datetime_and_garbage():
    with pytest.raises(ValueError):
        dw._iso_z(datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        dw._iso_z("not-a-date")
    with pytest.raises(TypeError):
        dw._iso_z(object())


# ── 필드 가드 ─────────────────────────────────────────────────────────────
def test_create_event_guards_fields(fake_dal, soon):
    long_loc = "서울특별시 " * 20  # > 64자
    eid = dw.create_event(
        topic_id="T", summary="ㅎ", start=soon, end=soon + timedelta(hours=1),
        description=" ", location=long_loc, category_id="C1",
    )
    assert eid == "ev1"
    body = fake_dal.paths("/events/create")[0]
    assert body["summary"] == "ㅎ (일정)"
    assert "description" not in body
    assert len(body["location"]) == 64 and body["location"].endswith("…")
    assert body["categoryId"] == "C1"
    assert body["start"].endswith(".000Z")


def test_create_event_empty_summary_and_long_summary(fake_dal, soon):
    dw.create_event(topic_id="T", summary="   ", start=soon, end=soon)
    dw.create_event(topic_id="T", summary="가" * 150, start=soon, end=soon)
    b1, b2 = fake_dal.paths("/events/create")
    assert b1["summary"] == "(제목 없음)"
    assert len(b2["summary"]) == 100 and b2["summary"].endswith("...")


def test_update_event_guards_fields(fake_dal, soon):
    eid = fake_dal.add_event("T", "원래 제목", soon)
    ok, sc = dw.update_event(eid, summary="a", description="x", location="L" * 100)
    assert ok and sc == 200
    body = fake_dal.paths("/events/update")[0]
    assert body["summary"] == "a (일정)"
    assert "description" not in body            # 2자 미만 → 생략
    assert len(body["location"]) == 64


# ── list_events / ListFailed ─────────────────────────────────────────────
def test_list_events_raises_on_5xx(fake_dal):
    fake_dal.fail_list = 500
    with pytest.raises(ListFailed) as ei:
        dw.list_events("T", from_="2026-01-01T00:00:00Z", to="2026-02-01T00:00:00Z")
    assert ei.value.status == 500 and ei.value.topic_id == "T"
    assert "2026-01-01T00:00:00.000Z" in str(ei.value)


def test_list_events_raises_on_400_and_network_exhaustion(fake_dal):
    fake_dal.fail_list = 400
    with pytest.raises(ListFailed):
        dw.list_events("T")
    fake_dal.fail_list = "net"
    with pytest.raises(ListFailed) as ei:
        dw.list_events("T")
    assert ei.value.status is None


def test_list_events_legacy_behaviour_with_flag(fake_dal):
    fake_dal.fail_list = 500
    assert dw.list_events("T", raise_on_fail=False) == []


def test_list_events_ok_returns_events(fake_dal, soon):
    fake_dal.add_event("T", "A", soon)
    fake_dal.add_event("T", "B", soon + timedelta(days=1))
    fake_dal.add_event("OTHER", "C", soon)
    evs = dw.list_events("T", from_=soon - timedelta(days=1), to=soon + timedelta(days=10))
    assert sorted(e["summary"] for e in evs) == ["A", "B"]


# ── TopicCache strict ────────────────────────────────────────────────────
def test_topic_cache_strict_raises_and_lenient_records(fake_dal):
    fake_dal.fail_list = 500
    with pytest.raises(ListFailed):
        TopicCache("T").load(days_back=1, days_forward=1)
    c = TopicCache("T")
    c.load(days_back=1, days_forward=1, strict=False)
    assert c.complete is False and len(c.failed_chunks) >= 1


def test_topic_cache_lookup_normalizes_start(fake_dal, soon):
    eid = fake_dal.add_event("T", "제목", soon)
    c = TopicCache("T")
    c.load(days_back=1, days_forward=10)
    assert c.complete
    # 밀리초 없는 문자열로 조회해도 서버 포맷(.000Z)과 맞는다 — figurebot 404 복구 무력화 사고 재발 방지
    assert c.lookup("제목", soon.strftime("%Y-%m-%dT%H:%M:%SZ")) == eid
    assert c.has_id(eid) and c.in_window(soon)


# ── DRY_RUN: 쓰기는 가짜, 조회는 실제 호출 ──────────────────────────────
class _FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = ""

    def json(self):
        return self._body


class _FakeSession:
    def __init__(self):
        self.posts = []

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        if url.endswith("/events/list"):
            return _FakeResp(200, [{"id": "real-1", "summary": "실제", "start": "2026-01-01T00:00:00.000Z"}])
        return _FakeResp(200, {"id": "should-not-be-called"})


def test_dry_run_reads_hit_server_but_writes_do_not(monkeypatch):
    sess = _FakeSession()
    monkeypatch.setenv("DRY_RUN", "true")       # import 이후 세팅 — 호출 시점에 읽혀야 한다
    monkeypatch.setattr(dw, "DRY_RUN", False)
    monkeypatch.setattr(dw, "_get_session", lambda: sess)
    evs = dw.list_events("T")
    assert [e["id"] for e in evs] == ["real-1"]
    new_id = dw.create_event(topic_id="T", summary="드라이", start="2026-01-01T00:00:00Z", end="2026-01-01T01:00:00Z")
    assert new_id.startswith("dry-")
    assert dw.delete_event("real-1") is True
    assert [u for u, _ in sess.posts] == [f"{dw.API_BASE}/events/list"]   # 쓰기 2건은 서버에 안 갔다


# W8-0 .73 — DALWIKI_DISCORD_NOTIFY 액션 필터
def test_discord_notify_action_filter(monkeypatch):
    from dalwiki_client import discord_notify as dn
    queued = []
    monkeypatch.setattr(dn, "WEBHOOK_URL", "https://discord.test/hook")
    monkeypatch.setattr(dn, "_ensure_worker", lambda: None)
    monkeypatch.setattr(dn._queue, "put_nowait", lambda item: queued.append(item["action"]))
    monkeypatch.delenv("DALWIKI_DISCORD_NOTIFY", raising=False)
    for path in ("/events/create", "/events/update", "/events/delete"):
        dn.enqueue(path, {"id": "x"}, {})
    assert queued == ["CREATE", "UPDATE", "DELETE"]                # 미설정 = 구 동작(전부)
    queued.clear()
    monkeypatch.setenv("DALWIKI_DISCORD_NOTIFY", "create, Delete")
    for path in ("/events/create", "/events/update", "/events/delete"):
        dn.enqueue(path, {"id": "x"}, {})
    assert queued == ["CREATE", "DELETE"]
    queued.clear()
    monkeypatch.setenv("DALWIKI_DISCORD_NOTIFY", "none")
    dn.enqueue("/events/create", {"id": "x"}, {})
    assert queued == []
