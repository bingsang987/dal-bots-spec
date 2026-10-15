# W2 봇 재구축 공통 브리프 (2026-10-15)

재구축 세션은 이 문서를 먼저 읽는다. 계획서 §2·§3.1·§5-확정: `C:\Users\gamer\Documents\GitHub\pointfarm\docs\bot-fleet-remediation-plan-2026-10-15.md`. 봇별 발견 사항: 같은 폴더 `bot-fleet-audit-2026-10-15.md` §5 + beads `bd show dalbots-bmi.<n>`(dal-bots-spec 레포에서).

## 목표
기존 봇을 **botkit 위의 얇은 어댑터**로 다시 짠다. 소스 파싱 로직과 토픽/카테고리 ID는 옛 코드에서 가져오고, dal.wiki 쓰기·상태·중복·알림·로그는 전부 botkit에 맡긴다. 목표 분량 200~400줄.

## 공통 기반 (dal-bots-spec, 전역 python 3.14에 editable 설치됨)
- `from dalwiki_client import list_events, get_event, ListFailed, TopicCache ...` — list_events는 실패 시 `ListFailed` 예외(기본).
- `from botkit.sync import SyncEngine, Event, SyncState, SyncAborted` — `Event(key, summary, start, end, all_day, category_id, description, link, location)`; `SyncEngine(topic_id, state_path, absent="delete"|"keep", ...)`, `engine.sync(events, source_ok=True/False)`; 소스 0건/일부 실패면 `source_ok=False`로 넘겨 소멸 판정을 건너뛴다.
- `from botkit.run import run, alert` — `run("봇이름", main)`이 로그 회전·예외 시 `[HEALTH-ALERT]`·exit code를 처리. 주 소스 전부 실패/0건이면 `alert("...")`.
- `from botkit.llm import llm_json, LLMUnavailable, HAIKU, SONNET` (LLM 쓰는 봇만).
- 사용법·시그니처는 `src/botkit/*.py` docstring과 `tests/`가 정본. README·spec/04-cross-cutting.md도 갱신돼 있다.

## 확정 규칙 (사용자 결정, 어기지 말 것)
1. 소스에서 사라진 이벤트 = **삭제**(absent="delete"). 단 그 run의 소스가 0건이거나 일부 실패면 삭제 판정 자체를 건너뛴다(엔진 `source_ok=False`).
2. 자체 dalwiki 래퍼/requests 직접 호출 금지. 쓰기는 SyncEngine만.
3. categoryId 항상 명시, summary에 `[카테고리]` 프리픽스 금지, summary 2~100자, ISO UTC `.000Z`, allDay는 00:00~23:59:59 KST, description 링크는 `target="_blank" rel="noopener noreferrer"`, **본문에 출처 표기 금지**(원본 링크는 link 필드만), 제목에 일정명 포함.
4. 하드코딩 시크릿·연도·시즌 금지: 키는 `.env`(기존 .env 값 그대로 재사용), 연도는 `today.year` 기준으로 계산하고 만료되면 `alert()`.
5. 죽은 소스는 제거한다(대체 소스 조사는 별도 이슈 dalbots-bmi.14). 모델 ID는 HAIKU/SONNET 상수만.
6. `run*.bat`는 ASCII 본문, `python C:\Users\gamer\bot_dashboard\track.py <라벨>` 줄 유지(라벨은 기존 것 그대로), 끝에 `pause`. 스케줄러 티어 bat는 **건드리지 말 것**(bat 파일명이 바뀌면 이 세션이 끝나고 pointfarm 세션이 티어를 고친다 — 가능하면 기존 bat 파일명 유지).
7. 대시보드·track.py 체계는 그대로.

## 절차
1. 옛 코드 전체를 읽고 "소스 → Event 목록" 변환에 필요한 것만 추린다(파서, ID 맵, 제목 규칙). 옛 파일은 `legacy/` 폴더로 옮긴다(삭제 금지, git 추적 유지).
2. 새 `bot.py`(+ 필요 시 `sources/*.py`) 작성. 안정키(key)는 소스의 고유 ID 우선(match id, game id, imdb id, 공고번호 등).
3. **상태 이관**: 옛 state(JSON/sqlite)에 있는 key→event_id를 `SyncState.import_legacy`로 옮긴다. 옛 상태가 없거나 못 믿으면 `TopicCache`로 (summary, start)→id를 채워 **기존 이벤트를 adopt**한다. 첫 실행에서 대량 삭제가 나오면 안 된다 — 첫 dry-run에서 삭제 예정이 전체의 10%를 넘으면 멈추고 원인을 적어라.
4. **검증**: `python bot.py --dry-run`으로 (key, summary, start, category) 목록을 뽑아 현재 dal.wiki 창(-30일~+90일)의 이벤트와 대조한다. 신규/변경/삭제 건수와 샘플을 보고서에 남긴다. 그 다음 `--apply`(또는 bat) 1회 실제 실행 → 결과 통계 → 바로 2회째 실행해서 **UNCHANGED가 전부**인지(멱등성) 확인.
5. 옛 bat의 파일명은 유지하되 내용을 새 진입점으로 바꾼다. README를 새 구조로 다시 쓴다(소스, 토픽/카테고리 ID, 실행법, 알려진 한계).
6. 봇 레포 커밋·푸시(`git add -A`, 메시지 끝에 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`). beads는 dal-bots-spec 레포에서 해당 `dalbots-bmi.<n>` 이슈에 결과 코멘트 후 close(남은 항목은 새 이슈로).
7. 마지막 보고에 반드시: 변경 전/후 dal.wiki 이벤트 수, 삭제된 건수와 이유, 멱등성 확인 결과, 티어 bat 수정이 필요한지(파일명 변경 여부).

## 금지
- 다른 봇 폴더·스케줄러·dal-bots-spec 패키지 코드 수정(패키지 버그를 찾으면 고치지 말고 보고서에 적고 beads에 등록).
- 데이터 대량 삭제·토픽 생성·카테고리 생성(필요하면 멈추고 보고).
