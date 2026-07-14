"""수집 상태 저장 — 이미 처리한 원자료(RSS 엔트리/게시글) 스킵용.

키: (source, ext_id). 값: 마지막 본 sig. sig가 바뀌면 재처리(수정글 반영).
각 봇이 자기 디렉토리에 sqlite 파일 하나 둔다.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_items (
    source   TEXT NOT NULL,
    ext_id   TEXT NOT NULL,
    sig      TEXT,
    first_seen TEXT,
    last_seen  TEXT,
    PRIMARY KEY (source, ext_id)
);
"""


class SeenStore:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(_SCHEMA)

    def sig_of(self, source: str, ext_id: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT sig FROM seen_items WHERE source=? AND ext_id=?",
            (source, ext_id),
        ).fetchone()
        return row[0] if row else None

    def is_new_or_changed(self, source: str, ext_id: str, sig: str) -> bool:
        return self.sig_of(source, ext_id) != sig

    def mark(self, source: str, ext_id: str, sig: str, now_iso: str) -> None:
        self.conn.execute(
            """INSERT INTO seen_items (source, ext_id, sig, first_seen, last_seen)
               VALUES (?,?,?,?,?)
               ON CONFLICT(source, ext_id) DO UPDATE SET
                 sig=excluded.sig, last_seen=excluded.last_seen""",
            (source, ext_id, sig, now_iso, now_iso),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
