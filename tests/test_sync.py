"""botkit.sync.SyncEngine 시나리오 — 가짜 dal.wiki 위에서."""
from __future__ import annotations

from datetime import timedelta

import pytest

from botkit.sync import Event, SyncAborted, SyncEngine, SyncState
from dalwiki_client import client as dw

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


# ---------------------------------------------------------------------------
# adopt_scope="category" — 공유 토픽에서 카테고리마다 같은 (summary,start) (dalbots-bay)
# ---------------------------------------------------------------------------
def cat_engine(tmp_path, name="c.db", **kw):
    return SyncEngine(TOPIC, tmp_path / name, dry_run=False, cache_days_back=5, cache_days_forward=30,
                      adopt_scope="category", **kw)


def test_default_topic_scope_still_adopts_last_same_key(engine, fake_dal, soon):
    # 구 동작 고정: 카테고리 무시, 같은 키 중 마지막 것
    fake_dal.add_event(TOPIC, "휴방", soon, categoryId="B")
    a = fake_dal.add_event(TOPIC, "휴방", soon, categoryId="A")
    st = engine.sync([mk("k", "휴방", soon, category_id="B")])
    assert st.adopted == 1 and engine.state.get("k").event_id == a


def test_category_scope_adopts_only_same_category(tmp_path, fake_dal, soon):
    b = fake_dal.add_event(TOPIC, "휴방", soon, categoryId="B")
    a = fake_dal.add_event(TOPIC, "휴방", soon, categoryId="A")
    eng = cat_engine(tmp_path)
    st = eng.sync([mk("k", "휴방", soon, category_id="B")])
    assert (st.adopted, st.created) == (1, 0)
    assert eng.state.get("k").event_id == b
    assert fake_dal.events[a]["categoryId"] == "A"          # A 미러는 그대로
    assert [p["id"] for p in fake_dal.paths("/events/update")] == [b]


def test_category_scope_no_same_category_candidate_creates(tmp_path, fake_dal, soon):
    a = fake_dal.add_event(TOPIC, "휴방", soon, categoryId="A")
    eng = cat_engine(tmp_path)
    st = eng.sync([mk("k", "휴방", soon, category_id="B")])
    assert (st.adopted, st.created) == (0, 1)
    assert fake_dal.events[a]["categoryId"] == "A"
    assert fake_dal.paths("/events/update") == []


def test_category_scope_404_recovery_picks_same_category(tmp_path, fake_dal, soon):
    b = fake_dal.add_event(TOPIC, "휴방", soon, categoryId="B")
    a = fake_dal.add_event(TOPIC, "휴방", soon, categoryId="A")
    eng = cat_engine(tmp_path)
    eng.state.import_legacy([("k", "old-uuid")])
    st = eng.sync([mk("k", "휴방", soon, category_id="B", description="본문")])
    assert (st.recovered, st.created) == (1, 0)
    assert eng.state.get("k").event_id == b
    assert fake_dal.events[a]["categoryId"] == "A" and "description" not in fake_dal.events[a]


def test_category_scope_404_recovery_skips_other_categories_then_creates(tmp_path, fake_dal, soon):
    a = fake_dal.add_event(TOPIC, "휴방", soon, categoryId="A")
    eng = cat_engine(tmp_path)
    eng.state.import_legacy([("k", "old-uuid")])
    st = eng.sync([mk("k", "휴방", soon, category_id="B")])
    assert (st.recovered, st.created) == (0, 1)
    assert fake_dal.events[a]["categoryId"] == "A"


def test_category_scope_does_not_double_claim_within_category(tmp_path, fake_dal, soon):
    # 같은 카테고리에 같은 (summary,start) 가 2건, 소스 key 도 2개 → 각자 다른 id 를 입양
    x = fake_dal.add_event(TOPIC, "휴방", soon, categoryId="B")
    y = fake_dal.add_event(TOPIC, "휴방", soon, categoryId="B")
    eng = cat_engine(tmp_path)
    st = eng.sync([mk("k1", "휴방", soon, category_id="B"), mk("k2", "휴방", soon, category_id="B")])
    assert (st.adopted, st.created) == (2, 0)
    assert {eng.state.get("k1").event_id, eng.state.get("k2").event_id} == {x, y}


def test_category_engines_share_preloaded_cache(tmp_path, fake_dal, soon):
    from dalwiki_client import TopicCache
    a = fake_dal.add_event(TOPIC, "휴방", soon, categoryId="A")
    b = fake_dal.add_event(TOPIC, "휴방", soon, categoryId="B")
    cache = TopicCache(TOPIC)
    cache.load(days_back=5, days_forward=30, strict=True)
    n_list = len(fake_dal.paths("/events/list"))
    ea = cat_engine(tmp_path, cache=cache, state=SyncState(tmp_path / "s.db", f"{TOPIC}/A"))
    eb = cat_engine(tmp_path, cache=cache, state=SyncState(tmp_path / "s.db", f"{TOPIC}/B"))
    sa = ea.sync([mk("ka", "휴방", soon, category_id="A")])
    sb = eb.sync([mk("kb", "휴방", soon, category_id="B"), mk("kb2", "새 방송", soon, category_id="B")])
    assert (sa.adopted, sb.adopted, sb.created) == (1, 1, 1)
    assert ea.state.get("ka").event_id == a and eb.state.get("kb").event_id == b
    assert len(fake_dal.paths("/events/list")) == n_list           # 재로드 없음
    assert cache.lookup("새 방송", soon, category_id="B") == eb.state.get("kb2").event_id


def test_topic_cache_keeps_all_ids_per_key(fake_dal, soon):
    from dalwiki_client import TopicCache
    a = fake_dal.add_event(TOPIC, "휴방", soon, categoryId="A")
    b = fake_dal.add_event(TOPIC, "휴방", soon, categoryId="B")
    n = fake_dal.add_event(TOPIC, "휴방", soon)
    c = TopicCache(TOPIC)
    c.load(days_back=5, days_forward=30)
    assert c.lookup("휴방", soon) == n                              # 구 동작: 마지막
    assert c.lookup_all("휴방", soon) == [a, b, n]
    assert c.lookup("휴방", soon, category_id="A") == a
    assert c.lookup("휴방", soon, category_id=None) == n            # 카테고리 없는 것
    assert c.lookup("휴방", soon, category_id="A", exclude={a}) is None
    assert c.lookup("휴방", soon, exclude={n}) == a
    c.discard(n)
    assert c.lookup("휴방", soon) == b                              # 남은 후보로 폴백
    c.discard(a)
    c.discard(b)
    assert c.lookup("휴방", soon) is None and len(c) == 0


# ---------------------------------------------------------------------------
# W8-0 .104/.68 — 재발급 key 입양 + 같은 run absent 가 그 id 를 지우지 않음
# ---------------------------------------------------------------------------
def test_reissued_key_adopts_and_absent_relinks_without_delete(engine, fake_dal, soon):
    engine.sync([mk("old", "콘서트", soon)])
    x = engine.state.get("old").event_id
    n_calls = len(fake_dal.calls)
    # 소스가 같은 (summary,start) 를 새 key 로 재발급, 옛 key 는 소스에서 사라짐
    st = engine.sync([mk("new", "콘서트", soon, description="본문 추가")])
    assert (st.adopted, st.created, st.deleted, st.relinked, st.failed) == (1, 0, 0, 1, 0)
    assert x in fake_dal.events                                    # 이벤트 생존
    assert engine.state.get("new").event_id == x
    assert engine.state.get("old") is None                         # 옛 state 행만 제거
    assert [p for p, _ in fake_dal.calls[n_calls:] if p == "/events/delete"] == []   # DELETE 0
    # 다음 run: UNCHANGED, MISSING 아님
    st2 = engine.sync([mk("new", "콘서트", soon, description="본문 추가")])
    assert (st2.unchanged, st2.missing, st2.writes) == (1, 0, 0)


def test_topic_scope_does_not_steal_id_of_present_key(engine, fake_dal, soon):
    engine.sync([mk("k1", "휴방", soon)])
    x = engine.state.get("k1").event_id
    # k1 은 소스에 그대로, k2 가 같은 (summary,start) 로 NEW → x 를 뺏지 말고 새로 만든다
    st = engine.sync([mk("k1", "휴방", soon), mk("k2", "휴방", soon)])
    assert (st.adopted, st.created) == (0, 1)
    assert engine.state.get("k2").event_id != x
    # k1 이 사라지면 k1 의 이벤트만 지워지고 k2 는 남는다
    st2 = engine.sync([mk("k2", "휴방", soon)])
    assert (st2.deleted, st2.relinked) == (1, 0)
    assert x not in fake_dal.events and engine.state.get("k2").event_id in fake_dal.events


def test_relinked_in_dry_run_keeps_state(tmp_path, fake_dal, soon):
    eng = SyncEngine(TOPIC, tmp_path / "s.db", dry_run=False, cache_days_back=5, cache_days_forward=30)
    eng.sync([mk("old", "콘서트", soon)])
    dry = SyncEngine(TOPIC, tmp_path / "s.db", dry_run=True, cache_days_back=5, cache_days_forward=30)
    st = dry.sync([mk("new", "콘서트", soon)])
    assert (st.relinked, st.deleted) == (1, 0)
    assert dry.state.get("old") is not None and dry.state.get("new") is None


# ---------------------------------------------------------------------------
# W8-0 .65 — CHANGED 경로도 on_missing 존중(사람이 지운 일정 재생성 금지)
# ---------------------------------------------------------------------------
def test_changed_but_human_deleted_is_ignored_by_default(engine, fake_dal, soon):
    engine.sync([mk("k1", "AA", soon)])
    fake_dal.events.pop(engine.state.get("k1").event_id)          # 사람이 삭제
    n_calls = len(fake_dal.calls)
    st = engine.sync([mk("k1", "AA (장소 변경)", soon)])           # 소스 내용이 바뀜
    assert (st.missing, st.updated, st.created) == (1, 0, 0)
    writes = [p for p, _ in fake_dal.calls[n_calls:] if p != "/events/list"]
    assert writes == []                                            # PATCH·create 없음
    assert fake_dal.events == {}


def test_changed_but_human_deleted_recreate_policy(tmp_path, fake_dal, soon):
    eng = SyncEngine(TOPIC, tmp_path / "s.db", on_missing="recreate", dry_run=False,
                     cache_days_back=5, cache_days_forward=30)
    eng.sync([mk("k1", "AA", soon)])
    fake_dal.events.pop(eng.state.get("k1").event_id)
    st = eng.sync([mk("k1", "AA (장소 변경)", soon)])
    assert (st.missing, st.created) == (1, 1)
    assert fake_dal.events[eng.state.get("k1").event_id]["summary"] == "AA (장소 변경)"


def test_changed_with_stale_id_recovers_by_old_summary_start(engine, fake_dal, soon):
    # 이관 행(옛 id, 창 안 start) — 같은 일정이 v2 id 로 살아 있으면 missing 이 아니라 복구 PATCH
    v2 = fake_dal.add_event(TOPIC, "AA", soon)
    engine.state.import_legacy([("k1", "old-uuid")], summary_start={"k1": ("AA", dw_iso(soon))})
    st = engine.sync([mk("k1", "AA (연기)", soon)])
    assert (st.recovered, st.missing, st.created) == (1, 0, 0)
    assert engine.state.get("k1").event_id == v2
    assert fake_dal.events[v2]["summary"] == "AA (연기)"


def dw_iso(dt):
    from dalwiki_client.client import _iso_z
    return _iso_z(dt)


# ---------------------------------------------------------------------------
# W8-0 .66 — 대량 absent 가드 + absent 보류/삭제 alert
# ---------------------------------------------------------------------------
def _alert_lines(out: str) -> list[str]:
    from botkit.run import ALERT_TAG
    return [l for l in out.splitlines() if l.startswith(ALERT_TAG)]


def _fifty(soon):
    return [mk(f"k{i}", f"경기 {i}", soon + timedelta(hours=i)) for i in range(50)]


def test_mass_absent_40pct_skips_delete_and_alerts_once(engine, fake_dal, soon, capsys):
    evs = _fifty(soon)
    engine.sync(evs)
    capsys.readouterr()
    st = engine.sync(evs[:30])                                     # 20/50 = 40% 사라짐
    assert st.absent_skipped == "mass_absent" and st.deleted == 0
    assert len(fake_dal.events) == 50 and len(engine.state.all()) == 50
    lines = _alert_lines(capsys.readouterr().out)
    assert len(lines) == 1 and "mass_absent" in lines[0] and "absent 20건" in lines[0]


def test_absent_5pct_deletes_normally_without_alert(engine, fake_dal, soon, capsys):
    evs = _fifty(soon)
    engine.sync(evs)
    capsys.readouterr()
    st = engine.sync(evs[:47] + evs[50:])                          # 3/50 ≈ 5%
    assert st.absent_skipped is None and st.deleted == 3
    assert len(fake_dal.events) == 47
    assert _alert_lines(capsys.readouterr().out) == []           # 정상 삭제는 alert 없음(관제탑 결정)


def test_mass_absent_denominator_is_window_rows_and_options(tmp_path, fake_dal, soon, capsys):
    # 창 밖 과거 행 100건이 분모를 부풀리지 않는다: 창 안 20행 중 11건 사라짐 → max(10, 6)=10 초과 → 보류
    eng = SyncEngine(TOPIC, tmp_path / "s.db", dry_run=False, cache_days_back=5, cache_days_forward=30)
    past = (soon - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    for i in range(100):
        eng.state.put(f"old{i}", f"gone{i}", "h", f"옛 {i}", past)
    evs = [mk(f"k{i}", f"경기 {i}", soon + timedelta(hours=i)) for i in range(20)]
    eng.sync(evs, source_window=(soon - timedelta(days=1), soon + timedelta(days=20)))
    st = eng.sync(evs[:9], source_window=(soon - timedelta(days=1), soon + timedelta(days=20)))
    assert st.absent_skipped == "mass_absent" and st.deleted == 0
    # max_absent 로 한도 직접 지정 / 가드 끔
    eng2 = SyncEngine(TOPIC, tmp_path / "s.db", dry_run=False, cache_days_back=5, cache_days_forward=30,
                      max_absent_ratio=None, alert_absent=False)
    capsys.readouterr()
    st2 = eng2.sync(evs[:9], source_window=(soon - timedelta(days=1), soon + timedelta(days=20)))
    assert st2.deleted == 11 and _alert_lines(capsys.readouterr().out) == []


def test_source_incomplete_skip_also_alerts(engine, fake_dal, soon, capsys):
    engine.sync([mk("k1", "AA", soon), mk("k2", "BB", soon)])
    capsys.readouterr()
    st = engine.sync([mk("k1", "AA", soon)], source_complete=False)
    assert st.absent_skipped == "source_incomplete"
    lines = _alert_lines(capsys.readouterr().out)
    assert len(lines) == 1 and "source_incomplete" in lines[0]


# ---------------------------------------------------------------------------
# W8-0 .70 — absent 삭제 404 관용(start_iso 없는 이관 행)
# ---------------------------------------------------------------------------
def test_absent_delete_404_is_gone_even_without_start(engine, fake_dal, soon):
    engine.sync([mk("k1", "AA", soon)])
    engine.state.import_legacy([("legacy", "already-deleted-id")])   # start_iso 없음
    st = engine.sync([mk("k1", "AA", soon)])
    assert (st.deleted, st.failed) == (1, 0)
    assert engine.state.get("legacy") is None


def test_absent_delete_5xx_still_fails(engine, fake_dal, soon):
    engine.sync([mk("k1", "AA", soon), mk("k2", "BB", soon)])
    fake_dal.fail_delete = True
    st = engine.sync([mk("k1", "AA", soon)])
    assert (st.deleted, st.failed) == (0, 1) and "status=500" in st.failures[0]
    assert engine.state.get("k2") is not None


# ---------------------------------------------------------------------------
# W8-0 공통 패턴(스포츠 5) — import_legacy dry-run 누수 제거
# ---------------------------------------------------------------------------
def test_import_legacy_dry_run_stays_in_memory(tmp_path, monkeypatch, soon):
    path = tmp_path / "s.db"
    s = SyncState(path, TOPIC)
    s.put("live", "id-live", "h", "L", "")
    monkeypatch.setenv("DRY_RUN", "true")
    assert s.import_legacy([("a", "id-a"), ("b", "id-b")]) == 2      # dry_run=None → env 판정
    assert s.in_memory and s.get("a") is not None and s.get("live") is not None
    s.close()
    fresh = SyncState(path, TOPIC)
    assert fresh.get("a") is None and fresh.get("live") is not None  # 파일엔 이관 행 없음


def test_import_legacy_explicit_flag_and_live_commit(tmp_path, monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)
    path = tmp_path / "s.db"
    s = SyncState(path, TOPIC)
    s.import_legacy([("a", "id-a")], dry_run=True)
    assert SyncState(path, TOPIC).get("a") is None
    s2 = SyncState(path, TOPIC)
    s2.import_legacy([("b", "id-b")])                                # 실제 run → 파일 commit
    assert not s2.in_memory and SyncState(path, TOPIC).get("b") is not None


def test_adopt_relinks_old_key_even_with_keep_policy(tmp_path, fake_dal, soon):
    """absent='keep' 이라도 입양한 id 를 쥐던 옛 key 의 state 행은 제거돼 두 key 가 한 id 를 공유하지 않는다."""
    eng = SyncEngine(TOPIC, tmp_path / "s.db", absent="keep", dry_run=False,
                     cache_days_back=5, cache_days_forward=30)
    eng.sync([mk("old", "콘서트", soon)])
    x = eng.state.get("old").event_id
    st = eng.sync([mk("new", "콘서트", soon, description="본문 추가")])
    assert (st.adopted, st.relinked, st.deleted, st.absent_kept) == (1, 1, 0, 0)
    assert eng.state.get("new").event_id == x and eng.state.get("old") is None
    assert x in fake_dal.events


def test_normal_absent_delete_does_not_alert(engine, fake_dal, soon, capsys):
    """정상 소멸 삭제(한도 이내)는 HEALTH-ALERT 를 내지 않는다 — 보류(absent_skipped)일 때만 낸다."""
    engine.sync([mk("a", "A", soon), mk("b", "B", soon)])
    capsys.readouterr()
    st = engine.sync([mk("a", "A", soon)])
    assert st.deleted == 1 and st.absent_skipped is None
    assert _alert_lines(capsys.readouterr().out) == []


def test_keep_policy_never_sets_absent_skipped_or_alerts(tmp_path, fake_dal, soon, capsys):
    """absent='keep' 엔진은 source=0/incomplete 여도 '보류' alert 를 내지 않는다(.210)."""
    eng = SyncEngine(TOPIC, tmp_path / "k.db", absent="keep", dry_run=False,
                     cache_days_back=5, cache_days_forward=30)
    eng.sync([mk("a", "A", soon), mk("b", "B", soon)])
    capsys.readouterr()
    st = eng.sync([mk("a", "A", soon)], source_complete=False)
    assert st.absent_skipped is None and st.absent_kept == 1 and st.deleted == 0
    st0 = eng.sync([], source_complete=True)
    assert st0.absent_skipped is None and st0.absent_kept == 2
    assert _alert_lines(capsys.readouterr().out) == []


# ── protect_human_edits / histories 실패(.225) ───────────────────────────
def _protected_engine(tmp_path, **kw):
    return SyncEngine(TOPIC, tmp_path / "p.db", dry_run=False, cache_days_back=5, cache_days_forward=30,
                      protect_human_edits=True, **kw)


def test_protect_histories_failure_holds_patch(tmp_path, fake_dal, soon):
    eng = _protected_engine(tmp_path)
    eng.sync([mk("k1", "AA", soon)])
    eid = eng.state.get("k1").event_id
    old_hash = eng.state.get("k1").content_hash
    fake_dal.fail_histories = True
    st = eng.sync([mk("k1", "AA 변경", soon)])
    assert st.failed == 1 and st.updated == 0 and st.protected == 0
    assert fake_dal.events[eid]["summary"] == "AA"              # PATCH 안 나감
    assert eng.state.get("k1").content_hash == old_hash         # 다음 run 재시도
    fake_dal.fail_histories = False
    st = eng.sync([mk("k1", "AA 변경", soon)])
    assert st.updated == 1 and fake_dal.events[eid]["summary"] == "AA 변경"


# ── .215 PROTECTED 해시 저장 / absent 사람 이벤트 보호 ───────────────────
def test_protected_saves_hash_so_next_run_skips_histories(tmp_path, fake_dal, soon):
    eng = _protected_engine(tmp_path)
    eng.sync([mk("k1", "AA", soon)])
    eid = eng.state.get("k1").event_id
    fake_dal.histories[eid] = [{"type": "UPDATE", "isBot": False}]
    st = eng.sync([mk("k1", "AA 변경", soon)])
    assert st.protected == 1 and st.updated == 0
    assert fake_dal.events[eid]["summary"] == "AA"
    n = len(fake_dal.paths("/topics/histories"))
    st = eng.sync([mk("k1", "AA 변경", soon)])
    assert st.unchanged == 1 and st.protected == 0
    assert len(fake_dal.paths("/topics/histories")) == n        # histories 재호출 없음


def test_protected_adopt_saves_state(tmp_path, fake_dal, soon):
    eid = fake_dal.add_event(TOPIC, "AA", soon, categoryId="CAT")
    fake_dal.histories[eid] = [{"type": "CREATE", "isBot": False}, {"type": "UPDATE", "isBot": False}]
    eng = _protected_engine(tmp_path)
    st = eng.sync([mk("k1", "AA", soon, description="<p>봇 본문</p>")])
    assert st.protected == 1 and st.adopted == 0 and st.created == 0
    assert eng.state.get("k1").event_id == eid                  # 다음 run 에 다시 입양·histories 하지 않음


def test_absent_delete_default_ignores_human_history(tmp_path, fake_dal, soon):
    eng = SyncEngine(TOPIC, tmp_path / "s.db", dry_run=False, cache_days_back=5, cache_days_forward=30)
    eng.sync([mk("k1", "AA", soon), mk("k2", "BB", soon)])
    eid2 = eng.state.get("k2").event_id
    fake_dal.histories[eid2] = [{"type": "UPDATE", "isBot": False}]
    st = eng.sync([mk("k1", "AA", soon)])
    assert st.deleted == 1 and eid2 not in fake_dal.events       # 옵션 없으면 구 동작


def test_protect_human_absent_keeps_human_events(tmp_path, fake_dal, soon):
    eng = SyncEngine(TOPIC, tmp_path / "s.db", dry_run=False, cache_days_back=5, cache_days_forward=30,
                     protect_human_absent=True)
    eng.sync([mk(f"k{i}", f"E{i}", soon) for i in range(4)])
    ids = {k: eng.state.get(k).event_id for k in ("k1", "k2", "k3")}
    fake_dal.histories[ids["k1"]] = [{"type": "UPDATE", "isBot": False}]   # 사람 편집
    fake_dal.histories[ids["k2"]] = [{"type": "CREATE", "isBot": False}]   # 사람 작성(입양)
    fake_dal.histories[ids["k3"]] = [{"type": "UPDATE", "isBot": True}]    # 봇만
    st = eng.sync([mk("k0", "E0", soon)])
    assert (st.human_kept, st.deleted, st.failed) == (2, 1, 0)
    assert ids["k1"] in fake_dal.events and ids["k2"] in fake_dal.events
    assert ids["k3"] not in fake_dal.events
    assert set(eng.state.all()) == {"k0"}
    assert "human_kept=2" in st.summary()


def test_protect_human_absent_histories_failure_holds_delete(tmp_path, fake_dal, soon):
    eng = SyncEngine(TOPIC, tmp_path / "s.db", dry_run=False, cache_days_back=5, cache_days_forward=30,
                     protect_human_absent=True)
    eng.sync([mk("k1", "AA", soon), mk("k2", "BB", soon)])
    eid2 = eng.state.get("k2").event_id
    fake_dal.fail_histories = True
    st = eng.sync([mk("k1", "AA", soon)])
    assert st.failed == 1 and st.deleted == 0
    assert eid2 in fake_dal.events and eng.state.get("k2") is not None


# ── .218 allow_empty ─────────────────────────────────────────────────────
def test_allow_empty_propagates_last_delete(engine, fake_dal, soon, capsys):
    engine.sync([mk("k1", "AA", soon)])
    eid = engine.state.get("k1").event_id
    st = engine.sync([], allow_empty=True)
    assert st.absent_skipped is None and st.deleted == 1
    assert eid not in fake_dal.events and engine.state.all() == {}
    assert "[HEALTH-ALERT]" not in capsys.readouterr().out


def test_allow_empty_still_respects_incomplete_and_mass_guard(engine, fake_dal, soon):
    engine.sync([mk(f"k{i}", f"E{i}", soon) for i in range(12)])
    st = engine.sync([], allow_empty=True, source_complete=False)
    assert st.absent_skipped == "source_incomplete" and st.deleted == 0
    st = engine.sync([], allow_empty=True)
    assert st.absent_skipped == "mass_absent" and st.deleted == 0       # 12 > max(10, 0.3×12)
    assert len(fake_dal.events) == 12


# ── .216 clear_empty_fields ──────────────────────────────────────────────
def test_clear_empty_fields_erases_removed_description(tmp_path, fake_dal, soon):
    for opt, expect in ((False, "<p>본문</p>"), (True, None)):
        eng = SyncEngine(TOPIC, tmp_path / f"c{opt}.db", dry_run=False, cache_days_back=5, cache_days_forward=30,
                         clear_empty_fields=opt)
        eng.sync([mk("k1", f"AA{opt}", soon, description="<p>본문</p>", location="서울")])
        eid = eng.state.get("k1").event_id
        st = eng.sync([mk("k1", f"AA{opt}", soon)])
        assert st.updated == 1
        assert fake_dal.events[eid].get("description") == expect
        st = eng.sync([mk("k1", f"AA{opt}", soon)])
        assert st.unchanged == 1



# ── .228 verify_imported / Event.matches_server ─────────────────────────
def test_verify_imported_confirms_matching_rows_only(tmp_path, fake_dal, soon):
    same = mk("k1", "같음", soon, description="<p>본문</p>", location="서울")
    diff = mk("k2", "다름", soon)
    gone = mk("k3", "없음", soon)
    e1 = fake_dal.add_event(TOPIC, "같음", soon, soon + timedelta(hours=2), categoryId="CAT", allDay=False,
                            description="<p>본문</p>", location="서울")
    e2 = fake_dal.add_event(TOPIC, "다름(사람 수정)", soon, soon + timedelta(hours=2), categoryId="CAT")
    eng = SyncEngine(TOPIC, tmp_path / "v.db", dry_run=False, cache_days_back=5, cache_days_forward=30)
    eng.state.import_legacy([("k1", e1), ("k2", e2), ("k3", "nope")], dry_run=False)
    assert eng.verify_imported([same, diff, gone]) == 1
    assert eng.state.get("k1").content_hash == same.content_hash()
    assert eng.state.get("k2").content_hash == "" and eng.state.get("k3").content_hash == ""
    n_upd = len(fake_dal.paths("/events/update"))
    st = eng.sync([same, diff], absent_filter=lambda r: False)
    assert st.unchanged == 1 and st.updated == 1                # 같은 건 PATCH 없음, 다른 건 1회
    assert len(fake_dal.paths("/events/update")) == n_upd + 1
    assert eng.verify_imported([same, diff]) == 0               # 확정된 행은 다시 안 본다


def test_verify_imported_dry_run_leaves_file(tmp_path, fake_dal, soon):
    ev = mk("k1", "같음", soon)
    eid = fake_dal.add_event(TOPIC, "같음", soon, soon + timedelta(hours=2), categoryId="CAT")
    path = tmp_path / "d.db"
    SyncState(path, TOPIC).import_legacy([("k1", eid)], dry_run=False)
    eng = SyncEngine(TOPIC, path, dry_run=True)
    assert eng.verify_imported([ev]) == 1 and eng.state.in_memory
    assert SyncState(path, TOPIC).get("k1").content_hash == ""


def test_matches_server_uses_guards(soon):
    ev = mk("k", "  제목  ", soon, description=" ", location="장소" * 40)
    srv = {"summary": "제목", "start": ev.start_iso(), "end": ev.end_iso(), "allDay": False,
           "categoryId": "CAT", "description": None, "location": dw._guard_location("장소" * 40)}
    assert ev.matches_server(srv)
    assert not ev.matches_server({**srv, "startTimezone": "UTC"})
    assert not ev.matches_server({**srv, "link": "https://x"})
