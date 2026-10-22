"""ingest_core.approval — 승인 등록 dedup(.152) + superseded."""
from __future__ import annotations

from datetime import timedelta

from dalwiki_client.client import _iso_z
from ingest_core import approval

TOPIC = "T1"


def _row(tmp_path, soon, **kw):
    db = str(tmp_path / "a.db")
    approval.enqueue("couponcal", topic_id=TOPIC, summary=kw.get("summary", "쿠폰 데이"),
                     start_iso=_iso_z(soon), end_iso=_iso_z(soon + timedelta(hours=1)),
                     category_id=kw.get("category_id", "CAT"), dedup_key="K", db_path=db)
    conn = approval._conn(db)
    return db, conn.execute("SELECT * FROM approval_items").fetchone()


def test_register_approved_patches_existing_instead_of_create(tmp_path, fake_dal, soon):
    x = fake_dal.add_event(TOPIC, "쿠폰 데이", soon, categoryId="CAT")   # 봇이 이미 자동 등록
    _, row = _row(tmp_path, soon)
    logs = []
    assert approval._register_approved(row, logs.append) is True
    assert fake_dal.paths("/events/create") == []
    assert [b["id"] for b in fake_dal.paths("/events/update")] == [x]
    assert len(fake_dal.events) == 1


def test_register_approved_creates_when_absent_or_other_category(tmp_path, fake_dal, soon):
    fake_dal.add_event(TOPIC, "쿠폰 데이", soon, categoryId="OTHER")
    _, row = _row(tmp_path, soon)
    assert approval._register_approved(row, print) is True
    assert len(fake_dal.paths("/events/create")) == 1


def test_register_approved_list_failure_does_not_create(tmp_path, fake_dal, soon):
    fake_dal.fail_list = True
    _, row = _row(tmp_path, soon)
    assert approval._register_approved(row, lambda m: None) is False
    assert fake_dal.paths("/events/create") == []


def test_supersede_hides_item_from_runner(tmp_path, soon):
    db, _ = _row(tmp_path, soon)
    assert approval.pending_count(db) == 1
    assert approval.supersede("couponcal", "K", db_path=db) is True
    assert approval.pending_count(db) == 0
    st = approval._conn(db).execute("SELECT status FROM approval_items").fetchone()[0]
    assert st == "superseded"
    assert approval.supersede("couponcal", "K", db_path=db) is False      # 이미 처리됨
