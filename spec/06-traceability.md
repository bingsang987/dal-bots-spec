# 요구사항 ↔ 코드 추적 매트릭스

| Req ID | 기능 | 설명 | 코드 위치 |
|--------|------|------|----------|
| INT-R1 | API | POST/PATCH 응답에서 `id` 또는 `eventId` 키 모두 시도 | `runbot/dalwiki.py`, `worldcupbot/dalwiki_client.py`, `pokemonTCGbot/dalwiki_api.py` |
| INT-R2 | API | PATCH는 전체 필드 포함 (partial update 금지) | `laftelbot/dalwiki_api.py`, `megasale_bot/dalwiki_api.py` |
| INT-R3 | API | `link`/`location` 빈값이면 키 자체를 페이로드에서 제거 | `worldcupbot/dalwiki_client.py`, `megasale_bot/dalwiki_api.py` |
| INT-R4 | API | DELETE 502에 한해 1.5s × 시도 횟수 재시도 | `runbot/dalwiki.py`, `onlinegamebot/core/dalwiki_client.py` |
| INT-R5 | API | summary 97자 초과 시 "..." 자르기 | `figurebot/dalwiki.py`, `runbot/dalwiki.py` |
| INT-R6 | API | allDay 이벤트 end는 `23:59:59+09:00` | `animebot/anime_bot.py`, `laftelbot/dalwiki_api.py` |
| INT-R7 | API | 외부 소스 rate limit 준수 (Liquipedia 30s/req) | `ewcbot/liquipedia_client.py` |
| DEDUP-R1 | 중복제거 | canonical_key + content_hash 기반 변경 감지 | `runbot/main.py`, `figurebot/bot.py`, `laftelbot/scanners/diff_scanner.py` |
| DEDUP-R2 | 중복제거 | `dalwiki_id` NULL이면 PATCH 대신 POST 폴백 | `runbot/dalwiki.py`, `onlinegamebot/core/dalwiki_client.py` |
| DEDUP-R3 | 중복제거 | Dry-run 시 DB write 금지 | `runbot/main.py`, `laftelbot/main.py`, `gamingschedulebot/freegame/main.py` |
| F01-R1 | 이벤트 등록 | 새 이벤트는 POST /events | 전체 봇 `dalwiki.py` / `dalwiki_client.py` |
| F01-R2 | 이벤트 등록 | POST 응답 UUID를 DB에 저장 | 전체 봇 |
| F01-R3 | 이벤트 등록 | POST 실패 시 dalwiki_id NULL 유지 | `runbot/dalwiki.py`, `figurebot/dalwiki.py` |
| F01-R4 | 이벤트 등록 | Dry-run 모드에서 API 미호출 | `runbot/dalwiki.py`, `laftelbot/dalwiki_api.py` |
| F01-R5 | 이벤트 등록 | Dry-run 가짜 ID DB 미저장 | `runbot/main.py`, `laftelbot/main.py` |
| F02-R1 | PATCH | content_hash 변경 시에만 PATCH | `runbot/main.py`, `figurebot/bot.py` |
| F02-R2 | PATCH | PATCH 페이로드 전체 필드 포함 | `laftelbot/dalwiki_api.py`, `pokemonTCGbot/dalwiki_api.py` |
| F02-R3 | PATCH | PATCH 성공 후 DB hash 갱신 | `runbot/main.py`, `figurebot/bot.py` |
| F02-R4 | PATCH | dalwiki_id 없으면 POST 폴백 | `runbot/dalwiki.py` |
| F02-R5 | PATCH | Resync 모드에서 강제 PATCH | `runbot/main.py` (resync 서브커맨드) |
| F04-R1 | Dual Posting | 두 토픽에 각각 독립 POST/PATCH/DELETE | `onlinegamebot/core/dalwiki_client.py`, `chzzkvodbot/chzzk_vod_bot.py` |
| F04-R2 | Dual Posting | 두 UUID를 별도 컬럼으로 저장 | `onlinegamebot/core/models.py` |
| F04-R3 | Dual Posting | 한 쪽 실패가 다른 쪽 처리 중단 안 함 | `onlinegamebot/core/pipeline.py` |
| F04-R4 | Dual Posting | 캘린더별 다른 summary prefix | `onlinegamebot/core/classifier.py` |
| F05-R1 | VOD 매칭 | 메인 캘린더 이벤트 목록 먼저 조회 | `chzzkvodbot/chzzk_vod_bot.py`, `aesthervodbot/aesther_vod_bot.py` |
| F05-R3 | VOD 매칭 | 이미 매칭된 이벤트 재처리 금지 | `chzzkvodbot/chzzk_vod_bot.py` (vod_matched 컬럼) |
| F05-R4 | VOD 매칭 | 사용자 수동 입력 보호 | `aesthervodbot/aesther_vod_bot.py` |
| F06-R1 | AI 추출 | 사전 필터 후 Claude API 호출 | `convinibot/claude_extractor.py`, `megasale_bot/claude_classifier.py` |
| F06-R2 | AI 추출 | 신뢰도 낮은 결과 미등록 | `convinibot/claude_extractor.py`, `megasale_bot/claude_classifier.py` |
| F07-R1 | 스캐너 | `--mode` 플래그로 독립 실행 | `laftelbot/main.py` |
| F07-R3 | 스캐너 | expire 모드는 DELETE 대신 PATCH | `laftelbot/scanners/expire_scanner.py` |
| F08-R1 | 소스 병합 | 3개 소스 독립 수집 후 로컬 병합 | `runbot/main.py`, `runbot/merger.py` |
| F08-R4 | 소스 병합 | 소스 하나 실패 시 나머지로 계속 | `runbot/main.py` |

---

> **참고**: 이 매트릭스의 코드 위치는 역공학 시점(2026-05)의 스냅샷이다. 코드가 변경되면 추적 정보도 함께 갱신해야 한다.
