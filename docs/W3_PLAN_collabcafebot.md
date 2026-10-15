# W3 실행 계획 — collabcafebot (botkit 이식)

작성 2026-10-16. 실행자는 이 문서 + `W2_REBUILD_BRIEF.md`(규칙·검증·금지)를 따른다. 코드 위치 `C:\Users\gamer\Documents\collabcafebot\popup_bot.py`(1,745줄). 감사 근거: audit §5.2, beads dalbots-bmi.25 / .10 / .14.

## 0. 이 봇이 하는 일 (바뀌지 않는 것)
Discord 입력채널(TweetShift 트윗) + 웹 2소스(팝가 `popga.co.kr`, 데이포유 `dayforyou.com`) → Haiku가 서브컬쳐 팝업/콜라보카페/굿즈 일정을 JSON으로 추출(`CLASSIFY_SYSTEM`, 출력 필드: category, title, venue, ip_name, ip_id, venue_id, start, end, description, reservation_url, official_url, official_sns_urls, confidence) → 토픽 `wyJcQePq9IFq`(카테고리 `CATEGORY_DALWIKI_NAME` 3종)에 등록. 같은 행사가 같은 체인의 여러 지점에서 열리면 1개 이벤트에 지점을 누적(`fair_dedupe_key`, `_merged_title`, `_merged_location`). confidence<0.7은 승인 큐(ingest_core.approval).

## 1. 그대로 가져가는 함수 (복사, 내용 유지)
`_passes_preclassify`/`PRECLASSIFY_KEYWORDS`, `CLASSIFY_SYSTEM`(프롬프트 본문), `to_iso_kst`, `DENY_RES_URL_HOSTS`/`_sanitize_res_url`, `_link_p`, `_embed_html`, `build_event_payloads`(§3에서 Event 빌더로 감싼다), `_summary_core`, `_get_suffix`, `_BRANCH_TOKENS`, `_venue_chain`, `fair_dedupe_key`, `_branch_label`, `_strip_branch_from_title`, `_IPSIG_STOP`(§4에서 확장), `_same_ip_signal`, `_merged_title`, `_merged_location`, `slug_dedupe_key`, `_is_period_overlap`, `extract_tweet_id_*`, `fetch_tweet_via_fxtwitter`, `collect_message_assets`, `_make_review_callback`(승인 큐 연동 부분), `fetch_url`, `fetch_url_limited`, `_strip_html`, `web_fetch_popga`, `_fetch_dayforyou_one`/`web_fetch_dayforyou`(§5 수정), `WEB_SOURCE_URL`(현대백화점 항목 삭제).

## 2. 제거하는 것
- 자체 dal.wiki 클라이언트: `get_topic_id`(topic id는 .env `DALWIKI_TOPIC_ID` 고정), `fetch_existing_events`, `post_event`, `patch_event`, `DALWIKI_HEADERS`, aiohttp 호출 전부.
- `web_fetch_hyundai`, `cli_fetch_hyundai`, `fetch-hyundai` 서브커맨드, `WEB_SOURCE_URL["현대백화점"]` (첫날부터 전 월 404, dalbots-bmi.14).
- `DAYFORYOU_KEYWORDS`의 `"팝업"` (매일 HTTP 500).
- `daemon` 모드, `scheduler_loop`, `on_message`, `SWEEP_MODE` 분기 — **sweep 단일 모드**만 남긴다(티어가 `run.bat`→`python popup_bot.py sweep`로 호출하므로 진입점 이름은 유지).
- `is_duplicate_haiku`와 `find_existing_match` 3·4단계(Haiku fuzzy) — §4 참조.
- `popup_db.json` 직접 읽기/쓰기(`load_db/save_db`, `dedupe_key` legacy 키, `find_slug_match_in_db`) → SyncState로 대체(§3).
- `MODEL_HAIKU = "claude-haiku-4-5-20251001"` → `from botkit.llm import HAIKU`.
- `DRY_RUN = os.environ.get("DRY_RUN","1")` 기본 1 → botkit.run의 `--dry-run` 표준으로.

## 3. 상태·쓰기: SyncEngine 설계
- `engine = SyncEngine(TOPIC_ID, BOT_DIR/"state.db", absent="keep", on_missing="ignore", protect_human_edits=True, cache_days_back=60, cache_days_forward=400)`.
  - `absent="keep"`: 트윗/웹 스냅샷은 "전체 목록"이 아니라 소멸 판정 불가. 삭제는 하지 않는다.
  - `protect_human_edits=True`: 사람이 손본 이벤트는 PATCH 금지(제목 flapping·덮어쓰기 근절).
- **Event.key 규칙** (우선순위):
  1. `slug||{ip_id}|{venue_id}|{start}|{suffix}` — ip_id·venue_id 둘 다 있을 때.
  2. 없으면 `legacy||{venue}|{start}|{summary_core}`.
  - 멀티지점 머지: `fair||{ip_id}|{chain}|{start}|{suffix}`가 **대표 키**가 된다. 같은 fair 키의 지점들은 Event 1개로 합쳐 `summary=_merged_title`, `location=_merged_location`. 지점 목록은 sqlite 보조 테이블 `branches(fair_key, venue, first_seen)`에 저장. 따라서 run마다 같은 fair 키 → branches 합집합 → 같은 내용이면 해시 동일 → UNCHANGED.
  - 실행 흐름: 한 run에서 모은 모든 소스의 후보를 (fair 키 또는 slug/legacy 키)로 **그룹화해 Event 목록을 만든 뒤** `engine.sync(events, source_complete=False)` **1회** 호출. 메시지마다 쓰지 않는다. (`source_complete=False` + `absent="keep"`이라 삭제 0 보장.)
- **상태 이관**(1회, `migrate_state.py`): `popup_db.json`의 `slug||*`·`fair||*` 엔트리에서 `(key, event_id)`를 `SyncState.import_legacy`로 옮긴다(legacy 8,829개 중 slug/fair 키가 없는 것은 `legacy||` 키로 변환해 옮기되 **end < 오늘-30일인 과거 건은 버린다**). branches/base는 보조 테이블로. 이관 후 `popup_db.json`은 `legacy/`로 이동.
- 첫 dry-run에서 create 예정이 많으면(기존 이벤트를 못 찾는 것) 상태 이관이 잘못된 것이다 → 멈추고 원인 확인(브리프 10% 규칙).

## 4. dal.wiki 기존 이벤트 매칭(중복 방지) — 결정론 우선
state에 키가 없을 때만 실행. 후보는 `engine.cache`(TopicCache, (summary,start)→id)가 아니라 **같은 날짜 ±2일 창의 토픽 이벤트**가 필요하므로 `list_events(TOPIC_ID, from_, to_)` 1회로 가져와 run 내 캐시.
1. venue 정확 일치 + `_summary_core` 정확 일치 → 채택.
2. venue 정확 일치 + ip_name 토큰이 양쪽 summary에 있고 suffix 동일 → 채택.
3. (선택) Haiku fuzzy는 **ip_id와 venue_id가 모두 있고 후보 ≤5일 때만** `llm_json(HAIKU)` 1회. 그 외 호출 금지.
- 채택 시 **그 run에서는 PATCH하지 않는다**: `engine.state.put(key, existing_id, content_hash=Event.content_hash(), ...)`로 매핑만 기록(=adopt). 다음 run부터 실제 변경만 PATCH. 이것이 "매일 재PATCH·제목 flapping" 재발 방지의 핵심.
- IP 하드가드 `_same_ip_signal`: `_IPSIG_STOP`에 체인·매장명 토큰 추가 — `투니크`, `애니플러스`, `SMG`, `롯데월드`, `롯데월드몰`, `현대백화점`, `더현대`, `무신사`, `오브젝트`, `위버스샵`, `올리브영`, `다이소`, `홈플러스`, `이마트`, `CGV`, `메가박스`, `롯데시네마`, `유니버스`, `온라인`, `공식`, `스토어`, `샵`. 신호 없으면 매칭 거부(별도 이벤트).

## 5. 웹 소스 비용 절감 (매일 재분류 중단)
- sqlite 테이블 `web_cache(source, item_key, text_hash, classified_at, result_json)`.
- 팝가: `popup_id`별로 상세 텍스트 해시. 해시 같으면 LLM 호출 없이 `result_json` 재사용. 바뀐 항목만 **개별 호출**(지금은 20개 합쳐 1회 → 항목별 분리로 max_tokens 잘림 해소).
- 데이포유: `getScheduleList` 응답을 항목 단위로 분해한다(HTML 구조를 먼저 확인: 행/카드 단위 태그를 찾아 쪼개고, 못 쪼개면 6,000자 청크). 항목 해시로 동일 캐시. 키워드 3개(`콜라보카페`, `캐릭터`, `굿즈`)만.
- 결과: 2회째 run의 LLM 호출 수가 0(또는 신규 항목 수)이어야 한다 — 검증 항목.
- 소스 fetch 실패(HTTP≠200, 예외)는 `alert("[collabcafebot] {source} 수집 실패: …")`.

## 6. Discord 경로
- `run_catchup_once(token, [CHANNEL_INPUT], CursorStore(BOT_DIR/"state"/"cursors.json"), handler, retry_queue=RetryQueue(...))`.
- handler(message): 기존 `process_message` 본문에서 **등록 호출을 빼고** 후보 이벤트 목록을 반환(또는 run 단위 리스트에 append). `_passes_preclassify` 미통과·분류 결과 없음 = 정상 처리(커서 전진). `LLMUnavailable` → 예외 그대로 던져 재시도 큐.
- 커서 이관: `popup_last_message.json`의 `{channel_id: message_id}` → `CursorStore.import_legacy`.
- fxtwitter 보강·이미지: `image_block`에 바이트를 넘긴다(Discord CDN URL은 만료되므로 catchup 시 바로 다운로드).
- Claude: `llm_json(prompt, system=CLASSIFY_SYSTEM, images=[...], model=HAIKU, max_tokens=4000)`. 결과가 list가 아니면 `LLMParseError` → 그 메시지만 실패(재시도 큐).

## 7. 승인 큐·알림
- confidence<0.7 → 기존대로 `ingest_core.approval.enqueue`(APPROVAL_ENABLED일 때) 또는 review 채널 알림. 등록 결과 요약은 CHANNEL_LOG로 1메시지(run당 1회, 건별 아님).

## 8. 파일 구성(목표 ~700줄 이하)
```
popup_bot.py        진입점: run("collabcafebot", main) / main(ctx): sweep
classify.py         CLASSIFY_SYSTEM + classify(text, images) (llm_json)
events.py           build_event(ev) → Event, 머지(fair) 그룹화, 매칭(§4)
sources_web.py      popga / dayforyou + web_cache
sources_discord.py  handler + 커서 이관
migrate_state.py    popup_db.json → state.db (1회)
legacy/             옛 popup_bot.py, daemon.bat, fetch-*.bat, cleanup_dups_*.py, merge_split_migration.py, 백업 JSON·out_*.json·err_*.log·verify*.txt 전부
```
`run.bat` 파일명·내용(track 라벨 `collabcafebot`, `python popup_bot.py sweep`) 유지. README 재작성.

## 9. 검증 체크리스트 (보고서에 수치로)
1. 상태 이관: import_legacy 건수, state 행 수, legacy 중 버린 과거 건수.
2. `python popup_bot.py sweep --dry-run`: create/update/unchanged 수 — create가 소스 신규분 외에 다수면 중단.
3. apply 1회 → 통계. **2회째 즉시 실행: UNCHANGED 전건 + LLM 호출 0** (web_cache 효과).
4. 토픽 `wyJcQePq9IFq` 최근 -30~+90일: (summary,start,cat) 완전중복 0, `[`로 시작하는 summary 0.
5. 이전에 flapping 났던 이벤트(64Pxa_6-oRt0, i42dT5oRevO4) 제목이 더 이상 바뀌지 않는지 2회 run 사이 비교.
6. 커서: 1회차 후 cursors.json의 message_id가 채널 최신 메시지인지, 2회째 run에서 "미처리 0".
7. 티어 bat 변경 불필요 확인(파일명 유지).

## 10. 하지 말 것
- Haiku로 "중복 여부" 판정을 기본 경로에 두지 말 것(§4 3단계 예외만).
- 매칭된 기존 이벤트의 제목·본문을 그 run에서 덮어쓰지 말 것(adopt 후 다음 run부터).
- 소멸 삭제(absent=delete) 금지 — 이 봇은 스냅샷 소스가 아니다.
- dal-bots-spec 패키지 수정 금지(필요하면 보고서에 적고 beads 등록).
