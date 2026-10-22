"""dalwiki_client.histories — 조회 실패와 '이력 없음' 구분(.225)."""
from __future__ import annotations

import sqlite3

import pytest

from dalwiki_client import histories as H
from dalwiki_client.histories import HistoriesFailed

TOPIC = "T1"
HUMAN_UPDATE = {"type": "UPDATE", "isBot": False, "userSlug": "u"}
BOT_UPDATE = {"type": "UPDATE", "isBot": True}


def test_histories_uses_patched_post(fake_dal):
    # import 시점 바인딩이면 dw._post 몽키패치가 안 먹고 실 API 로 나간다(.219-4)
    fake_dal.histories["e1"] = [HUMAN_UPDATE]
    assert H.list_histories(TOPIC, "e1") == [HUMAN_UPDATE]
    assert fake_dal.paths("/topics/histories")[0] == {"topicId": TOPIC, "take": 25, "skip": 0, "eventId": "e1"}


@pytest.mark.parametrize("mode", [True, "net", "body"])
def test_list_histories_failure_default_empty_or_raise(fake_dal, mode):
    fake_dal.fail_histories = mode
    assert H.list_histories(TOPIC, "e1") == []                  # 구 동작 유지
    with pytest.raises(HistoriesFailed):
        H.list_histories(TOPIC, "e1", raise_on_fail=True)


def test_empty_history_is_not_failure(fake_dal):
    assert H.list_histories(TOPIC, "e1", raise_on_fail=True) == []
    assert H.was_human_edited(TOPIC, "e1") is False
    assert H.get_latest_action(TOPIC, "e1") is None
    assert H.was_human_deleted(TOPIC, "e1") is False


def test_judgement_functions_raise_on_failure(fake_dal):
    fake_dal.fail_histories = True
    for fn in (H.was_human_edited, H.was_human_deleted, H.get_latest_action, H.deletion_actor,
               H.was_human_touched):
        with pytest.raises(HistoriesFailed):
            fn(TOPIC, "e1")
    # 명시적 구 동작
    assert H.was_human_edited(TOPIC, "e1", raise_on_fail=False) is False
    assert H.get_latest_action(TOPIC, "e1", raise_on_fail=False) is None


def test_was_human_edited_and_touched(fake_dal):
    fake_dal.histories["e1"] = [BOT_UPDATE, HUMAN_UPDATE]
    fake_dal.histories["e2"] = [BOT_UPDATE, {"type": "CREATE", "isBot": False}]
    assert H.was_human_edited(TOPIC, "e1") is True
    assert H.was_human_edited(TOPIC, "e2") is False
    assert H.was_human_touched(TOPIC, "e2") is True


def test_tombstone_sweep_skips_on_histories_failure(fake_dal):
    conn = sqlite3.connect(":memory:")
    tomb = H.TombstoneStore(conn, scope="main")
    popped = []
    fake_dal.fail_histories = True
    added = tomb.sweep_from_mapping(TOPIC, {"k": "gone"}, current_event_ids=[], on_pop=popped.append)
    assert added == [] and popped == []
    fake_dal.fail_histories = False
    fake_dal.histories["gone"] = [{"type": "DELETE", "isBot": False, "categoryId": "C",
                                   "start": "2026-11-01T00:00:00.000Z"}]
    assert tomb.sweep_from_mapping(TOPIC, {"k": "gone"}, current_event_ids=[], on_pop=popped.append) == ["gone"]
    assert popped == ["k"]
