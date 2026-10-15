"""공용 픽스처 — 가짜 dal.wiki(`dalwiki_client.client._post` 몽키패치)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dalwiki_client import client as dw  # noqa: E402


class FakeDal:
    """in-memory dal.wiki. `_post(path, body)` 시그니처를 흉내 낸다."""

    def __init__(self):
        self.events: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self._n = 0
        # 장애 주입
        self.fail_list: object = False        # True → 500, "net" → None(재시도 소진), int → 그 status
        self.fail_create = False
        self.fail_update_status: int | None = None   # 404/500 등 강제
        self.fail_delete = False

    # -- helpers --------------------------------------------------------
    def add_event(self, topic_id, summary, start, end=None, **extra) -> str:
        self._n += 1
        eid = f"ev{self._n}"
        self.events[eid] = {
            "id": eid, "topicId": topic_id, "summary": summary,
            "start": dw._iso_z(start), "end": dw._iso_z(end or start), **extra,
        }
        return eid

    def paths(self, path: str) -> list[dict]:
        return [b for p, b in self.calls if p == path]

    # -- fake _post -----------------------------------------------------
    def post(self, path: str, body: dict, retries: int = 3):
        self.calls.append((path, dict(body)))
        if path == "/events/list":
            if self.fail_list == "net":
                return None
            if self.fail_list:
                return {"status": self.fail_list if isinstance(self.fail_list, int) and self.fail_list is not True else 500,
                        "body": None}
            frm, to = body.get("from"), body.get("to")
            evs = [
                e for e in self.events.values()
                if e["topicId"] == body["topicId"]
                and (frm is None or e["start"] >= frm)
                and (to is None or e["start"] < to)
            ]
            return {"status": 200, "body": evs[: body.get("take", 1000)]}
        if path == "/events/create":
            if self.fail_create:
                return {"status": 400, "body": None}
            self._n += 1
            eid = f"ev{self._n}"
            self.events[eid] = {"id": eid, **body}
            return {"status": 200, "body": {"id": eid}}
        if path == "/events/update":
            if self.fail_update_status:
                return {"status": self.fail_update_status, "body": None}
            eid = body["id"]
            if eid not in self.events:
                return {"status": 404, "body": None}
            self.events[eid].update({k: v for k, v in body.items() if k != "id"})
            return {"status": 200, "body": {"id": eid}}
        if path == "/events/delete":
            if self.fail_delete:
                return {"status": 500, "body": None}
            if body["id"] not in self.events:
                return {"status": 404, "body": None}
            self.events.pop(body["id"])
            return {"status": 200, "body": None}
        if path == "/events/get":
            e = self.events.get(body["id"])
            return {"status": 200, "body": e} if e else {"status": 404, "body": None}
        if path == "/topics/histories":
            return {"status": 200, "body": []}
        raise AssertionError(f"FakeDal: 처리 안 된 path {path}")


@pytest.fixture
def fake_dal(monkeypatch):
    fake = FakeDal()
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setattr(dw, "DRY_RUN", False)
    monkeypatch.setattr(dw, "_post", fake.post)
    return fake


@pytest.fixture
def soon():
    """TopicCache 기본 윈도우 안에 들어오는 미래 시각(aware UTC)."""
    base = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=3)
    return base
