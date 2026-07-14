"""공용 승인 큐 + 디스코드 이모지 승인 (봇 무관 재사용).

설계:
  - 각 봇은 enqueue()만 호출한다(애매한 이벤트를 등록 대신 큐로).
  - enqueue는 '이미 완성된 dal.wiki 이벤트 필드'를 저장 → 봇별 등록 로직 불필요.
  - 승인 러너 run_cycle()이 전 봇 큐를 한 번에 처리:
      이전 게시물의 ✅/❌ 반응을 읽어 승인→dal.wiki 등록 / 거절→폐기, 새 항목 게시.
  - 상시 프로세스 불필요(배치 때 잠깐 접속).

★ 안전 원칙: enqueue()는 어떤 경우에도 예외를 던지지 않는다(봇 흐름을 막지 않음).
  실패 시 False를 반환하므로 호출부에서 '원래대로 등록'으로 폴백할 수 있다.

공용 DB: APPROVAL_DB_PATH(기본 ~/Documents/_approval/approval.db). 봇 컬럼으로 구분.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

KST = timezone(timedelta(hours=9))

_DEFAULT_DB = Path(os.getenv(
    "APPROVAL_DB_PATH",
    str(Path.home() / "Documents" / "_approval" / "approval.db")))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot TEXT NOT NULL,
    topic_id TEXT, summary TEXT,
    start_iso TEXT, end_iso TEXT, all_day INTEGER DEFAULT 1,
    description TEXT, link TEXT, location TEXT, category_id TEXT,
    confidence REAL, dedup_key TEXT,
    status TEXT DEFAULT 'new',        -- new | posted | approved | rejected
    message_id TEXT, created_at TEXT,
    UNIQUE(bot, dedup_key)
);
"""

EMOJI_YES = "✅"
EMOJI_NO = "❌"


def _conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    p = Path(db_path) if db_path else _DEFAULT_DB
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


def enqueue(bot: str, *, topic_id: str, summary: str, start_iso: str,
            end_iso: str, description: str = "", link: str = "",
            location: str = "", category_id: Optional[str] = None,
            all_day: bool = True, confidence: float = 0.0,
            dedup_key: Optional[str] = None,
            db_path: Optional[str] = None) -> bool:
    """애매한 이벤트를 승인 대기 큐에 넣는다. 절대 예외를 던지지 않는다.

    Returns: 새로 큐에 들어갔으면 True. 이미 있거나 실패면 False.
    """
    try:
        conn = _conn(db_path)
        cur = conn.execute(
            """INSERT OR IGNORE INTO approval_items
               (bot, topic_id, summary, start_iso, end_iso, all_day,
                description, link, location, category_id, confidence,
                dedup_key, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'new',?)""",
            (bot, topic_id, summary, start_iso, end_iso, int(all_day),
             description, link, location, category_id, confidence,
             dedup_key or summary, datetime.now(KST).isoformat()))
        conn.commit()
        ok = cur.rowcount > 0
        conn.close()
        return ok
    except Exception:
        return False


def pending_count(db_path: Optional[str] = None) -> int:
    try:
        conn = _conn(db_path)
        n = conn.execute(
            "SELECT COUNT(*) FROM approval_items WHERE status IN ('new','posted')"
        ).fetchone()[0]
        conn.close()
        return n
    except Exception:
        return 0


# ── 디스코드 승인 러너 ────────────────────────────────────────
def _embed(discord, row):
    e = discord.Embed(
        title=(row["summary"] or "")[:250], url=(row["link"] or None),
        description="\n".join(x for x in [
            f"**봇** {row['bot']}",
            f"**시작** {(row['start_iso'] or '')[:10] or '-'}",
            f"**마감** {(row['end_iso'] or '')[:10] or '-'}",
            f"**신뢰도** {row['confidence']:.2f}" if row['confidence'] is not None else "",
        ] if x))
    e.set_footer(text="✅ 승인 → dal.wiki 등록 / ❌ 미승인 → 폐기 (다음 배치 반영)")
    return e


def _register_approved(row, log) -> bool:
    """승인된 항목을 dal.wiki에 등록. 성공 True."""
    try:
        from dalwiki_client import update_or_create_event
        new_id = update_or_create_event(
            None, topic_id=row["topic_id"], summary=row["summary"],
            start=row["start_iso"], end=row["end_iso"],
            all_day=bool(row["all_day"]), description=row["description"] or None,
            link=row["link"] or None, location=row["location"] or None,
            category_id=row["category_id"])
        return bool(new_id)
    except Exception as e:
        log(f"  [승인] 등록 실패: {e}")
        return False


def run_cycle(*, token: str, channel_id: str, db_path: Optional[str] = None,
              log=print) -> None:
    """배치에서 호출. 접속→반응처리+게시→종료. 상시 프로세스 불필요."""
    if not token or not channel_id:
        log("  [승인] 토큰/채널 미설정 — 스킵")
        return
    try:
        import discord
    except Exception:
        log("  [승인] discord.py 없음 — 스킵")
        return

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        approved = rejected = posted = 0
        try:
            channel = client.get_channel(int(channel_id)) or \
                await client.fetch_channel(int(channel_id))
            conn = _conn(db_path)
            # 1) 이전 게시물 반응 처리
            for r in conn.execute("SELECT * FROM approval_items WHERE status='posted'").fetchall():
                if not r["message_id"]:
                    continue
                try:
                    msg = await channel.fetch_message(int(r["message_id"]))
                except discord.NotFound:
                    conn.execute("UPDATE approval_items SET status='rejected' WHERE id=?", (r["id"],))
                    conn.commit(); continue
                except Exception:
                    continue
                yes = no = False
                for reaction in msg.reactions:
                    emoji = str(reaction.emoji)
                    if emoji not in (EMOJI_YES, EMOJI_NO):
                        continue
                    try:
                        async for u in reaction.users():
                            if getattr(u, "bot", False):
                                continue
                            yes = yes or emoji == EMOJI_YES
                            no = no or emoji == EMOJI_NO
                    except Exception:
                        pass
                if yes:
                    ok = _register_approved(r, log)
                    conn.execute("UPDATE approval_items SET status=? WHERE id=?",
                                 ("approved" if ok else "posted", r["id"]))
                    conn.commit()
                    if ok:
                        approved += 1
                        try: await msg.edit(content="✅ **승인됨** — dal.wiki 등록 완료")
                        except Exception: pass
                elif no:
                    conn.execute("UPDATE approval_items SET status='rejected' WHERE id=?", (r["id"],))
                    conn.commit(); rejected += 1
                    try: await msg.edit(content="❌ **미승인** — 폐기")
                    except Exception: pass
            # 2) 새 항목 게시
            for r in conn.execute("SELECT * FROM approval_items WHERE status='new'").fetchall():
                try:
                    m = await channel.send(embed=_embed(discord, r))
                    await m.add_reaction(EMOJI_YES)
                    await m.add_reaction(EMOJI_NO)
                except Exception as e:
                    log(f"  [승인] 게시 실패: {e}"); continue
                conn.execute("UPDATE approval_items SET status='posted', message_id=? WHERE id=?",
                             (str(m.id), r["id"]))
                conn.commit(); posted += 1
            conn.close()
            log(f"  [승인] 승인 {approved} · 거절 {rejected} · 신규게시 {posted}")
        except Exception as e:
            log(f"  [승인] 오류: {e}")
        finally:
            await client.close()

    client.run(token, log_handler=None)


def run_forever(*, token: str, channel_id: str, db_path: Optional[str] = None,
                poll_seconds: int = 90, log=print) -> None:
    """상시 실행 러너 — PC 켜져 있는 동안 계속 접속.
    ✅/❌ 반응은 즉시 처리(on_raw_reaction_add), 새 항목은 poll_seconds마다 게시.
    유휴 시 리소스 거의 0. 연결 끊기면 discord.py가 자동 재접속."""
    if not token or not channel_id:
        log("[승인-상시] 토큰/채널 미설정 — 종료")
        return
    try:
        import discord
        from discord.ext import tasks
    except Exception:
        log("[승인-상시] discord.py 없음")
        return

    chan_id = int(channel_id)
    client = discord.Client(intents=discord.Intents.default())

    async def _channel():
        return client.get_channel(chan_id) or await client.fetch_channel(chan_id)

    @tasks.loop(seconds=poll_seconds)
    async def poster():
        try:
            channel = await _channel()
            conn = _conn(db_path)
            for r in conn.execute("SELECT * FROM approval_items WHERE status='new'").fetchall():
                try:
                    m = await channel.send(embed=_embed(discord, r))
                    await m.add_reaction(EMOJI_YES)
                    await m.add_reaction(EMOJI_NO)
                    conn.execute("UPDATE approval_items SET status='posted', message_id=? WHERE id=?",
                                 (str(m.id), r["id"]))
                    conn.commit()
                    log(f"[승인-상시] 게시: {r['summary'][:40]}")
                except Exception as e:
                    log(f"[승인-상시] 게시 실패: {e}")
            conn.close()
        except Exception as e:
            log(f"[승인-상시] poster 오류: {e}")

    @client.event
    async def on_ready():
        if not poster.is_running():
            poster.start()
        log(f"[승인-상시] 준비 완료: {client.user}")

    @client.event
    async def on_raw_reaction_add(payload):
        try:
            if client.user and payload.user_id == client.user.id:
                return
            emoji = str(payload.emoji)
            if emoji not in (EMOJI_YES, EMOJI_NO):
                return
            conn = _conn(db_path)
            r = conn.execute(
                "SELECT * FROM approval_items WHERE message_id=? AND status='posted'",
                (str(payload.message_id),)).fetchone()
            if not r:
                conn.close(); return
            channel = await _channel()
            try:
                msg = await channel.fetch_message(payload.message_id)
            except Exception:
                msg = None
            if emoji == EMOJI_YES:
                ok = _register_approved(r, log)
                conn.execute("UPDATE approval_items SET status=? WHERE id=?",
                             ("approved" if ok else "posted", r["id"]))
                conn.commit()
                if ok and msg:
                    try: await msg.edit(content="✅ **승인됨** — dal.wiki 등록 완료")
                    except Exception: pass
                log(f"[승인-상시] 승인→등록: {r['summary'][:40]}")
            else:
                conn.execute("UPDATE approval_items SET status='rejected' WHERE id=?", (r["id"],))
                conn.commit()
                if msg:
                    try: await msg.edit(content="❌ **미승인** — 폐기")
                    except Exception: pass
                log(f"[승인-상시] 미승인: {r['summary'][:40]}")
            conn.close()
        except Exception as e:
            log(f"[승인-상시] 반응처리 오류: {e}")

    client.run(token, log_handler=None)
