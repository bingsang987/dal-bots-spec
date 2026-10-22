# W4 실행 계획 — VOD/미러 계열 5봇 (botkit.vod 공통 모듈 + 패치)

작성 2026-10-22. 실행자는 이 문서 + `W2_REBUILD_BRIEF.md`(규칙·금지)를 따른다. 대상: `chzzkvodbot`(1,039줄) · `stellivebot`(2,206줄) · `aesthervodbot`(1,362줄) · `cluezvodbot`(1,364줄) · `streamersyncbot`(587줄). 감사 근거: audit §5.1, beads dalbots-bmi.1(close)·.11·.12·.13·.23·.29.

## 0. 원칙 — 재구축 금지, 공통 모듈 추출 + 결함 패치
이 봇들은 per-day reconciliation(날짜 단위 비교 후 PATCH로 ID 보존 = SEO 보호), 1부/2부 series 병합, 휴방/예정/갠방 시간 기준 분류, 카페 공지 블록 보존 같은 규칙을 오래 다듬어 온 코드다. **로직은 그대로 두고** (1) 복붙된 부분을 `botkit.vod` 한 벌로 뽑고 (2) 감사에서 나온 결함만 고친다. 예외: streamersyncbot은 순수 미러(메인 카테고리 → 타겟 토픽)라 SyncEngine 이식이 더 단순하다(§4).

## 1. `botkit.vod` 모듈 (dal-bots-spec `src/botkit/vod.py`, 세션 A가 만든다)
```
VOD_VIDEO_NO_RE                      # chzzk.naver.com/video/(\d+)
build_vod_block(video_no, index)     # 표준 h3 블록 1벌. rel="noopener noreferrer ugc" 고정(stellive의 nofollow 제거)
                                     #   index 규약: 0 → '치지직 VOD 링크', n≥1 → '치지직 VOD 링크-{n+1}' (chzzk/aesther/cluez 방식으로 통일;
                                     #   stellive build_vod_h3는 1-base였음 → 어댑터에서 index-1)
append_vod(description_html, video_no) -> str   # 이미 있으면 no-op, 카페 공지 블록(cafe.naver.com 포함 블록)은 보존하고 그 아래 append
extract_video_nos(description_html) -> list[str]
fetch_events_chunked(topic_id, from_ms, to_ms, chunk_days=3) -> tuple[list[dict], bool]
                                     # (events, complete). 청크 하나라도 ListFailed/예외면 complete=False. 절대 []로 삼키지 않는다.
hydrate_descriptions(events)         # events/get으로 description lazy fetch, _desc_loaded/_desc_failed 플래그. 실패면 기존값 유지
get_description(event_id) -> str|None   # 3회 백오프, 4xx 즉시 중단, None=실패(호출자는 그 이벤트 건드리지 말 것) — aesther 500-533 로직 이관
MissingGuard(conn, scope)            # Tombstone 대체. 테이블 missing_keys(scope, key, event_id, seen_missing_at).
   .sweep(mapping: dict[key→event_id], present_ids: set) -> list[key]   # 매핑된 id가 토픽(완전 조회)에 없으면 기록·반환
   .is_missing(key) -> bool          # 기록된 키는 재생성 금지
   .forget(key)                      # 소스에서 그 키가 사라지면 정리
   ※ sweep는 fetch가 complete=True일 때만 호출(불완전 조회로 '없음' 오판 금지)
safe_summary(s) -> str               # strip 후 2자 미만이면 원문(또는 '방송')으로, 100자 클립
vod_stats_line(...)                  # 선택
```
테스트 `tests/test_vod.py`: append_vod 멱등/카페 블록 보존/인덱스 규약, fetch_events_chunked 부분 실패 → complete=False, MissingGuard sweep/is_missing/forget, safe_summary.

## 2. 공통 결함 수정 규칙 (4봇 모두)
R-A. **청크 실패 = 그 범위 불완전**: `fetch_events_chunked`의 complete=False면 그 run의 prune/DAY_WIPE/orphan 생성/MissingGuard.sweep을 전부 스킵하고 `alert("[봇] dal.wiki 조회 불완전 — 삭제·생성 보류")`. PATCH(기존 매핑 갱신)는 허용.
R-B. **Tombstone 제거 → MissingGuard**: `TombstoneStore` 생성·`is_tombstoned` 호출·`tombstones` 테이블 전부 제거(테이블은 `DROP TABLE IF EXISTS`). 대신 각 run 초입에 `MissingGuard.sweep(DB 매핑, 토픽 조회 id 집합)`(complete일 때만), 생성 직전 `is_missing(key)`면 스킵+로그. 키 = 봇이 쓰는 매핑 키(chzzk: indiv_id→main_id의 indiv_id / aesther·cluez: main_id / stellive: stellight_id·main 매핑 / streamersync: src_id).
R-C. **summary 2자 가드**: 모든 create/update 직전 `safe_summary`. (chzzk `indiv_to_main_title`은 W0에서 처리됨 — 모듈로 교체.)
R-D. **PATCH 실패 시 보존**: update 실패한 기존 이벤트는 synced 집합에 넣어 prune 대상에서 제외(chzzk는 W0에서 처리됨; aesther/cluez/stellive 동일 패턴 점검).
R-E. **dal.wiki 전체 장애**: 멤버 전원 조회 실패면 `alert` 후 종료(지금은 TOTAL 0으로 정상 종료).
R-F. **멤버별 격리**: process_member를 try로 감싸 한 멤버의 HTTP 오류가 전체 run을 죽이지 않게(stellive 2165). 멤버 실패 수는 통계+alert.
R-G. **로그 회전**: run() 래퍼 또는 RotatingFileHandler(5MB×3). chzzk 로그 datefmt에 날짜 추가.
R-H. 1회성 스크립트·백업·HANDOFF 파일은 `legacy/`.

## 3. 봇별 패치 (함수/라인은 2026-10-22 기준)
### 3.1 cluezvodbot (가장 작고 최신 — 모듈 검증용 1번 타자)
- `_build_vod_block`/`_make_vod_append`(58-95) → `botkit.vod` 호출. `DalWikiClient.fetch_events`(385-408) → `fetch_events_chunked`(complete 전달). `get_event_description`(469-502) → `vod.get_description`. `_existing_desc`(978)는 유지하되 내부에서 `get_description` 사용.
- R-A: `run`(606)에서 메인/indiv/group 조회 complete 플래그를 `_process_mirror`(996)에 넘겨 DAY_WIPE·orphan 생성 스킵.
- R-B: 670-676/946 등 Tombstone 제거 → MissingGuard(scope=indiv/group, key=main_id).
- `_create_orphan_vod_event`(899) 제목 `(vod_title or "갠방")[:100]` → `safe_summary`.
- 미하루 소스 0건 지속(dalbots-24e)은 범위 밖 — alert 1줄만.
### 3.2 aesthervodbot
- cluez와 동일 치환(58-95, 416-439, 500-533, 621). 남은 cluez 역이식 3건(dalbots-agn): ① `_create_orphan_vod_event`(934) 메인 제목 '갠방 - ' prefix 제거(원제) ② orphan 생성에 `from_ms` 이전 VOD 가드 ③ orphan이 dict를 반환해 같은 방송 2부 VOD를 `past_events`에 편입(cluez 963-972 방식).
- R-A/R-B/R-C/R-F 적용. `compute_indiv_summary`(366) 이중 prefix('예정 - 방송 예정 - ') 수정(cluez 335도 동일 — 한 군데 고치고 양쪽 반영).
- hourly `sync_hourly.bat`와 2x `run.bat`가 같은 DB를 거의 동시에 씀(11:00/11:01) → sqlite `busy_timeout=10000` + 파일 락(`state/.lock`, 이미 돌고 있으면 즉시 종료 로그).
### 3.3 stellivebot
- VOD 블록 `build_vod_h3`(986, 1-base, nofollow) → `botkit.vod.build_vod_block(index-1)`; `append_vod_to_description`(997) → `append_vod`; `fetch_dalwiki_events`(351) → `fetch_events_chunked`; `fetch_event_description`/`hydrate_descriptions`(314-349) → 모듈.
- R-F: `process_member`(1359) 호출부(2165-2175) try/except + `_get`(203) 예외를 멤버 단위로.
- R-A: `process_member`/`sync_member_to_main`(1747)/`reconcile_*`(1096·1245)에서 complete=False면 CREATE·orphan 정리 스킵.
- R-B: 2158-2163/1912 Tombstone 제거 → MissingGuard(key=stellight_id, scope=indiv / key=indiv_id, scope=main).
- 제목 100자 2단계 수렴(1610 vs 906-917): 메인 제목 생성 시 indiv 저장본과 같은 절단을 적용해 1회에 수렴.
- `reconcile.py`(자체 urllib, 42-56) → dalwiki_client 사용으로 교체, `run_reconcile.bat`에 track 라벨 추가(라벨 `stellivebot-reconcile`, 내가 MONITOR에 넣는다).
- 미사용 `_is_skip_kst_date`/`db_load_main_map`/`db_upsert_main`, `_diag_*`·`_dry_purge_*`·`_purge_list_36`·`_unify_vod_format`·`HANDOFF_DESC_FIX.md`·`dedup_*.log`·`realrun_test.log` → legacy/. `group_mirror.py`(10-20 추가분)는 유지하되 모듈 함수 사용.
### 3.4 chzzkvodbot (가장 큼 — 마지막)
- `build_vod_block`(410) → 모듈, `fetch_events`(200) → `fetch_events_chunked`, `enrich_descriptions`(179) → `hydrate_descriptions`.
- R-A: `process_member`(472) 3일 청크 중 하나라도 실패면 그 멤버의 MAIN_PRUNE(856-871)·MAIN_CREATE(844) 스킵 + alert. 현재 '전부 실패해야 조기 return'을 '하나라도 실패'로.
- R-B: 955-963/1006 Tombstone 제거 → MissingGuard(scope=main, key=indiv_id).
- `_build_event_kwargs`(226) 비-allDay end=start 강제 유지(사양) — 단 사용자가 넣은 종료시각이 있으면 보존하도록 `ev.get("end")`가 start와 다르면 그대로.
- 30분 근사 매칭(732-739)이 같은 시각대 indiv 2건을 메인 1건에 겹쳐 매핑 가능 → 이미 매핑된 main_id는 후보에서 제외.
- 로그 datefmt 날짜 추가, 1회성 스크립트(`_dedup_check/_dedup_clean/_register_janatdae/_unify_vod_format/_user_actions.json/*.db.bak_*`) legacy/.

## 4. streamersyncbot — SyncEngine 이식 (M)
- 소스: 메인 v1c2BaT1zEvn의 HONEYZ 5·ACAXIA 5 카테고리 이벤트(윈도우 -7~+21일, `hydrate_descriptions`로 본문 포함). 타겟: WhPckbBXTTsv / X6gXMTQFuVIr, 카테고리 `SRC_CAT_MAP`.
- `SyncEngine(target_topic, state/streamersync.db, absent="delete", on_missing="ignore", protect_human_edits=True)`, `Event.key=src:{source event id}`, summary/description/start/end/allDay/category 그대로 미러. `sync(events, source_complete=fetch_complete, source_window=(from, to))` — 소스 조회 불완전 또는 **타겟 TopicCache 로드 실패면 SyncAborted(쓰기 0)** → 80건 CREATE 폭주·일괄 삭제 근절.
- 첫 run: 타겟 기존 이벤트를 (summary,start,category)로 adopt(그 run PATCH 금지). 마커(`inject_marker/extract_marker/data-sync-src`)·`api_patch`·수동 fuzzy 흡수 코드 제거.
- 소스에서 사라진(삭제/이동) 이벤트는 absent=delete로 자동 전파(지금은 마커 사망으로 전파 안 됨).
- hourly 티어 `run.bat` 파일명·라벨 유지.

## 5. 실행 순서·세션
- **세션 A(Opus)**: §1 모듈+테스트 → §4 streamersyncbot 이식(모듈 검증) → §3.1 cluezvodbot. dal-bots-spec 커밋·푸시 후 봇 2개 커밋·푸시.
- **세션 B(Opus, A 완료 후)**: §3.2 aesthervodbot → §3.3 stellivebot.
- **세션 C(Opus, B와 병렬 가능)**: §3.4 chzzkvodbot.
- 라이브 실행 금지 시간대: hourly 정각 ±5분(aesther·streamersync), 11:00/19:30 ±10분(2x: chzzk·stellive·aesther·cluez), 월 13:00(stellive reconcile).

## 6. 검증 (봇마다 보고서에 수치로)
1. dry-run → apply → 2회째 전건 unchanged(VOD +0, create 0, prune/wipe 0).
2. `fetch_events_chunked` 부분 실패 시뮬레이션(청크 1개 예외 주입) → prune/wipe/create 0 + alert 1줄 확인.
3. MissingGuard: 테스트 토픽 없이, dry-run으로 '매핑된 id가 토픽에 없는' 케이스 수를 보고(실제 사용자 삭제 흔적).
4. 토픽 실데이터: 개별 15(chzzk)+15(stellive)+4+1(aesther)+2+1(cluez)+2(streamersync) 토픽 완전중복 0, summary 2자 미만 0, VOD 블록 rel 통일(`nofollow` 0).
5. 로그 회전 적용, 티어 bat 무변경(파일명 유지) 확인.

## 7. 하지 말 것
- per-day reconciliation·series 병합·분류 규칙·카페 공지 보존 로직 변경 금지(모듈 호출로 바꾸되 동작 동일).
- 메인 토픽 v1c2BaT1zEvn에 대한 삭제는 기존 범위(봇이 만든 매핑 이벤트 prune) 밖으로 확장 금지.
- dal-bots-spec의 기존 함수 시그니처 변경 금지(모듈 추가만).

## 모듈 사용 예(세션 A 작성)
2026-10-22, 세션 A. `botkit.vod`(dal-bots-spec d5a752b)를 streamersyncbot(3d7dabb)·cluezvodbot(105c505)에 붙이면서 확인한 패턴. 세션 B/C는 이 절을 기준으로 삼는다.

### 공개 함수 (`from botkit import vod`)
| 이름 | 시그니처 | 메모 |
|---|---|---|
| `VOD_VIDEO_NO_RE` | `re.Pattern` | `chzzk.naver.com/video/(\d+)` |
| `build_vod_block` | `(video_no, index=0) -> str` | 0 → '치지직 VOD 링크', n≥1 → '치지직 VOD 링크-{n+1}', rel=`noopener noreferrer ugc`. cluez/aesther/chzzk 기존 출력과 바이트 단위 동일 |
| `append_vod` | `(description_html, video_no) -> str` | 같은 videoNo 있으면 원문 그대로. 기존 본문(카페 공지·이전 VOD) 뒤에 붙인다 |
| `extract_video_nos` | `(description_html) -> list[str]` | 등장 순서, 중복 제거 |
| `safe_summary` | `(s, fallback=None) -> str` | strip 후 2자 미만 → fallback(2자 이상일 때) → '방송'. 100자 하드 클립(… 없음) |
| `fetch_events_chunked` | `(topic_id, from_ms, to_ms, chunk_days=3, *, category_ids=None) -> (events, complete)` | 청크 실패(ListFailed·예외)면 complete=False, 성공 청크 결과는 돌려준다. id 중복 제거 |
| `get_description` | `(event_id, *, attempts=3, backoff=1.5) -> str \| None` | `dalwiki_client._post` 경유(X-Api-Key). 옛 aesther/cluez 는 requests+Bearer 직접 호출이었다. 4xx 즉시 중단 |
| `hydrate_descriptions` | `(events) -> int(실패 수)` | 성공: `description`·`_desc_loaded=True`·`_desc_failed=False`. 실패: **기존 description 유지**, `_desc_failed=True`, `_desc_loaded` 안 세움(재시도 가능) |
| `MissingGuard` | `(conn, scope, *, dry_run=False)` | `.sweep(mapping, present_ids) -> list[key]`, `.is_missing(key)`, `.forget(key)`, `.keys()`. 테이블 `missing_keys(scope,key,event_id,seen_missing_at)`. present 로 다시 보이면 기록 자동 해제. dry_run 이면 메모리에만 |
| `drop_tombstones` | `(conn)` | `DROP TABLE IF EXISTS tombstones` |
| `vod_stats_line` | `(bot, **counts) -> str` | 0 아닌 값만 `[bot] k=v` |

### R-A 패턴 (cluez `run`)
```python
main_events, main_ok = vod.fetch_events_chunked(MAIN_TOPIC_ID, from_ms, to_ms)
if not main_events and not main_ok:            # R-E
    alert("dal.wiki 메인 조회 전부 실패 — 이번 run 중단(쓰기 0)"); return
indiv, indiv_ok = vod.fetch_events_chunked(m.individual_topic_id, from_ms, to_ms)
if not (main_ok and indiv_ok and group_ok):
    alert("[봇] dal.wiki 조회 불완전 — 삭제·생성 보류 (main, indiv:키유 …)")
# DAY_WIPE  : main_ok and indiv_ok(그룹은 main_ok and group_ok)일 때만
# orphan 생성: allow_orphan_create and main_ok
# '매핑은 있는데 조회 결과에 없음' CREATE: indiv_ok 가 아니면 보류(INDIV_SKIP_INCOMPLETE) — 옛 코드는 여기서 중복 CREATE 했다
```
시뮬레이션(실데이터 dry-run, 청크 4번째 500 주입): 옛 cluez — 메인 실패 시 DAY_WIPE 4(2일×indiv/group), 개별/그룹 실패 시 중복 CREATE 각 2. 새 cluez — 전부 0 + alert. aesther/chzzk/stellive 도 같은 실험(옛 코드 사본 + **실 DB 사본**을 같은 폴더에 두고)으로 전후를 보고하면 된다. DB 사본 없이 돌리면 매핑이 비어 전부 wipe 로 나와 비교가 무의미하다.

### R-B 패턴 (cluez `_process_mirror`)
```python
guard = vod.MissingGuard(conn, scope=f"indiv:{m.name}", dry_run=dry_run)   # 멤버별 scope 권장
if indiv_ok:   # 완전 조회일 때만. mapping 은 '이번 윈도우 메인 id' 로 한정(윈도우 밖 매핑을 없음으로 오판 금지)
    guard.sweep({mid: iid for mid, iid in db_indiv.items() if mid in current_main_ids}, set(indiv_fetched))
if main_ok:
    for k in guard.keys():
        if k not in current_main_ids: guard.forget(k)
...
elif existing_indiv_id and (guard.is_missing(main_id) or not indiv_ok):
    skip  # INDIV_SKIP_MISSING / INDIV_SKIP_INCOMPLETE
else:
    create
```
- scope 를 `indiv`/`group` 하나로 두고 `forget` 정리를 하면 다른 멤버 키를 지운다 → `indiv:{멤버}` 처럼 나눌 것.
- DAY_WIPE 로 지운 이벤트는 매핑도 같이 지우므로 guard 와 충돌하지 않는다(그 날 재생성은 그대로).
- 옛 orphan 생성의 Tombstone 은 그냥 지우면 된다 — `vod_map`(video_no 기록)이 남아 있어 사람이 지운 orphan 은 원래도 재생성되지 않는다.
- `init_db` 끝에 `vod.drop_tombstones(conn)`.

### 함정
- `for vod in vods:` 처럼 지역 변수 `vod` 가 모듈을 가린다(cluez·aesther `_process_vod`/`_create_orphan_vod_event`). 루프 변수 이름을 바꿀 것.
- 옛 `DalWikiClient.delete_event` 가 `_delete_event()` 결과를 버리고 `True` 를 돌려줘 실패해도 매핑을 지웠다(cluez·aesther 동일 복제) → `return bool(_delete_event(id))`.
- stellive `hydrate_descriptions` 는 실패 시 `description=""` 로 덮었다. 모듈은 기존값 유지 + `_desc_failed` — 어댑터에서 `_desc_failed` 를 보고 PATCH payload 에서 description 을 빼는지 확인.
- `botkit.run.alert(msg, bot="이름")` 은 `[HEALTH-ALERT] 이름: ` 을 붙인다. 메시지에 봇 이름을 또 넣지 말 것. 콘솔 실행은 `PYTHONIOENCODING=utf-8`(bat 에 이미 있음) — 없으면 cp949 로 em dash 로그가 깨진다.
- 로그 파일명은 대시보드(`bot_dashboard/dashboard.py`·`today_writes.py`)가 본다. cluez 는 `--log-file` 그대로 + `RotatingFileHandler(5MB×3)` + `%(asctime)s` 접두(대시보드 TS_RE 가 날짜를 읽게 됨). `botkit.run()` 으로 바꾸면 `logs/<bot>.log` 로 바뀌어 대시보드 경로 수정이 필요하다.
- **SyncEngine 을 공유 토픽에 쓸 때**(streamersync): 엔진의 입양/404 복구 키가 (summary,start) 뿐이라 '휴방'(allDay 자정, 여러 멤버 공통 — 토픽당 31쌍)이 다른 멤버 이벤트를 입양한다. streamersync 는 (토픽×카테고리)별 엔진 + 카테고리 한정 TopicCache 뷰(`_CategoryEngine._load_cache` 오버라이드) + `SyncState(path, "토픽/카테고리")` 네임스페이스로 피했다. 패키지 이슈로 등록(beads).
- 첫 run 입양을 PATCH 없이 하려면 `engine.state.put(key, eid, ev.content_hash(), …)` 을 `sync()` 전에 직접 호출(내용이 같을 때만 현재 hash, 다르면 빈 hash → 다음 sync 에서 1회 PATCH). dry-run 은 state 파일 임시 사본에 대고 돌리면 입양 수치까지 정확히 나온다.
