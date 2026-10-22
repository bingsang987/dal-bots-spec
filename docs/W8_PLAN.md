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
(+ .213 canceltickbot 취소마감: 인터파크 playSeq/잔여석/랭킹 API CloudFront 403 → NOL 쪽 회차·랭킹 소스 조사(브라우저 네트워크 확인 가능), 없으면 취소마감 기능 보류 결정 보고)
chzzkvodbot .77 .78 .79 · stellivebot .98 .99 .100 .101~.103 · aesther·cluez .80 · 락 4봇 .8x(aesther `acquire_run_lock` 이식) · `update_or_create_event` → `update_event`(메인 쓰기) · streamersync P2 2 · 운영 P2: bot_scheduler 2·health_check 2·dalwiki-agent health 2(관제탑이 직접)

## 3. 응급(배치 전에 처리)
- runbot .107: `races.db` f1c2b26906c896b3 sources_json에서 roadrun 41642 제거(11-24 시한). 배치 C가 그 전에 끝나면 코드 수정으로 대체.
- gamingschedulebot .105: PS Plus 2026-10 라인업이 11월로 등록된 이벤트 교정(배치 C).
- 인터파크(.179/.180, 10-21 19:3x~): **차단이 아니라 소스 이전.** `tickets.interpark.com/api/open-notice/notice-list`가 200+빈 body(폐기), 공지 페이지는 `https://nol.yanolja.com/ticket/display/upcoming`(NOL 티켓)로 리다이렉트. 새 페이지는 SSR HTML에 목록이 들어 있고(상품 링크 `/ticket/products/<id>`, RSC payload `self.__next_f.push` 28청크에 'upcoming-list' 데이터), 별도 JSON API 호출 없음(브라우저 네트워크 확인). 배치 B: `scraper_interpark.py`(ticketbot·canceltickbot 공유 사본)를 NOL SSR/RSC 파서로 교체, 장르·지역 필터와 페이지네이션은 쿼리 확인, 상세는 `/ticket/products/<id>`. 봇은 그동안 `absent_skipped`로 안전.
## 4. 순서
W8-0(패키지) → A+B 병렬 → C+D 병렬 → 관제탑: 패키지 diff 리뷰, 전 토픽 패트롤 재실행, W7 이슈 전수 close 확인, 계획서·메모리 갱신.

## 5. 새 옵션 사용 예·동작 변화(W8-0 작성)

W8-0 결과(2026-10-22). 패키지는 editable 설치라 **푸시 전에 이미 전 봇의 다음 run부터 적용**된다. 기존 호출 `SyncEngine(topic, path, absent=, ...)`, `engine.sync(events, source_complete=, source_window=, absent_filter=)`, `run_catchup_once(token, ids, cursors, handler, retry_queue=)`는 시그니처가 그대로다. 아래 "동작 변화"는 기본값만으로 봇 동작이 달라지는 항목이다.

### 기본값만으로 동작이 바뀌는 것
| 항목 | 전 | 후 | 봇이 볼 것 |
|---|---|---|---|
| .104/.68 입양 | topic scope는 다른 key가 쥔 id도 입양 → 같은 run absent가 그 id DELETE | 두 scope 모두 **이번 run 소스에 있는 key가 쥔 id는 입양 안 함**. 소스에서 사라진 key의 id는 입양 가능(재발급) — 입양 즉시 옛 key의 state 행 제거(`ADOPT_RELINKED`, absent=keep·창 밖·필터 제외여도). absent 단계에서 만나면 `ABSENT_RELINKED` | 로그 `ABSENT_RELINKED`, `stats.relinked`. 같은 (summary,start)가 소스에 두 key로 동시에 있으면 이제 두 번째는 **새로 create**(예전엔 같은 id 공유) |
| .65 CHANGED+사람 삭제 | 404 → create로 되살림 | 같은 일정이 다른 id로 살아 있으면 그 id로 복구 PATCH(`recovered`), 없으면 `on_missing`(기본 ignore: `missing+1`, 쓰기 없음) | 사람이 지운 일정이 소스 변경으로 부활하지 않음. 되살려야 하는 봇은 `on_missing="recreate"` |
| .66 대량 absent | 가드 없음 | absent 후보 > `max(10, 0.3 × 범위 행)` → `absent_skipped="mass_absent"`, 삭제 0. 범위 행 = `source_window`·`absent_filter` 통과 state 행 | 봇 자체 삭제 가드와 중복돼도 무해. 소스가 정상적으로 30% 넘게 바뀌는 봇(시즌 교체 등)은 `max_absent_ratio=` 상향 또는 `max_absent=N` |
| .66 alert | 아무도 안 올림 | `absent_skipped`(source=0·source_incomplete·mass_absent)일 때만 엔진이 `[HEALTH-ALERT] sync[토픽] absent N건 처리 보류(사유)` 한 줄. **정상 삭제는 alert 없음**(관제탑 결정 2026-10-22, stats 로그로 충분) | 봇은 추가 처리 불필요. 끄려면 `alert_absent=False` |
| .67 catchup 타임아웃 | on_ready 안 오면 무한 블록 | 300초 후 close + alert + `CatchupStats.timed_out=True`, 커서·큐 그대로 | 반환 stats의 `timed_out` 확인 |
| .69 stop_on | handler의 `SyncAborted`/`ListFailed`는 메시지 실패 → 재시도 큐 + 커서 전진 | run 중단, 커서 유지, 큐 미삽입, alert | `stats.stopped_by`. 구 동작이 필요하면 `stop_on=()`(LLMUnavailable은 항상 중단) |
| .70 delete 404 | start 없는 이관 행은 매 run `delete 실패` | 404 = gone(state 제거, `deleted+1`) | 이관 잔재가 한 번에 정리되며 `absent 삭제 N건` alert가 1회 나올 수 있음 |
| .71 alert Discord | `ingest_core.discord` | `dalwiki_client.discord_notify.send_alert`(같은 `DALWIKI_DISCORD_WEBHOOK_URL`) | 없음 |
| 시크릿 마스킹 | 원문 | alert·`run()` 예외 메시지·traceback·run()이 붙인 로그 핸들러에서 `api_key|apiKey|serviceKey|apiKeyNm|token|secret|?key|&key = 값` → `=***` | 봇이 `logging.basicConfig`로 자기 콘솔 핸들러를 먼저 붙였으면 그 핸들러는 마스킹 안 됨(run()은 기존 콘솔 핸들러가 있으면 새로 안 붙임). 맨 `key=`는 URL 쿼리일 때만 마스킹 |
| import_legacy | DRY_RUN에서도 파일 commit | DRY_RUN이면 메모리 사본(`SyncState.to_memory`)에만 | 같은 프로세스의 dry-run sync는 이관 행을 보고, 파일은 불변 |
| .152 승인 등록 | dedup 없이 create | 토픽에 같은 (summary,start,category)가 있으면 그 id PATCH, 조회 실패면 등록 보류(posted 유지) | 상시 승인 러너(`_approval/run_persistent.py`)는 **재시작해야** 반영 |
| MissingGuard | 없으면 바로 영구 기록, 무음 | 새 기록 전 `events/get`: 404만 기록, 200이면 기록 안 함, 네트워크·5xx는 이번 run 메모리만. 새 기록 시 alert 한 줄 | 새로 없어진 키마다 GET 1회. 이미 기록된(오판일 수 있는) 키는 재확인하지 않음 — 배치 D가 필요하면 정리 |
| .72 temperature | thinking 모델에 넘기면 400 크래시 | ≠1이면 무시 + 경고 | 없음(현재 넘기는 봇 0) |
| .73 쓰기 알림 | 전부 | `DALWIKI_DISCORD_NOTIFY` 미설정이면 전부(동일) | 없음 |

### 새 옵션 사용 예
```python
from botkit.sync import SyncEngine

# 대량 소멸 가드 조정 / 끄기, 엔진 alert 끄기
engine = SyncEngine(TOPIC, "state.db", absent="delete",
                    max_absent_ratio=0.5,      # 기본 0.3, None 이면 비율 가드 끔
                    max_absent=None,           # 정수면 비율 대신 절대 한도
                    alert_absent=True)         # False 면 absent 보류/삭제 alert 안 냄
st = engine.sync(events, source_complete=ok, source_window=(lo, hi))
if st.absent_skipped == "mass_absent":
    ...                                        # 엔진이 이미 alert 했음 — 봇은 추가 처리만
log.info(st.summary())                         # relinked=N, absent_skipped(mass_absent) 포함

# 이관: dry-run 에선 파일에 안 남음(기본 dry_run=None → DRY_RUN 판정)
engine.state.import_legacy(rows, summary_start=ss)
```
```python
from botkit.discord import run_catchup_once, DEFAULT_STOP_ON
from botkit.llm import LLMUnavailable

stats = run_catchup_once(TOKEN, CHANNELS, cursors, handle, retry_queue=rq,
                         ready_timeout=300,                 # None 이면 무한(구 동작)
                         stop_on=DEFAULT_STOP_ON)           # (LLMUnavailable, SyncAborted, ListFailed)
if stats.timed_out or stats.stopped_by:
    return                                                  # 커서 유지됨, alert 이미 나감
```
```python
from botkit.run import alert, mask_secrets
alert(f"API 실패: {e}")                 # serviceKey=… 자동 마스킹
log.warning(mask_secrets(str(e)))       # run() 밖 자체 핸들러로 찍을 때

from dalwiki_client import delete_event_status
ok, status = delete_event_status(eid)   # 404 를 실패와 구분

from ingest_core import approval
approval.supersede("couponcal", dedup_key)   # 자동 등록(ready)된 묶음의 대기 큐 항목 회수(.152, couponcalbot이 호출)

from botkit import vod
gone = guard.sweep(mapping, present_ids)              # verify=True 기본
gone = guard.sweep(mapping, present_ids, verify=False)  # 이미 자체 get 확인을 하는 봇(stellive 328-338)
```
```
# .env — 쓰기 알림 소음 줄이기(.73)
DALWIKI_DISCORD_NOTIFY=create,delete     # 미설정=전부, none=끔(HEALTH-ALERT 는 계속 감)
```

### 봇 쪽에서 바꿔야 할 호출
- couponcalbot(배치 B, .176과 함께): 묶음 K가 ready로 자동 등록될 때 `approval.supersede(bot, K)` 호출. 안 해도 승인 시 중복 create는 이제 안 나지만, 카드가 #승인필요에 남는다.
- stellivebot(배치 D): 이미 `present | alive`로 자체 확인 중 → `sweep(..., verify=False)`로 GET 중복 제거(선택).
- 자체 absent 비율 가드를 가진 봇(PLBOT·KBObot·ipobot·econcal·housing·tumblbug·coupon·lckbot·lckteambot): 엔진 가드와 겹친다. 분모가 틀린 봇 가드(lck 계열)는 지우고 엔진 가드에 맡겨도 된다.
- 필수 변경은 없다(기존 호출 그대로 동작).
