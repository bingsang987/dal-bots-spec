# W7 코드 리뷰 — botkit 패키지 (관제탑 직접, 2026-10-22)

대상: `src/botkit/{sync,run,llm,discord,vod}.py`, `src/dalwiki_client/{client,discord_notify,histories}.py` (3,269줄). 체크리스트 `W7_REVIEW_CHECKLIST.md`. 전 봇의 공통 뿌리라 여기 결함 하나가 50개 봇 사고다.

사전 확인(의심했다가 해소): 티어 bat는 `2>&1`로 stderr도 캡처하므로 `traceback.print_exc()`가 RETRY 조건에 걸린다. `alert()`의 Discord 경로가 쓰는 `ingest_core`는 패키지 안에 남아 있어 import가 된다. gachabot은 `absent="keep"`, `source_window` 없는 absent=delete 봇들(coupon·museum·netflix·stockmarket·kdrama)은 `absent_filter`로 후보를 좁히고 있다. 봇별 호출 정확성은 리뷰 1~3 세션이 본다.

### sync.py (SyncEngine)
- [P1] [A5] sync.py:510-536 — **CHANGED 경로가 `on_missing`을 우회한다.** 사람이 지운 이벤트는 UNCHANGED일 때만 `missing`으로 존중되고, 소스 내용이 조금이라도 바뀌면 `_update` → 404 → 캐시 미스 → `_create`로 되살아난다. 근거: 526행 `if row.content_hash == h:` 블록 안에서만 `cache.has_id` 검사, 540행 `self._update(...)`는 바로 PATCH. 제안: CHANGED 분기 진입 전에 같은 `in_window and not has_id` 검사를 두고 `on_missing` 정책을 똑같이 적용(ignore면 missing+1 후 skip).
- [P1] [A1] sync.py:548-575 — **대량 absent 삭제 가드 없음.** 파서가 반쯤 깨져 소스가 100건 중 3건만 돌려주면(0건도 아니고 예외도 아님) `source_complete=True`인 채로 97건이 삭제된다. 브리프의 "dry-run에서 삭제 10% 넘으면 중단"은 사람이 보는 규칙이지 코드가 아니다. 제안: `SyncEngine(max_absent_ratio=0.3, max_absent=None)` — absent 후보가 `max(10, ratio×len(existing))`을 넘으면 `absent_skipped="mass_absent"` + 경고(호출자가 alert). 기존 시그니처 유지, 기본값으로 켬.
- [P2] [A4] sync.py:401-406 — `adopt_scope="topic"`(기본)일 때 `_lookup`이 `exclude=self._claimed`를 안 넘긴다. 소스에 같은 (summary,start)가 두 건이거나 키가 바뀐 경우 NEW가 **다른 key가 이미 쥔 id를 입양**해 두 key가 한 이벤트를 가리키고, 옛 key의 absent 삭제가 새 key의 이벤트를 지운다. 제안: 두 scope 모두 `exclude=self._claimed` 전달(동작 변화는 '남의 id를 안 뺏는다'뿐).
- [P2] [B4] sync.py:466-470 — `_delete`의 404 관용 판정이 `row.start_iso`를 요구해 `import_legacy`로 start 없이 이관된 행은 이미 지워진 이벤트를 매 run `delete 실패`로 세고(fail≠0) 영원히 state에 남는다. 제안: start_iso 비어 있으면 `delete_event` 404 응답(status)로 판정하거나 `on_missing="forget"` 경로로 보냄.
- [P3] sync.py:410-418 — `protect_human_edits`로 보호된 이벤트는 hash를 저장하지 않아 매 run histories를 다시 조회하고 PROTECTED 로그가 반복된다. 제안: 보호 판정 시 hash를 state에 저장(보호 상태 캐시) 또는 로그 레벨 debug.
- [P3] sync.py:493-496 — key 중복 `ValueError`가 run()까지 올라가 Traceback → 티어 RETRY가 같은 실패를 한 번 더 돌린다. 제안: `_fail`+alert 후 쓰기 0으로 정상 종료.

### run.py
- [P2] [F2] run.py:76-85 — `alert()`의 Discord 전송이 `ingest_core.discord`(taxcal 계열 옛 패키지)에 의존. 패키지 정리 시 조용히 사라진다(`except: pass`). 제안: `dalwiki_client.discord_notify` 웹훅(이미 모든 봇이 .env에 가짐)으로 통일하거나 botkit 자체 `alert_webhook`.
- [P3] run.py:170-186 — `_alerts`가 모듈 전역이라 한 프로세스에서 run()을 두 번 부르면 누적(테스트 외 영향 없음).

### discord.py
- [P1] [B3] discord.py:323-353 — **`run_catchup_once`에 타임아웃이 없다.** 게이트웨이가 `on_ready`를 안 주면 `client.run`이 영원히 블록 → 2x 티어 전체(그 뒤 봇 전부)가 멈춘다(감사 때 봇 행(hang) 사례 있었음). 제안: `on_ready` 미도달 N분(기본 5분) 시 close + `LLMUnavailable`과 같은 "커서 유지" 종료 + alert; `asyncio.wait_for` 또는 watchdog 스레드.
- [P2] [B3] discord.py:270-285 — handler 안에서 `SyncAborted`/`ListFailed`(dal.wiki 장애)가 나면 **메시지 1건 실패로 취급**돼 재시도 큐+커서 전진. dal.wiki가 3 run 연속 죽어 있으면 `max_attempts` 초과로 폐기 → 메시지 영구 누락. 제안: `stop_on=(LLMUnavailable, SyncAborted, ListFailed)` 파라미터로 "run 전체 중단, 커서 유지" 예외 집합을 확장.
- [P3] discord.py:233 — `retry_queue=None`이면 실패 메시지가 로그만 남고 커서 전진(문서에 '권장하지 않음'). 현재 호출 4곳 전부 큐를 넘기므로 영향 없음.

### llm.py
- [P2] [B2] llm.py:271-281 — `temperature`를 넘기면 5.x thinking 기본 모델은 400(temperature는 thinking과 함께 1만 허용)을 돌려주고 이 400은 재시도 대상이 아니라 그대로 raise → 봇 크래시. 현재 봇 중 temperature를 넘기는 곳은 없음(grep 0). 제안: thinking 모델이면 temperature 무시+경고.
- [P3] llm.py:212-221 — `api_key` 인자를 주면 매 호출 새 `Anthropic()` 클라이언트 생성(싱글턴 미사용).

### client.py / discord_notify.py / histories.py
- [P2] [G2] client.py:109-116 — 모든 2xx 쓰기마다 `discord_notify.enqueue`(웹훅 설정 시). 재구축 봇이 첫 run에 수백 건 PATCH하면 수십 개 임베드 메시지가 간다(배치 10개/5초). 운영상 소음일 뿐 손실은 없음. 제안: `DALWIKI_DISCORD_NOTIFY=create,delete`처럼 액션 필터 env.
- [P3] client.py:84-88 — `_get_session`이 첫 호출 때 API 키를 스냅샷. `.env` 로드 전에 조회를 한 번이라도 하면 그 프로세스는 키 없이 간다(현재 봇들은 import 전에 dotenv 로드).
- [P3] client.py:367-377 — `list_events` truncation 분할 `_max_depth=7`을 넘기면 잘린 채 조용히 반환(49일/128≈9시간 안에 1000건 이상일 때만).
- [P3] histories.py:94-103 — `was_human_edited`는 `lookback=10`건만 본다. 사람 수정 뒤 봇 UPDATE가 10번 넘게 쌓이면 보호가 풀린다(protect_human_edits 쓰는 봇: gachabot 등).

### vod.py
- 결함 없음. `fetch_events_chunked`의 complete 판정, `get_description` None 계약, `MissingGuard.sweep` 완전 조회 전제가 문서와 일치. [P3] `safe_summary`는 100자에서 '...' 없이 자른다(client 가드와 표기 불일치, 해시엔 영향 없음).

### 공통 패턴
1. "사람이 지운 것 존중"이 UNCHANGED 경로에만 있다(P1 1번). MissingGuard는 VOD 봇에만 있고 SyncEngine 봇은 이 구멍을 공유한다.
2. 실패를 "0건/예외"로만 감지하고 "부분 성공"을 감지하지 않는다(P1 2번). 파서 회귀가 가장 흔한 장애인데 가드가 없다.
3. 중단 예외 집합이 LLMUnavailable 하나뿐이다(discord P2). dal.wiki 장애도 같은 등급이어야 한다.
4. 외부 블로킹 호출(discord gateway)에 타임아웃이 없다(P1 3번).

### 처리 제안
P1 3건(+P2 adopt exclude·stop_on)은 패키지 한 번의 PR로 고치고 테스트를 붙인 뒤 전 봇에 자동 적용된다(시그니처 유지). 리뷰 1~3 결과와 합쳐 W8 수정 배치로.
