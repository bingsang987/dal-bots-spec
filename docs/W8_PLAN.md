# W8 수정 계획 — W7 리뷰 발견 180건(P0 17 · P1 55 · P2 107) 처리

작성 2026-10-22. 근거: `W7_REVIEW_botkit.md`, `W7_REVIEW_sports_commerce.md`, `W7_REVIEW_media_kpop.md`, `W7_REVIEW_vod_ops.md`. 규칙·금지는 `W2_REBUILD_BRIEF.md`. 이슈는 beads 에픽 `dalbots-bmi`의 `[W7]` 자식(번호는 아래).

## 0. 원칙
1. **패키지 먼저(W8-0)**. 네 보고서의 공통 패턴 대부분이 엔진 한 곳에서 막힌다. 봇 배치는 W8-0이 푸시된 뒤 시작하고 새 옵션을 쓴다.
2. 봇 배치는 **P0·P1 전부 필수**, P2는 같은 파일을 만지는 김에 처리하고 못 한 P2는 이슈에 "W8 보류" 코멘트. 각 이슈는 close 시 reason에 커밋 해시.
3. 검증은 W2 브리프(dry-run → 실제 → 2회째 전건 unchanged) + **부분 실패 시뮬레이션**: 소스 조각 하나를 0건/구조 깨짐으로 바꿔 돌려 `absent 삭제 0 + [HEALTH-ALERT] 1줄`을 로그로 보인다. 이번 리뷰 P0·P1의 절반이 이 케이스다.
4. 데이터 교정(이미 잘못 올라간 이벤트)은 dry-run 목록을 보고서에 남기고 적용.

## 1. W8-0 패키지 (dal-bots-spec, Opus 1세션, 테스트 필수, 기존 시그니처 유지)
| 이슈 | 변경 |
|---|---|
| .104 (P0) + .68 | `SyncEngine._lookup`: 두 scope 모두 `exclude=self._claimed`. `_delete`: absent 행의 event_id가 이번 run `_save`된 id면 dal.wiki DELETE 없이 state 행만 제거(로그 `ABSENT_RELINKED`). 테스트: 소스가 같은 (summary,start)를 새 key로 재발급 → 이벤트 생존, 옛 state 행만 제거 |
| .65 (P1) | CHANGED 분기 전에 `in_window and not has_id` 검사 → `on_missing` 정책 동일 적용(ignore면 `missing+1`, PATCH·create 없음) |
| .66 (P1) | `SyncEngine(max_absent_ratio=0.3, max_absent=None)`: absent 후보 > max(10, ratio×len(existing)) → `absent_skipped="mass_absent"`. **그리고** `absent_skipped`가 설정되거나 `deleted>0`이면 `botkit.run.alert`(가져오기 실패 시 logger.warning)로 한 줄 — 어떤 봇도 올리지 않던 신호를 엔진이 직접 낸다 |
| .67 (P1) | `run_catchup_once(..., ready_timeout=300)`: on_ready 미도달 시 close + alert + `CatchupStats.timed_out=True`, 커서 유지 |
| .69 (P2) | `catchup(..., stop_on=(LLMUnavailable, SyncAborted, ListFailed))` — 중단 예외 집합. SyncAborted/ListFailed 발생 시 커서 유지·큐 미삽입·alert |
| .70 (P2) | `_delete` 404 관용: `delete_event`가 status를 돌려주게(`delete_event_status`) 하고 404면 gone |
| .71 (P2) | `alert()` Discord 경로를 `dalwiki_client.discord_notify` 웹훅(.env `DALWIKI_DISCORD_WEBHOOK_URL`)으로 통일 |
| .117/.172/.175 (P1, 3보고서 공통) | **시크릿 마스킹**: `botkit.run.alert`·`run()` 예외 메시지·`botkit.run` 로그 포맷터에 `(api_key|serviceKey|apiKeyNm|key|token|secret)=[^&\s]+` → `=***` 마스킹. 테스트 |
| 공통 패턴(스포츠 5) | `SyncState.import_legacy(..., dry_run=None)`: DRY_RUN이면 메모리 사본에만(파일 commit 없음). 11봇 잠복 누수 제거 |
| .152 (P0) | `ingest_core.approval._register_approved`: TopicCache (summary,start,category) lookup 후 없을 때만 create, 있으면 그 id PATCH. 큐 항목이 자동 등록으로 ready 되면 `superseded` 표시 |
| VOD 공통 2 (P2 .81~) | `MissingGuard.sweep(..., verify=True)`: 새 키 기록 전 `events/get` 404 확인(200이면 기록 안 함 — 조회 창 경계 오판 방지) + 새 키 기록 시 `alert` 한 줄 |
| .72 (P2) | thinking 모델에 temperature 무시+경고 |
| .73 (P2) | `DALWIKI_DISCORD_NOTIFY=create,update,delete` 액션 필터 |

완료 기준: pytest 전부 통과(신규 테스트 ≥ 12), `docs/W8_PLAN.md` 끝에 '새 옵션 사용 예' 절 추가, 커밋·푸시, 위 이슈 close.

## 2. 봇 배치 (Opus 2세션 병렬, W8-0 이후)
### A. 스포츠 (ids)
koreafootballbot .158 .159 · koreansoccerguysbot .160 .161 · lckteambot .162 .163 · msibot .164 .165(worlds_bot botkit.run) · fgcbot .149 .166 .167 .168 .169 · stockmarketBOT .150 .170 · econcalbot .171 · housingbot .151 .172 · ipobot .173 .174 · youthpolicybot .175 · (P2: PLBOT KBObot kboticketbot lckbot 각 1)
공통 처방: "조각(월·대회·체인·상세)에 state 행이 있는데 응답 0/표 없음이면 그 조각 실패" → `source_complete=False` 또는 `absent_filter`를 '이번 run 실제 조회된 조각'으로 한정. 삭제 가드 분모는 창 안 행.
### B. 커머스 (ids)
theatergoodsbot .156 .157 .184 .185 .207(**SyncEngine 이식**, 공유 id 52건 분리 데이터 패치) · daisobot .155 .182 .183(**SyncEngine 이식**) · couponcalbot .176(+.152는 W8-0) · convinibot .153 .177 · canceltickbot .154 .180 .181 · ticketbot .179 · tumblbugbot .178 · (P2: taxcal camping megasale figurebot doujin 각 1)
인터파크(.179/.180): CloudFront 403 — 아래 §3.
### C. 미디어 (ids)
**재구축(SyncEngine)**: runbot .107 .108 .125 · gamepassbot .121 .122 .123 · gamingschedulebot .105 .106 .124(PS Plus 월 밀림 데이터 교정 포함) · moviebot .119(+.57). **패치**: animebot .112 .113 · laftelbot .114 .115 · netflixbox .116 · dramabot .118 · naehanbot .120 · onlinegamebot .126 .127 · mnetplusbot .110 .111 · festivalbot .109 · (P2: kdrama album gacha qwer collabcafe dcbot berriz·timetree 각 1)
### D. VOD + 운영 (ids)
chzzkvodbot .77 .78 .79 · stellivebot .98 .99 .100 .101~.103 · aesther·cluez .80 · 락 4봇 .8x(aesther `acquire_run_lock` 이식) · `update_or_create_event` → `update_event`(메인 쓰기) · streamersync P2 2 · 운영 P2: bot_scheduler 2·health_check 2·dalwiki-agent health 2(관제탑이 직접)

## 3. 응급(배치 전에 처리)
- runbot .107: `races.db` f1c2b26906c896b3 sources_json에서 roadrun 41642 제거(11-24 시한). 배치 C가 그 전에 끝나면 코드 수정으로 대체.
- gamingschedulebot .105: PS Plus 2026-10 라인업이 11월로 등록된 이벤트 교정(배치 C).
- 인터파크 CloudFront 403(10-21 19:3x~): 봇·브라우저 UA 모두 403 → IP/WAF 차단. 봇은 `absent_skipped`로 안전. 결정 필요: ① 대기(차단 해제 여부 매일 확인, 패트롤에 소스별 0건 추적 추가) ② 다른 경로(모바일 API·RSS) 조사 → beads .14 '죽은 소스'에 합류.

## 4. 순서
W8-0(패키지) → A+B 병렬 → C+D 병렬 → 관제탑: 패키지 diff 리뷰, 전 토픽 패트롤 재실행, W7 이슈 전수 close 확인, 계획서·메모리 갱신.
