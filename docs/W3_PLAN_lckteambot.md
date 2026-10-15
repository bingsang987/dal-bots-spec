# W3 실행 계획 — lckteambot (botkit 이식)

작성 2026-10-16. 실행자는 이 문서 + `W2_REBUILD_BRIEF.md`(규칙·검증·금지)를 따른다. 코드 `C:\Users\gamer\Documents\lckteambot\lck_team_bot.py`(1,133줄). 감사 근거: audit §5.1, beads dalbots-bmi.30 / .10 / .15. 참고 구현: `C:\Users\gamer\Documents\lckbot\bot.py`(W2에서 재구축된 LCK 봇 — Leaguepedia 시즌 페이지 자동 탐색·MatchId 키·SyncEngine 사용 예시, **그대로 재사용**).

## 0. 이 봇이 하는 일 (바뀌지 않는 것)
두 파이프라인이 팀 토픽 10개(`TEAMS[*].topic_id`) + 통합 토픽 `BzGUT0wOWsgx`에 쓴다.
- (A) 경기: Leaguepedia Cargo `MatchSchedule` → 팀 토픽(해당 팀 경기만) + 통합 토픽(전 경기). 카테고리 = 대회 단계(`TEAM_MATCH_CAT_IDS[topic][단계]`, 통합은 `UNIFIED_LCK_CAT_IDS[팀태그]`).
- (B) 피드: 팀별 Discord 채널(`CHANNEL_TEAM_MAP`, TweetShift) → 스트리밍 스케줄 이미지(비전) / 유튜브 알림 / 팬미팅·이벤트·굿즈(텍스트 분류) → 팀 토픽(`TEAM_FEED_CAT_IDS`) + 통합 토픽.
- 규칙(메모리): 통합 토픽 summary에 팀 태그 프리픽스 금지(카테고리 칩이 팀). 팀 토픽 summary도 프리픽스 없음.

## 1. 그대로 가져가는 것
`TEAMS`(10팀 설정), `CHANNEL_TEAM_MAP`, `TEAM_MATCH_CAT_IDS`, `TEAM_FEED_CAT_IDS`, `UNIFIED_LCK_CAT_IDS`, `UNIFIED_TOPIC_ID`, `NOTIFY_CHANNEL_ID`, `utc_str_to_iso`/`utc_str_to_end_iso`(best_of 기반 종료시각), `make_match_summary`/`make_unified_summary`(단, 태그 프리픽스 없는지 재확인), `build_tweet_embed_desc`, `_is_youtube_url`, `_collect_image_urls`, `_extract_tweet_id`, `_extract_tweet_text`, 비전 프롬프트와 분류 프롬프트 본문(`parse_schedule_image`, `classify_tweet` 안의 문자열), 스케줄 키워드·분류 키워드 목록.

## 2. 제거하는 것
- `OVERVIEW_PAGES` 하드코딩(2026) → lckbot/bot.py의 시즌 페이지 자동 탐색 함수 복사(대회명 패턴으로 `Tournaments`/`MatchSchedule` 조회, 올해+내년 1월). 못 찾으면 `alert`.
- 자체 dal.wiki 함수 `post_event/patch_event/delete_event/smart_patch/cleanup_tbd_for_match/post_team_match/post_unified_match/_feed_dup_exists/_feed_sig`, TopicCache 수동 로드 → SyncEngine.
- `lck_team_db.json`/`last_seen.json`/`tweet_buffer.json` 직접 입출력 → SyncState + CursorStore(§4·§5 이관).
- `Anthropic()` 직접 호출, `claude-sonnet-4-6`, `image/jpeg` 고정, 코드펜스 제거 regex → `llm_json`/`image_block`.
- 데몬 경로(`leaguepedia_poll_loop`, `on_message`, `run_bot.bat`) → legacy. `--once`가 유일한 모드(bat 유지).
- `make_match_key`(dt_utc 포함) → MatchId 키.

## 3. 파이프라인 A — 경기 (SyncEngine ×11)
- 소스: `fetch_matches(overview_pages)` — Cargo 필드에 `MatchId` 추가(`Team1,Team2,DateTime_UTC,Winner,BestOf,Team1Score,Team2Score,OverviewPage,Tab,MatchId`). UA 헤더 필수(`lol.fandom.com` 차단 방지), run당 Cargo 요청 ≤ 페이지 수(≤6), 429/403이면 즉시 중단 + alert, 그 run은 `source_complete=False`.
- Event.key = `match|{MatchId}` (MatchId 없으면 `match|{OverviewPage}|{Team1}|{Team2}|{Tab}|{N}` — 시각 미포함).
- 팀 토픽 Event: summary `make_match_summary`(스코어 포함), start/end, category_id=`TEAM_MATCH_CAT_IDS[topic][단계]`(단계는 Tab/OverviewPage에서 매핑; 매핑 실패 시 그 토픽의 기본 '경기' 카테고리 — 없으면 alert하고 등록 보류), link=Leaguepedia 매치 페이지.
- 통합 토픽 Event: 같은 key, summary 동일(태그 없음), category_id=`UNIFIED_LCK_CAT_IDS[tag]`(홈/원정 중 **첫 번째로 매칭된 팀** 태그 — 기존 동작 유지).
- 엔진: 토픽별 `SyncEngine(topic, state.db, absent="delete", on_missing="ignore")` 11개(같은 state.db 파일, SyncState(path, topic_id)가 토픽별로 행을 구분). `engine.sync(match_events, source_complete=cargo_ok, source_window=(now-60d, now+180d), absent_filter=lambda row: row.key.startswith("match|"))`.
  - **주의**: 같은 토픽에 피드 이벤트(`feed|…` 키)도 같은 state에 들어가므로 `absent_filter`로 `match|` 키만 소멸 후보로 제한해야 한다. 이게 없으면 경기 동기화가 피드 이벤트를 전부 지운다.
- 스코어 확정·일정 변경은 해시 차이로 자동 PATCH. TBD 플레이스홀더 청소는 absent=delete가 대신한다.
- 비시즌(0경기): info 로그, alert 아님.

## 4. 파이프라인 B — 피드 (SyncEngine ×11, absent=keep)
- `run_catchup_once(DISCORD_TOKEN, CHANNEL_TEAM_MAP.keys(), CursorStore(state/cursors.json), handler, retry_queue=RetryQueue(state/retry.json))`.
- 커서 이관(1회): `last_seen.json`은 채널별 ISO 시각이다. 첫 실행 시 채널마다 `history(limit=1, before=<that time>)`로 그 시각 이전 마지막 메시지 id를 구해 `CursorStore.set`. 이관 실패 채널은 "최근 50개"에서 시작하되 **dedup은 key로** 보장.
- handler(team, message) → `list[Event]` 반환(쓰기 금지). 분기:
  - 스케줄 이미지(키워드+이미지): `llm_json(prompt, images=[bytes], model=SONNET, max_tokens=1000)`; 이미지는 UA 붙여 다운로드한 바이트를 `image_block`에(media_type 자동, 리사이즈). key=`feed|{tweet_id}|stream|{date_iso}`. summary=멤버 목록(기존). 통합 summary `갠방 - {멤버}`.
  - 유튜브: key=`feed|{tweet_id}|youtube`. summary=`clean_title(첫 줄)`.
  - 팬미팅/이벤트/굿즈: 키워드 프리필터 후 `llm_json(prompt, model=HAIKU, max_tokens=300)`; null이면 정상 스킵. key=`feed|{tweet_id}|{cat}`. summary=`clean_title(title)`, description=`build_tweet_embed_desc`.
  - `LLMUnavailable` → 예외 전파(커서 정지, 재시도 큐). `LLMParseError` → 그 메시지만 실패 기록.
- 모든 피드 Event는 팀 토픽과 통합 토픽 두 엔진에 같은 key로 넣고 `sync(events, source_complete=False)`(absent="keep"이라 삭제 0).

## 5. summary 오염 교정 (`clean_title`)
```
def clean_title(s):  # 적용 전후 예시를 테스트로 고정
    s = re.sub(r"\[([^\]]*)\]\(<?https?://[^)>]*>?\)", r"\1", s)   # [#LCK](<https://…>) → #LCK
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"#\S+", "", s)                                      # 해시태그 제거
    s = re.sub(r"^[\s\W_]+", "", s)                                 # 선행 이모지·기호
    s = re.sub(r"\s{2,}", " ", s).strip(" -|·")
    return s[:60] or "일정"
```
- 기존 오염 이벤트 교정: 이관된 state의 피드 항목 중 summary가 `[`·`http`·`#`을 포함하는 것을 골라 `clean_title` 적용 → 해시 차이로 첫 apply에서 PATCH. 감사 샘플(농심 `[📺YOUTUBE] [#LCK](<https://…`, DK `[𝟮𝟬𝟮𝟲 [#𝙒𝙎𝘾𝙄]…`, 브리온 `[𝗬𝗢𝗨𝗧𝗨𝗕𝗘 | …]`, FEARX `[🌕추석 특집] …`)이 전부 깨끗해져야 한다. 수학 알파벳 볼드(𝗬𝗢𝗨…)는 `unicodedata.normalize("NFKC")`로 일반 문자로.

## 6. 상태 이관 (`migrate_state.py`, 1회)
- `lck_team_db.json`(1,057건, **cp949로 깨진 category 문자열 포함** → 파일을 utf-8로 읽고 깨진 값은 무시):
  - `match||…` 항목: `team_event_id`→팀 토픽 state, `unified_event_id`→통합 state. 키는 MatchId를 모르므로 **(summary,start)로 TopicCache adopt**가 더 안전: 이관은 id만 보관하고, 첫 run에서 Cargo 매치와 (팀1,팀2,start) 매칭으로 key를 붙인다(lckbot/bot.py의 adopt 로직 참조). v1 UUID(36자) id는 토픽에 없을 수 있으므로 `get_event`로 존재 확인 후 없으면 버림.
  - `feed` 항목(`type` in stream/youtube/팬미팅…): key=`feed|{tweet_id}|{type}[|date]`로 변환해 import_legacy.
- `last_seen.json` → §4 커서 이관.

## 7. KT 롤스터 토픽 0건 원인 조사 (필수 보고)
토픽 `WbNGBG3iFUAV`에 -30~+180일 이벤트가 0건(다른 9팀은 있음). 확인 순서: (1) `TEAMS["KT Rolster"]`의 topic_id·discord_channel·leaguepedia_name이 맞는지, (2) `TEAM_MATCH_CAT_IDS`/`TEAM_FEED_CAT_IDS`에 그 topic 키가 있는지(없으면 category_id ''→등록 실패), (3) 로그에서 KT 관련 POST 실패 흔적, (4) Discord 채널 권한. 원인과 수정 내용을 보고서에.

## 8. 파일 구성
```
lck_team_bot.py   진입점 run("lckteambot", main): A → B 순서로 1회 실행 (--once 호환)
matches.py        Cargo 조회·시즌 탐색(lckbot 재사용)·Event 빌더
feed.py           Discord handler·llm 호출·clean_title
config.py         TEAMS·카테고리 맵·토픽 id
migrate_state.py
legacy/           옛 lck_team_bot.py, run_bot.bat, cleanup_*.py, cleanup_orphans.json, tweet_buffer.json, 백업 json
```
`run_once.bat` 파일명·내용(track 라벨 `lckteambot`, `--once`) 유지. README 재작성(두 파이프라인, 키 규칙, 카테고리 맵, Liquipedia 제한).

## 9. 검증 체크리스트 (보고서에 수치로)
1. 시즌 페이지 자동 탐색 결과(페이지명 목록)와 Cargo 요청 수(≤6).
2. 이관: 팀/통합 state 행 수, adopt된 경기 수, 버린 v1 id 수.
3. `--dry-run`: 11토픽 각각 create/update/delete 수 — delete가 토픽당 10% 초과면 중단·보고.
4. apply 1회 → 2회째 전건 UNCHANGED(11토픽 모두).
5. 오염 summary 교정 건수(≥16) + 교정 후 토픽 11개에서 `[`·`http`·`#`로 시작/포함하는 summary 0.
6. KT 롤스터 토픽 이벤트 수 > 0.
7. 피드 2회째 run: 처리 메시지 0, LLM 호출 0.
8. 11토픽 완전중복(summary,start,cat) 0.

## 10. 하지 말 것
- Cargo를 팀별로 11번 호출하지 말 것(페이지별 1회, 메모리로 분배).
- 통합 summary에 `[DK]` 같은 태그를 넣지 말 것.
- absent=delete는 state에 있는 `match|` 키만 대상이다(§3의 `absent_filter`). state 밖 이벤트(사람이 만든 것, 옛 봇 잔재)는 엔진이 건드리지 않는다 — 옛 잔재 정리는 별도 보고 후 사용자 결정.
- dal-bots-spec 패키지 수정 금지.
