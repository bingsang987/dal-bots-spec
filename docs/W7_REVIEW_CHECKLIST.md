# W7 코드 리뷰 체크리스트 (읽기 전용 감사, 2026-10-22)

W0~W6에서 재구축/이식/패치된 봇 코드를 **읽고** 결함을 찾는다. 실행 검증(dry-run·2회째 무변경·데이터 패트롤)은 이미 통과했으므로, 여기서는 "실행 경로에 안 걸렸지만 조건이 바뀌면 터지는 것"을 잡는다. 수정은 하지 않는다. 발견은 beads(dal-bots-spec, 에픽 `dalbots-bmi` 아래 `[W7]` 접두 이슈)로 등록하고 보고서에 요약한다.

공통 기반: `C:\Users\gamer\Documents\GitHub\dal-bots-spec\src\botkit`(sync/run/llm/discord/vod)와 `src/dalwiki_client`. 규칙 원문: `docs/W2_REBUILD_BRIEF.md`. 봇 폴더 `C:\Users\gamer\Documents\<봇>`; 옛 코드는 `legacy/`(읽지 말 것, 비교가 필요할 때만).

## A. SyncEngine 호출 정확성 (틀리면 삭제 사고)
A1. `absent="delete"`인데 `source_window`가 없거나 소스가 긁는 창보다 넓다 → 과거/창 밖 이벤트 삭제. 창 밖 보호가 맞는지 숫자로 확인.
A2. `source_complete`가 항상 True로 하드코딩됐거나, 소스 일부 실패(예외 삼킴·빈 응답·HTTP≠200)가 False로 이어지지 않는다.
A3. 같은 토픽에 두 종류 키(예: `match|`·`feed|`, `tb|`·다른 봇)가 들어가는데 `absent_filter`가 없다 → 다른 파이프라인 이벤트 삭제.
A4. 공유 토픽(리센느 4k-F0Htz1dl6, 통합 토픽들)에서 `adopt_scope`/카테고리 한정이 빠져 타 봇 이벤트를 입양.
A5. `on_missing`이 `recreate`인 곳(사람이 지운 걸 되살림) — 정당한 이유가 있는지.
A6. `engine.sync()`를 메시지/항목마다 호출(절대 금지: 매 호출이 absent 판정) — run당 1회여야 한다.
A7. Event.key에 시각·제목·달성률 등 변하는 값이 섞여 있다(변경=새 이벤트+고아).
A8. key 중복(ValueError) 가능성: 소스가 같은 id를 두 번 주는 경우 처리.

## B. 실패 경로
B1. 소스 fetch 예외를 `except: pass`/`return []`로 삼키고 정상 흐름으로 간다(alert 없음, source_complete True).
B2. LLM: `LLMUnavailable`을 잡아 커서/seen/state를 전진시키는 곳. `LLMParseError` 처리.
B3. Discord catchup: handler 예외 후 커서가 전진하는 경로, RetryQueue에 안 들어가는 경로, `on_ready`에서 close 안 되는 경로.
B4. dal.wiki 쓰기 실패(None/False 반환)를 성공으로 취급해 state/캐시에 기록.
B5. `ListFailed`를 잡아 `[]`로 바꾸는 코드(= W1 이전 패턴 복귀).
B6. 예외가 stdout에 Traceback으로 찍히고도 계속 진행(티어 RETRY 유발, 이중 쓰기).

## C. dry-run 누수
C1. `--dry-run`/`DRY_RUN`에서도 실제로 쓰는 경로(SyncEngine 밖의 직접 `delete_event`/`update_event`/requests 호출, 상태 파일 저장, 커서 저장).
C2. dry-run에서 state를 저장해 다음 실제 run이 '이미 등록됨'으로 스킵하는 경로.

## D. 시간·시즌
D1. naive datetime, KST/UTC 혼동, `.000Z` 아닌 포맷, allDay end가 23:59:59 KST가 아닌 곳, timed start==end.
D2. 연도·시즌·페이지명·리그 id 하드코딩(2026, `Capcom_Pro_Tour/2026`, gid 등)과 만료 가드/alert 유무.
D3. `today.year` 기준 롤오버가 12월~1월 경계에서 깨지는 로직.

## E. 데이터 품질 규칙
E1. summary에 `[카테고리]` 프리픽스(원제의 대괄호는 허용), 2자 미만, 100자 초과 미클립, 일정명 없는 제목.
E2. description에 출처/소스 문구, 링크에 `target="_blank" rel="noopener noreferrer"` 누락, 원본 URL이 link 필드 대신 본문에만.
E3. categoryId 누락 가능 경로(매핑 실패 시 None으로 등록).
E4. location 64자 초과 미절단.

## F. 보안·운영
F1. 시크릿 하드코딩(키·토큰·웹훅), `.env` 미ignore, 로그에 키 노출(URL 쿼리 serviceKey 등).
F2. 로그 회전 없음, 상태 파일 무한 증가, 캐시 만료 없음.
F3. run*.bat: 비ASCII, track 라벨 불일치, stdout 리다이렉트(lastrun 빈 파일).
F4. 두 bat/티어가 같은 state를 동시에 쓸 가능성(hourly+2x 등)과 락 유무.
F5. README가 코드와 다른 곳(플래그·파일명·모드).

## G. 멱등성·성능
G1. 같은 입력으로 2회 실행 시 쓰기가 생기는 경로(해시에 안 들어가는 가변 필드, 정렬 불안정, 시각 포맷 흔들림).
G2. run마다 전체 토픽 TopicCache를 여러 번 로드, 상세 페이지 전수 재조회, LLM 재질의(캐시 미사용).
G3. Liquipedia/Leaguepedia·외부 API 레이트리밋(UA, 간격) 위반 가능성.

## 출력 형식
봇마다:
```
### <봇> (<주요 파일>)
- [심각도 P0/P1/P2/P3] [항목 A1 등] 파일:라인 — 문제 — 근거(코드 1~2줄) — 제안
```
P0=삭제/중복/영구 누락 가능, P1=조용한 실패·곧 만료·시크릿, P2=품질/비용, P3=정리. 근거 없는 추측 금지, 확인 못 한 건 "미확인". 마지막에 그룹 공통 패턴 절. beads 등록은 P0~P2만(P3는 보고서에만), 제목 `[W7] <봇>: <요약>`.
