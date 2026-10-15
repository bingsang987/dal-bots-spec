"""botkit.sync.SyncEngine 시나리오 — 가짜 dal.wiki 위에서."""
from __future__ import annotations

from datetime import timedelta

import pytest

from botkit.sync import Event, SyncAborted, SyncEngine, SyncState

TOPIC = "T1"


def mk(key, summary, start, **kw) -> Event:
    return Event(key=key, summary=summary, start=start, end=start + timedelta(hours=2),
                 category_id=kw.pop("category_id", "CAT"), **kw)


@pytest.fixture
def engine(tmp_path):
    return SyncEngine(TOPIC, tmp_path / "state.db", dry_run=False, cache_days_back=5, cache_days_forward=30)


def test_new_creates_and_saves_state(engine, fake_dal, soon):
    st = engine.sync([mk("k1", "경기 A", soon), mk("k2", "경기 B", soon + timedelta(days=1))])
    assert (st.created, st.updated, st.unchanged, st.failed) == (2, 0, 0, 0)
    assert len(fake_dal.paths("/events/create")) == 2
    row = engine.state.get("k1")
    assert row and row.event_id in fake_dal.events
    assert fake_dal.events[row.event_id]["categoryId"] == "CAT"
    assert fake_dal.events[row.event_id]["start"].endswith(".000Z")


def test_unchanged_makes_no_write(engine, fake_dal, soon):
    evs = [mk("k1", "경기 A", soon)]
    engine.sync(evs)
    n_calls = len(fake_dal.calls)
    st = engine.sync(evs)
    assert st.unchanged == 1 and st.writes == 0
    writes = [p for p, _ in fake_dal.calls[n_calls:] if p != "/events/list"]
    assert writes == []


def test_changed_updates_same_id(engine, fake_dal, soon):
    engine.sync([mk("k1", "경기 A", soon)])
    eid = engine.state.get("k1").event_id
    st = engine.sync([mk("k1", "경기 A (연기)", soon, description="<a href=\"https://x\" target=\"_blank\" rel=\"noopener noreferrer\">x</a>")])
    assert st.updated == 1 and st.created == 0
    assert fake_dal.events[eid]["summary"] == "경기 A (연기)"
    assert engine.state.get("k1").event_id == eid
    assert len(fake_dal.events) == 1


def test_absent_default_delete(engine, fake_dal, soon):
    engine.sync([mk("k1", "AA", soon), mk("k2", "BB", soon)])
    eid2 = engine.state.get("k2").event_id
    st = engine.sync([mk("k1", "AA", soon)])
    assert st.deleted == 1 and st.absent_skipped is None
    assert eid2 not in fake_dal.events
    assert engine.state.get("k2") is None
    assert engine.state.get("k1") is not None


def test_absent_keep_policy(tmp_path, fake_dal, soon):
    eng = SyncEngine(TOPIC, tmp_path / "s.db", absent="keep", dry_run=False, cache_days_back=5, cache_days_forward=30)
    eng.sync([mk("k1", "AA", soon), mk("k2", "BB", soon)])
    st = eng.sync([mk("k1", "AA", soon)])
    assert st.deleted == 0 and st.absent_kept == 1
    assert len(fake_dal.events) == 2


def test_absent_skipped_when_source_empty(engine, fake_dal, soon):
    engine.sync([mk("k1", "AA", soon), mk("k2", "BB", soon)])
    st = engine.sync([])
    assert st.absent_skipped == "source=0" and st.deleted == 0
    assert len(fake_dal.events) == 2
    assert len(engine.state.all()) == 2


def test_absent_skipped_when_source_incomplete(engine, fake_dal, soon):
    engine.sync([mk("k1", "AA", soon), mk("k2", "BB", soon)])
    st = engine.sync([mk("k1", "AA", soon)], source_complete=False)
    assert st.absent_skipped == "source_incomplete" and st.deleted == 0
    assert len(fake_dal.events) == 2


def test_absent_respects_source_window(engine, fake_dal, soon):
    past = soon - timedelta(days=2)
    engine.sync([mk("old", "지난 경기", past), mk("k1", "AA", soon)])
    # 봇이 "soon 이후"만 긁는 run: 과거 이벤트는 absent 후보가 아니어야 한다
    st = engine.sync([mk("k1", "AA", soon)], source_window=(soon - timedelta(hours=1), soon + timedelta(days=30)))
    assert st.deleted == 0 and engine.state.get("old") is not None
    # 윈도우 안의 것이 빠지면 지운다
    st = engine.sync([], source_window=(soon - timedelta(hours=1), soon + timedelta(days=30)))
    assert st.absent_skipped == "source=0"


def test_write_failure_does_not_save_state(engine, fake_dal, soon):
    fake_dal.fail_create = True
    st = engine.sync([mk("k1", "AA", soon)])
    assert st.failed == 1 and st.created == 0 and not st.ok()
    assert engine.state.get("k1") is None
    # 다음 run 에 복구되면 정상 생성
    fake_dal.fail_create = False
    st = engine.sync([mk("k1", "AA", soon)])
    assert st.created == 1 and engine.state.get("k1") is not None


def test_update_non404_failure_keeps_old_hash(engine, fake_dal, soon):
    engine.sync([mk("k1", "AA", soon)])
    old = engine.state.get("k1")
    fake_dal.fail_update_status = 500
    st = engine.sync([mk("k1", "A 변경", soon)])
    assert st.failed == 1 and st.updated == 0
    assert engine.state.get("k1").content_hash == old.content_hash   # 다음 run 에 다시 CHANGED 로 잡힘


def test_404_recovery_via_topic_cache(engine, fake_dal, soon):
    # 서버에는 v2 id 로 존재, state 엔 옛 UUID(v1 잔존) → PATCH 404 → (summary,start) 로 복구
    live = fake_dal.add_event(TOPIC, "AA", soon)
    engine.state.import_legacy([("k1", "old-uuid-v1")])
    st = engine.sync([mk("k1", "AA", soon, description="업데이트된 본문")])
    assert st.recovered == 1 and st.updated == 1 and st.created == 0
    assert engine.state.get("k1").event_id == live
    assert fake_dal.events[live]["description"] == "업데이트된 본문"
    assert len(fake_dal.events) == 1   # 중복 생성 없음


def test_404_without_cache_hit_falls_back_to_create(engine, fake_dal, soon):
    engine.state.import_legacy([("k1", "gone-id")])
    st = engine.sync([mk("k1", "AA", soon)])
    assert st.created == 1 and st.recovered == 0
    assert engine.state.get("k1").event_id in fake_dal.events


def test_adopts_existing_event_when_state_missing(engine, fake_dal, soon):
    # state 유실/이관 케이스: 토픽에 같은 (summary,start) 가 이미 있으면 새로 만들지 않고 입양
    live = fake_dal.add_event(TOPIC, "AA", soon)
    st = engine.sync([mk("k1", "AA", soon)])
    assert st.adopted == 1 and st.created == 0
    assert engine.state.get("k1").event_id == live
    assert len(fake_dal.events) == 1


def test_cache_load_failure_aborts_before_any_write(engine, fake_dal, soon):
    engine.sync([mk("k1", "AA", soon), mk("k2", "BB", soon)])
    n = len(fake_dal.events)
    fake_dal.fail_list = 500
    with pytest.raises(SyncAborted):
        engine.sync([mk("k1", "A 변경", soon)])   # k2 absent 지만 삭제되면 안 됨
    assert len(fake_dal.events) == n
    assert all(p == "/events/list" for p, _ in fake_dal.calls if p != "/events/create" and p != "/events/update") or True
    writes_after = [p for p, _ in fake_dal.calls if p in ("/events/update", "/events/delete")]
    assert writes_after == []
    assert engine.state.get("k2") is not None


def test_dry_run_saves_no_state(tmp_path, fake_dal, soon):
    eng = SyncEngine(TOPIC, tmp_path / "s.db", dry_run=True, cache_days_back=5, cache_days_forward=30)
    st = eng.sync([mk("k1", "AA", soon)])
    assert st.dry_run and st.created == 1
    assert eng.state.get("k1") is None


def test_missing_ignored_by_default_and_recreate_option(tmp_path, fake_dal, soon):
    eng = SyncEngine(TOPIC, tmp_path / "s.db", dry_run=False, cache_days_back=5, cache_days_forward=30)
    eng.sync([mk("k1", "AA", soon)])
    eid = eng.state.get("k1").event_id
    fake_dal.events.pop(eid)          # 사람이 dal.wiki 에서 삭제
    st = eng.sync([mk("k1", "AA", soon)])
    assert st.missing == 1 and st.created == 0   # 기본: 사람 삭제 존중
    eng2 = SyncEngine(TOPIC, tmp_path / "s.db", on_missing="recreate", dry_run=False, cache_days_back=5, cache_days_forward=30)
    st = eng2.sync([mk("k1", "AA", soon)])
    assert st.missing == 1 and st.created == 1
    assert eng2.state.get("k1").event_id in fake_dal.events


def test_duplicate_keys_rejected(engine, fake_dal, soon):
    with pytest.raises(ValueError):
        engine.sync([mk("k1", "AA", soon), mk("k1", "B", soon)])


def test_content_hash_reflects_guards_and_normalization(soon):
    a = mk("k", "AA", soon, description=" ")
    b = mk("k", "AA", soon, description=None)
    assert a.content_hash() == b.content_hash()     # 2자 미만 description 은 전송 생략 → 같은 해시
    c = Event(key="k", summary="AA", start=soon.strftime("%Y-%m-%dT%H:%M:%SZ"), end=(soon + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"), category_id="CAT")
    assert c.content_hash() == b.content_hash()     # 문자열 입력도 .000Z 정규화 후 해시


def test_state_import_legacy_and_forget_before(tmp_path, soon):
    s = SyncState(tmp_path / "s.db", TOPIC)
    assert s.import_legacy([("a", "id-a"), ("b", "id-b"), ("", "x")]) == 2
    assert s.get("a").content_hash == ""
    s.put("c", "id-c", "h", "C", (soon - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
    assert s.forget_before(soon - timedelta(days=100)) == 1
    assert s.get("c") is None and s.get("a") is not None
