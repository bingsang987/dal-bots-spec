"""botkit — dal.wiki 봇 공통 실행 기반 (dalwiki_client 위에 얹는 레이어).

    from botkit import run, alert, SyncEngine, Event, llm_json, HAIKU, SONNET

모듈:
    botkit.sync     SyncEngine — 소스 이벤트 목록 ↔ dal.wiki 토픽 동기화(상태 sqlite, NEW/CHANGED/
                    UNCHANGED, absent 정책, 404 복구, 쓰기 성공 후에만 state 저장)
    botkit.run      run(bot_name, main_fn) — 로그 회전, --dry-run 표준 처리, 예외 → [HEALTH-ALERT] + exit 1,
                    alert() 헬퍼
    botkit.llm      llm_json() — Claude JSON 호출(코드펜스 제거·raw_decode·429/529 백오프·
                    LLMUnavailable), 모델 상수 HAIKU/SONNET, 이미지 전처리
    botkit.discord  catchup() — Discord 채널 소급 처리(메시지별 성공 시에만 커서 전진, 실패는 재시도 큐)
    botkit.vod      VOD/미러 봇 공통 조각 — VOD 블록·append_vod, fetch_events_chunked(complete 플래그),
                    get_description/hydrate_descriptions, MissingGuard(Tombstone 대체), safe_summary
                    (`from botkit import vod`)

규약(spec/04-cross-cutting.md):
    - 자체 dalwiki 래퍼 금지. 쓰기는 전부 dalwiki_client 경유.
    - categoryId 항상 명시, summary 에 대괄호 카테고리 prefix 금지.
    - 소스 전부 실패 / 0건 / dal.wiki 장애 / LLM 불가 → alert() 한 줄(`[HEALTH-ALERT]`).
    - LLM 불가(LLMUnavailable) → 커서·state 전진 금지, 다음 run 재시도.
"""
from .run import RunContext, alert, run
from .sync import AbsentPolicy, Event, SyncAborted, SyncEngine, SyncState, SyncStats
from .llm import HAIKU, SONNET, LLMError, LLMParseError, LLMUnavailable, extract_json, llm_json

__all__ = [
    "run", "alert", "RunContext",
    "SyncEngine", "SyncState", "SyncStats", "SyncAborted", "Event", "AbsentPolicy",
    "llm_json", "extract_json", "HAIKU", "SONNET",
    "LLMError", "LLMUnavailable", "LLMParseError",
]
