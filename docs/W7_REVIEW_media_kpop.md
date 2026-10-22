# W7 코드 리뷰 — 미디어·K팝 그룹 (23봇, 읽기 전용, 2026-10-22)

체크리스트: [W7_REVIEW_CHECKLIST.md](W7_REVIEW_CHECKLIST.md). 규칙: [W2_REBUILD_BRIEF.md](W2_REBUILD_BRIEF.md).
대상: `C:\Users\gamer\Documents\<봇>`의 현재 코드(legacy/ 제외). bat → 진입점 → 실행 경로 모듈을 전부 읽었고, 근거로 로그·state sqlite를 읽기 전용으로 조회했다. 파일 수정과 봇 실행은 하지 않았다.

- **범위 조정:** gacha·qwer·collab·dcbot의 B2·B3(LLM 실패·커서·RetryQueue)는 리뷰1 담당이라 눈에 띄는 것만 적었다. 이 4봇은 A·C·D·E·F와 공유 토픽 A4(리센느) 중심으로 봤다.
- **B2·B3를 끝까지 추적한 봇:** berriz·timetree·mnetplus·festival. 4봇 모두 Discord catchup을 쓰지 않아 B3는 해당이 없다. LLM은 timetree·festival만 쓴다.
- **검증 방식:** 서브에이전트 6개가 병렬로 읽었다. P0과 주요 P1은 내가 코드·데이터를 직접 다시 확인했다(runbot races.db, gamingschedulebot seen.db, festivalbot 로그, botkit/sync.py).

## 요약 — 봇별 건수

P0/P1은 beads에 건마다 등록했다. P2는 봇별로 묶어 등록했고, P3는 이 보고서에만 적었다. "엔진"은 아래 §0의 botkit 공통 결함(P0 1건)을 뜻한다. 해당 봇이 자기 P0로 따로 세지는 않는다.

| 봇 | P0 | P1 | P2 | P3 | 비고 |
|---|---|---|---|---|---|
| botkit.sync (엔진) | 1 | – | – | – | §0, absent=delete + topic 입양 봇 전부 노출 |
| gachabot | 0 | 0 | 1 | 4 | |
| qwerbot | 0 | 0 | 3 | 4 | A4 리센느 |
| collabcafebot | 0 | 0 | 2 | 3 | |
| dcbot | 0 | 0 | 1 | 3 | 검수 모드(쓰기 없음) |
| berriz-bots | 0 (엔진) | 0 | 1 | 4 | |
| timetree-bots | 0 (엔진) | 0 | 2 | 4 | |
| mnetplusbot | 0 (엔진) | 2 | 2 | 3 | A4 리센느 |
| festivalbot | 0 (엔진) | 1 | 2 | 3 | |
| animebot | 0 | 2 | 2 | 4 | SyncEngine 미사용(W6 결정) |
| laftelbot | 0 (엔진) | 2 | 2 | 4 | |
| netflixbox | 0 (엔진) | 2 | 1 | 2 | |
| dramabot | 0 (엔진) | 2 | 2 | 3 | |
| kdramabot | 0 (엔진) | 1 | 4 | 5 | |
| moviebot | 0 | 2 | 4 | 4 | SyncEngine 미사용(.57) |
| album_bot | 0 (엔진) | 1 | 3 | 4 | P1은 .59에 코멘트(잠복, Metacritic 복구 시 P0) |
| naehanbot | 0 (엔진) | 1 | 1 | 2 | |
| figurebot | 0 | 0 | 2 | 5 | |
| gamepassbot | 0 | 3 | 3 | 2 | SyncEngine 미사용 |
| gamingschedulebot | **2** | 2 | 4 | 2 | SyncEngine 미사용, PS Plus 실데이터 오등록 |
| onlinegamebot | 0 | 2 | 2 | 3 | |
| runbot | **2** | 1 | 4 | 4 | SyncEngine 미사용, 11-24 이후 삭제 예정 1건 |
| doujin_bot | 0 (엔진) | 0 | 1 | 2 | |
| museum_bot | 0 (엔진, 자체 트리거) | 0 | 0 | 4 | |
| **합계** | **5** | **24** | **49** | **78** | |

※ gamepassbot·gamingschedulebot의 ±48h 흡수 P1은 한 건으로 묶어 등록했다. TMDB api_key 노출 P1(netflixbox·dramabot·kdramabot)도 한 건이다. 그래서 beads의 P1 건수는 표의 합계보다 적다.

---

## 0. botkit 공통 — topic 범위 입양 id를 같은 run의 absent가 삭제 (P0, 엔진)

- [P0] [A4/A8] `src/botkit/sync.py:383-387, 507-513, 538-571` — `adopt_scope="topic"`(기본값)인 `_lookup`은 `self.cache.lookup(summary, start)`만 본다. 이미 다른 key가 쥔 id(`_claimed`)를 빼지 않는다. 그리고 `_delete`는 absent 행의 event_id가 이번 run에 다른 key로 넘어갔는지 확인하지 않는다.
  - 근거: `return self.cache.lookup(summary, start)` → `self._update(ev, adopt, stats, h, counter="adopted")`, 그 뒤 `for k, r in absent_rows.items(): self._delete(k, r, stats)`
  - 경로: 소스가 같은 일정을 **새 id로 다시 발급**하고 (summary, start)가 같다. 그러면 ① 새 key(NEW)가 옛 key의 이벤트 X를 입양해 PATCH하고 ② 같은 run의 absent에서 옛 key가 X를 DELETE한다. ③ 새 key의 state는 삭제된 X를 가리킨 채 남는다. 다음 run에는 hash가 같아서 UNCHANGED이고, id가 없으니 MISSING(`on_missing="ignore"`)이 된다. 결과적으로 **소스에 살아 있는 일정이 영구 소실**된다. alert도 없다. 1~3건이면 각 봇의 소멸 비율 가드도 통과한다.
  - 노출된 봇(absent=delete + 기본 adopt_scope):
    - berriz-bots `bot.py:207` — Berriz id 교체 전례 있음(w3-berriz, 당시는 날짜가 달라 무사)
    - timetree-bots `bot.py:239`
    - mnetplusbot `bot.py:228` — reconcile은 점유 id를 빼지만 엔진 입양이 그다음에 돈다
    - festivalbot `bot.py:448`
    - netflixbox `bot.py:90-93` — key 우선순위가 imdb > nf > jw라, imdb가 뒤늦게 채워지면 key가 바뀌어 이 경로를 탄다
    - album_bot — 나무위키 `n:` 행과 멜론 `dz:` 행이 같은 앨범으로 공존하는 경우. 현재는 source_complete=False라 비활성
    - naehanbot, laftelbot, kdramabot, dramabot — key 체계가 바뀔 때만 해당
    - doujin_bot `bot.py:276` — comicw가 같은 제목·날짜로 재등록하는 경우. 선례로 지스타 342→414가 있었는데, 그때는 제목이 달라 무사했다
    - **museum_bot** `bot.py:116-128` — 봇 자체 `dedupe`가 같은 (summary, start)에서 순위 높은 key만 남긴다. 진 key가 state에 있고 이긴 key가 NEW이면, 소스 변화가 없어도 이 경로가 열린다(국립중앙 통합 피드 → 지역 국립박물관 피드 시차 게재)
  - 제안:
    - **엔진:** `_lookup`의 topic 모드에도 `exclude=self._claimed`를 적용한다. 또는 `_delete` 전에 `row.event_id`가 이번 run에 `_save`된 id이면 state 행만 지우고 이벤트는 남긴다.
    - **봇 쪽 응급:** 단독 토픽 봇은 `adopt_scope="category"`. museum_bot은 dedupe에서 이미 state에 있는 key를 우선한다(onlinegamebot `dedupe(known)` 방식).

---

## 1. Discord·K팝 (gachabot · qwerbot · collabcafebot · dcbot)

### 공유 토픽 A4 — 리센느 `4k-F0Htz1dl6`

| 봇 | 쓰기 | key | state | absent | 동일 일정 판정 |
|---|---|---|---|---|---|
| qwerbot rescene (2x 11:00·19:30) | 메시지마다 `engine.sync([ev])` | `{트윗id}\|{순번}`, legacy `tweet:…` | `qwerbot/state/rescene.db` | keep | `Engine.find`(KST 날짜 + 글자 정렬 제목 시그니처) → Haiku 근접중복 → hash만 저장 입양 + 엔진 topic 입양. 취소 트윗은 `find(loose=True)`로 타봇 이벤트를 찾아 `(취소)` PATCH |
| mnetplusbot (daily 11:15) | run당 1회 | `mnet\|{id}` | `mnetplusbot/state/mnetplus.db` | delete + `absent_filter=mnet\|` + 창(now-30d, now+400d) + 30% 가드 | `reconcile`: 같은 KST 날짜 + 정규화 제목 **완전 일치**(시각·카테고리 무시, 자기 점유 id만 제외) → 엔진 topic 입양 |
| dcbot rescene (2x) | **없음**(bat에 `--apply` 없음 → pending.jsonl만) | `dc-rescene1-{날짜}\|{정규화제목}` | `dcbot/state/rescene1_sync.db` | keep, `source_complete=False` | `--apply`면 엔진 topic 입양(PATCH) |

- 확인됨: 다른 봇의 **key**를 absent로 지우는 경로는 없다. state 파일과 접두가 분리돼 있고, mnet에는 `absent_filter`가 있으며, qwer는 keep이다. qwerbot 3그룹은 토픽·state가 모두 분리돼 있다. 같은 토픽에 두 번 sync하는 bat도 없다. 실제 실행 시각(11:03 / 11:39)이 겹치지 않는다. 락은 없다(P3).
- 위험은 **이벤트 소유가 교차할 때** 생긴다. 해당 항목은 아래 mnetplusbot 절에 적었다.

### gachabot (bot.py, events.py, extract.py, config.py)
- 실행 경로: `run_catchup.bat` → `bot.py --catchup` → `run_catchup_once`(12채널) → handler에서 Haiku 추출(ParseCache) → 토픽별 `TopicSync.sync_one`. 토픽 12 + 통합 `IbM473NkLrAE`(단독). absent=keep, on_missing=ignore, protect_human_edits.
- [P2] [D1] events.py:68-79 — timed 일정인데 end가 없으면 start==end가 된다(`end = start` … `return start, end, False`). LLM이 end를 비우는 점검·특방 공지에서 생긴다. 빈도는 미확인. — 제안: 기본 길이(1h)를 주거나 종일로 처리.
- [P3] [A6] events.py:291-302 — 항목마다 `engine.sync([ev], source_complete=True)`를 부른다. keep이라 무해하지만, delete로 바꾸는 순간 P0가 된다. keep 고정 assert를 권장.
- [P3] [C1/F5] bot.py:149,175 — dry-run에서도 실제 `state/parse_cache.db`에 저장한다. README:13에 이 내용이 없다.
- [P3] bot.py:202 `C.TYPE_TAG[b.type]` — genshin `kigen`/`buki_kigen`이 TYPE_TAG에 없다(프롬프트에 그 type이 없어 가능성은 낮음).
- [P3] [A7] events.py:287-288 `match()`가 점유된 id도 재사용한다. 두 key가 한 이벤트를 공유하지만 keep이라 영향은 없다.
- 확인됨: A1~A5·A8 OK(keep, 단독 토픽, ignore). C1 OK(state·커서·재시도 큐는 tempdir 사본, bot.py:72-92). D1 OK(naive→KST events.py:45, 종일 23:59:59 :76). D2 OK. E1~E3 OK(target/rel :143, type 미매핑이면 스킵 :167). F1~F3 OK. G2 OK.

### qwerbot (bot.py, tweets.py, config.py, namu_supplement.py)
- 실행 경로: `run_catchup/run/run_redvelvet/run_rescene.bat` → `bot.py --group X` → `run_catchup_once(limit=200)` → handler → `register()` → 항목마다 `engine.sync([ev])`. absent=keep. `run_namu.bat` → `namu_supplement.py --apply`(별도 state `{g}_namu.db`, keep, rescene에는 돌지 않음).
- [P2] [D1/E] tweets.py:325-327 — 종일 일정의 종료일을 버린다(`end_dt = start_dt.replace(hour=23, minute=59, second=59)`). 팝업·전시 같은 기간 일정이 첫날만 남는다. — 제안: 종일이면 `to_dt(end)`의 KST 날짜 23:59:59를 쓴다.
- [P2] [G3] namu_supplement.py:38 — UA가 Googlebot 사칭이고 status를 확인하지 않는다(:50). — 제안: 정직한 UA + status 확인.
- [P2] [A4] bot.py:246-263 — 취소 트윗이 `find(loose=True)`로 같은 날 **첫 번째** 포함관계 이벤트를 골라 `import_legacy`로 붙이고 `(취소)` PATCH한다. 같은 날 후보가 여럿이면 엉뚱한 이벤트나 타봇 이벤트를 고를 수 있다. mnet 소스가 나중에 바뀌면 mnet PATCH가 `(취소)`를 덮는다(qwerbot 편집은 isBot이라 protect_human_edits로 보호되지 않음, histories.py:100-103). — 제안: loose 후보가 2건 이상이거나 타봇 소유면 alert만 하고 보류.
- [P3] [A6] bot.py:191-194 — 메시지별 sync(keep이라 무해, assert 권장).
- [P3] [A7] namu_supplement.py:172 key `namu|{date}|{norm(title)}`에 매주 Haiku 재추출 값이 들어간다. `is_dup`이 ±1일·유사도 0.6으로 대부분 막는다.
- [P3] tweets.py:323 `bool(ev.get("all_day"))` — 문자열 `"false"`가 True가 된다(gachabot은 `as_bool`).
- [P3] [F2] `tweet_seen`·`tweet_parse`·`dup_judge` 정리 루틴이 없다.
- 확인됨: A1~A3·A5·A8 OK. C1 OK(tempdir 사본 :398-404, namu는 `--apply` 없으면 DRY). D1 OK(timed 기본 +2h30, `end<=start` 보정 :329-331). D2/D3 OK(namu는 `today.year, +1` + alert). E1~E3 OK. F1~F3 OK. G2 OK.

### collabcafebot (popup_bot.py, events.py, classify.py, sources_discord.py, sources_web.py)
- 실행 경로: `run.bat` → `popup_bot.py sweep` → Discord catchup(분류만 캐시) + 팝가·데이포유 → `build_events` → `resolve_existing` → **`engine.sync(events, source_complete=False)` run당 1회**(:192). 토픽 `wyJcQePq9IFq`(단독). absent=keep.
- [P2] [A7] events.py:169-179, 269-281, 698-702 + sources_web.py:170,247 — key `fair||{ip_id}|{chain}|{start}|…`에 LLM 분류값이 들어간다. 입력 해시가 바뀌어(데이포유는 해시 입력이 `제목|장소|기간`) 재분류되면 새 key가 된다. 기존 이벤트는 옛 key가 점유하고 있어 `aux.set_alias` 후 건너뛰고, 이후 영구 skip된다(:619-621). 그래서 기간 연장·제목 수정이 반영되지 않는다. — 제안: 같은 (source, item_key)의 옛 key로 이어 붙이거나, alias 대상 내용이 다르면 alert.
- [P2] [G1] events.py:428-466 — 같은 key 그룹의 대표 후보 `members[0]`가 run마다 소스 존재 여부(팝가 상위 20개, 일시 실패)에 따라 바뀐다. 그때마다 summary·description이 PATCH로 오간다. 빈도는 미확인.
- [P3] [B1] sources_web.py:213-217 — 데이포유 상세 실패 시 `""`인 채 분류되고 그 결과가 캐시에 고정된다.
- [P3] [E2] events.py:116 — LLM description을 escape 없이 HTML에 넣는다.
- [P3] popup_bot.py:87-115 — 저신뢰 후보는 `c.fresh`일 때만 처리하고, 승인 큐·리뷰 채널이 둘 다 실패하면 재시도가 없다.
- 확인됨: A1~A3·A5·A6·A8 OK(keep, run당 1회, `groups` dict). C1 OK(tempdir 사본 :65-77, AuxStore가 사본 conn을 씀). D1 OK. E1~E4 OK(clean_title, target/rel :104, fair location 64자 압축 :250-265). F1~F3 OK. G2/G3 OK.

### dcbot (bot.py, vision.py, lib/dc_fetcher.py, galleries/rescene.yaml)
- 실행 경로: `run_rescene.bat` → `bot.py --config galleries\rescene.yaml`(**--apply 없음 = 검수 모드**) → 정보탭 → Sonnet 비전 → dedup → `state/rescene1_pending.jsonl`. **dal.wiki 쓰기는 없다.**
- [P2] [B4, --apply 한정] bot.py:339-351 vs 355-361 — `--apply`일 때 `post_done`을 sync보다 **먼저** 커밋한다. 그 run의 sync가 실패하면 다음 run에서 '변경없음'으로 스킵되어 영구 누락된다(:257-259). 현재 bat에는 영향이 없다. 승인 플로우를 붙일 때 고칠 것. 또 `--apply`의 엔진 topic 입양은 mnet·qwer 이벤트를 PATCH한다(adopt=_update). 켜기 전에 `adopt_scope="category"`가 필요하다.
- [P3] [F2] pending.jsonl 209행이 전부 pending이다. 소비 경로가 없다(알려진 한계).
- [P3] [D1, --apply 한정] bot.py:173-175 — `"24:30"`이면 `datetime(…, 24)`에서 ValueError가 난다.
- [P3] [G2] `list_events(take=300)` — 클라이언트가 재귀로 분할하므로 잘림은 없다(확인만).
- 확인됨: A1~A3·A5~A8 OK. C1 OK(메모리 사본 :72-75). D3 OK(`resolve_date` 요일로 연도 역산 :148-162). B1 OK(리스트 실패·0건이면 alert, LLMUnavailable이면 기록 없이 중단 :283-286). E1·E3 OK. F1~F3 OK. G3 OK(UA·Referer·간격·백오프).

---

## 2. K팝·LLM (berriz-bots · timetree-bots · mnetplusbot · festivalbot)

4봇 모두 botkit.discord를 쓰지 않는다(B3 해당 없음). LLM은 timetree·festival만 쓰고, 둘 다 `LLMUnavailable`을 잡은 뒤 **상태를 전진시키지 않는다**(보류 + absent 제외 + 캐시 미기록). B2의 P0 경로는 없다.

### berriz-bots (bot.py, berriz.py, artists/*.yaml)
- 실행 경로: 2x `run_all.bat` → `bot.py --artist ive`, 이어서 `--artist kiiikiii`. 토픽 IVE `ahGUO5dJTIOH` / KiiiKiii `ovRE7nQSAlX2`(단독). absent=delete, `absent_filter=not ticket|`, 창(now-7d, now+90d), protect_human_edits, adopt_scope=topic(**엔진 §0**).
- [P2] [D1/E] bot.py:73-75 — 종일 일정의 endDate를 무시하고 시작일 23:59:59로 자른다. 여러 날 종일 일정이 첫날만 남는다(실데이터 미확인).
- [P3] bot.py:77 timed start==end(README에 명시). bot.py:137 티켓 후보마다 `get_event` 조회(현재 0건). bot.py:208 ticket 복사본은 원본이 정정돼도 남는다(README 명시). `.env`의 미사용 `CHANNEL_LOG`.
- 확인됨: A1 OK(창을 덮는 달 전체를 조회한 뒤 `lo <= start < hi`, :92-95·111·224). A2 OK(달 하나라도 실패하면 ok=False, :101-103·192). A3 OK(ticket| 필터). A6~A8 OK. 소멸 30% 가드. B1/B5 OK. C1/C2 OK(`:memory:` 사본). D1~D3 OK. E1~E4 OK. F1·F2·F4 OK.

### timetree-bots (bot.py, timetree.py, artists/seventeen.yaml)
- 실행 경로: 2x `run_seventeen.bat` → `bot.py --config artists\seventeen.yaml`. 토픽 `rM65HqLLyJc1`(단독). absent=delete, `absent_filter=key not in held`, 창(now-3d, now+210d), adopt_scope=topic(**엔진 §0**).
- B2 추적: `classify`(:118-143)는 LLMUnavailable이면 즉시 반환하고, LLMParseError면 그 청크만 스킵한다. 분류에 실패한 신규 항목은 `held`가 되어 events와 absent에서 모두 빠지고, 캐시를 쓰지 않아 다음 run에 재질의한다 → 영구 누락 없음.
- [P2] [B2] bot.py:272 — alert 조건이 `if held:` 하나뿐이다. 그래서 LLMUnavailable인데 신규 항목이 없고(텍스트 변경 항목만 있고) 크레딧이 소진된 상태가 조용히 지속된다. `err`도 로그에 남지 않는다. — 제안: `err`가 "LLM 불가"면 항상 alert.
- [P2] [D1/E] bot.py:165-167 — 종일 일정의 `end_at`을 무시한다(berriz와 같은 패턴).
- [P3] [C2] bot.py:239·243 — dry-run에서도 디스크 state를 열어 `import_legacy`와 `catcache.put`을 실제로 기록한다(이관이 끝나 지금은 무해). bot.py:169-170 timed start==end. `tt_category` 정리 없음. 소멸 가드 집계에 held가 포함된다(안전 쪽으로만 틀림).
- 확인됨: A1 OK(API from/to = 창). A2 OK(페이지 실패·비200·50쪽 초과면 complete=False, 0건이면 alert, timetree.py:68-76). A3·A6~A8 OK. C1 OK. D1~D3 OK. E1~E4 OK. G2 OK(text_hash 캐시).

### mnetplusbot (bot.py) — 공유 토픽 4k-F0Htz1dl6
- 실행 경로: daily `run_apply.bat` → `bot.py`. key `mnet|`, 카테고리 상수 6종 + Default `R53sARwWFrgm`. absent=delete, `absent_filter=mnet|`, 창(now-30d, now+400d), reconcile + 엔진 topic 입양(**엔진 §0**: reconcile은 점유 id를 빼지만, 그다음 엔진 입양이 옛 mnet key의 id를 정확 일치로 입양한다. 공유 이벤트라면 qwerbot 쪽도 함께 사라진다).
- [P1] [A4] bot.py:172-174 ↔ qwerbot/bot.py:121-130 — **qwerbot이 먼저 만든 일정을 mnet이 중복 생성한다.** 판정이 한쪽 방향이다. qwer는 같은 날짜 후보 + Haiku로 mnet 이벤트를 잡지만, mnet은 같은 날짜 + 정규화 제목 **완전 일치**만 본다(`index.get((kst_date(...), norm_title(ev.summary)))`). 그다음 엔진도 (summary, start) 완전 일치만 본다. 근거로 w3-qwerbot 노트에 "rescene 18건 중 3건 mnet 근접중복(정규화 제목이 달라 못 잡음)"과 완전중복 1쌍(J5diVALiM20T/nXeIl2Wic8IZ)이 기록돼 있다. — 제안: reconcile에 qwerbot `title_sig`(어순 무관)·포함관계·±1일을 맞추거나, 같은 날 타봇 후보가 있으면 생성을 보류하고 alert.
- [P1] [A4/A3] bot.py:157-192·230 — `absent_filter`는 다른 봇의 **key**는 보호하지만 **이벤트**는 보호하지 않는다. reconcile로 qwerbot·사람 이벤트를 `mnet|` key가 쥔 뒤 그 mnet 항목이 소스에서 빠지면(창 안, 가드 이하), mnet이 **타봇 이벤트를 삭제**한다. qwerbot이 붙인 location·트윗 링크도 함께 사라지고, qwerbot은 MISSING(ignore)로 재생성하지 않는다. 실제 공유 사례도 있다: REPOINT `mnet|6abeaf6f…` → `J5diVALiM20T`(mnetplusbot.log 10-16). README "알려진 한계"에 적혀 있지만, 손실을 감지하는 경로가 없다. — 제안: 타봇에서 입양한 행을 표시해 두고, absent 시 keep + alert.
- [P2] [A4/G1] bot.py:180-183 — reconcile이 날짜와 제목만 보고 시각·카테고리를 무시한다. 같은 날 같은 제목의 다른 회차(14시·19시)를 qwer가 한 건만 올렸다면, mnet의 다른 회차가 그 이벤트를 입양한다. 이때 PATCH 없이 hash만 저장하므로 1회차가 누락되고 qwer의 시각이 그대로 남는다. — 제안: timed면 시작 시각 ±1h까지 비교.
- [P2] [A4] qwerbot `(취소)` PATCH ↔ mnet bot.py:105 `summary=title` — mnet 소스가 무엇이든 바뀌면 `(취소)` 표시를 덮어쓴다. — 제안: 토픽 제목이 `(취소)`로 시작하면 접두를 유지하거나 PATCH 대상에서 뺀다.
- [P3] [F4] 2x(qwer 11:00)와 daily(mnet 11:15)가 겹치면 동시 CREATE로 중복이 생길 수 있다(현재는 시간 분리 + README 경고, 락 없음). `.env`의 미사용 `DALWIKI_TOPIC_ID`, 잔재 categories.json.
- 확인됨: A1 OK(15개월 전부 조회 후 창 필터 :116-127). A2 OK(`raise_for_status` + 형식 검사, 실패하면 alert :77-82·128-133, 0건이면 sync 생략). A5 OK(repoint/respect). A6~A8 OK. C1/C2 OK(`:memory:`, reconcile `put`도 사본). D1~D3 OK. E1~E4 OK. F1·F2 OK.

### festivalbot (bot.py)
- 실행 경로: weekly `run.bat` → `bot.py`. 토픽 `IZdp3T7oB48s`(단독). absent=delete, `absent_filter=key not in held`, 창(오늘 00:00 KST, d_to-1일), 20% 가드, adopt_scope=topic(**엔진 §0**).
- B2 추적: `llm_judge`(EV02만)는 LLMUnavailable이면 이후 EV02 전부를 held로 두고 run 끝에 alert(:398-409, 431-432). LLMParseError는 그 건만 held로 두고 판정을 저장하지 않는다 → 영구 누락 없음.
- [P1] [A2/A1] bot.py:446-468 — **TourAPI 목록이 같은 contentid를 일시적으로 빠뜨리면 삭제 후 재생성(새 id)이 일어난다. 로그로 실증됐다.**
  - `2026 수성국제비엔날레`(3370452): 10-15·10-16 `DELETE(absent) 3370452 Xk0AAAJmqrBF` → 10-19 `CREATE 3370452 … → VYOKF5u3G-Eq`(logs/festivalbot.log 646·1732·1762·1802행).
  - 10-19에는 10-28~30 시작인 미래 축제 6건이 목록에서 빠져 삭제됐다(홍성바비큐·성남동 커피·부산웹툰·양주 드론봇·부여국화·고양한우, 1805-1810행). 목록은 217→170건이었고, 6/144로 가드에 걸리지 않았다.
  - W2의 "소멸 24건" 판단 중 적어도 1건은 같은 id로 돌아왔다. 사람 편집·댓글이 사라졌다 다시 생긴다(protect_human_edits=False). 원인은 미확인.
  - 제안: 삭제 전에 `detailCommon2`로 재확인해 응답이 있고 미래면 held로 돌린다. 또는 2회 연속 소멸일 때만 삭제한다. 10-26 run에서 6건이 재생성되는지 확인할 것.
- [P2] [B/E] bot.py:151-156·215-216 — 200인데 `detailCommon2`의 items가 비면 `{}` → "상세 없음" → `no_basic`이 **90일 캐시**된다. 그러면 TTL 만료 후 재판정 run에서 absent 삭제로 이어진다. — 제안: `{}`이면 held로 두고 판정을 저장하지 않는다.
- [P2] [G1/A] bot.py:50·184·397-418 — 판정 TTL 90일 후 EV02를 다시 묻는다. Haiku가 YES→NO로 뒤집히면 등록된 축제가 삭제된다(경계선 축제가 90일 주기로 삭제·생성을 오갈 수 있음). — 제안: 등록된 cid는 재판정 NO여도 유지한다.
- [P3] [F1] bot.py:103·110 — 4xx `raise_for_status()` 메시지를 `str(e)[:120]`으로 남기는데, serviceKey가 첫 파라미터라 **키 앞 9~12자**가 로그에 남는다. [B2] :410-413 반복되는 ParseError에 alert가 없다. [C2] :448-451 dry-run에서도 `import_legacy_state`(이관 완료라 무해). [F3] run.bat에 chcp가 없다(실해 없음).
- 확인됨: A1 OK(진행 중 축제는 start<오늘이라 창 밖, 목록 `eventStartDate=오늘~+180`). A2 OK(페이지 실패면 complete=False, `len(seen) >= totalCount`). A3·A5~A8 OK. C1 OK. D1~D3 OK. E1~E4 OK. F1·F2 OK. G2/G3 OK.

---

## 3. 애니·OTT (animebot · laftelbot · netflixbox · dramabot)

### animebot (bot.py, namu.py, schedule.py)
- 실행 경로: daily `run.bat` → `bot.py`. 토픽 `Fhumn3q1yXWA`. **SyncEngine 미사용**(W6 결정): 자체 sqlite `namu_anime.db` + `update_or_create_event`/`delete_event` 직접 호출. 삭제는 "이번에 파싱된 작품 + rd≥today + CUTOFF 이후 + 이번 산출에 없음" 조건이다(:263-274).
- [P1] [A1/B1] bot.py:200-209, 264-274 + namu.py:504-542 — **부분 파싱을 성공으로 처리해 미래 이벤트를 삭제한다.** `parse_show`는 HTML만 있으면 회차표가 비어도 dict를 돌려주고, 그 작품은 `parsed_ok`에 들어간다. 그런 작품의 미래 key 중 이번에 산출되지 않은 것이 전부 `delete_event`된다. 근거: `if not show: failed.append(page)…; parsed_ok.add(page)` → `if key in produced or page not in parsed_ok or rd < today: continue`. 나무위키 표 마크업이 바뀌면 hot 작품 전체가 동시에 지워진다. PARSE_FAIL_ALERT는 None만 센다. 파서가 복구되면 새 id로 재생성된다. — 제안: DB에 미래 행이 있던 작품의 산출이 0건이면 실패로 보고 보존한다. run당 삭제 상한 + alert.
- [P1] namu.py:130-146 — **분기 분류 페이지 1쪽만 읽는다.** `fetch(f"분류:{year}년 {quarter}분기 일본 애니메이션")` 1회 후 `<li>`만 모으고 다음 쪽(`cfrom=`)을 따라가지 않는다. 3분기 캐시에는 `cfrom=히로인? 성녀?…` 다음쪽 링크와 `<li>` 102개가 있었고, 4분기는 93개로 상한에 근접했다. 분기 중 문서가 늘면 가나다 꼬리 신작이 조용히 누락된다. — 제안: `cfrom`을 따라 순회.
- [P2] [A5] bot.py:246-252 + client.py:490-503 — sig가 바뀌면 `update_or_create_event` 404 → create로 **사람이 지운 이벤트를 되살린다.** 사람 편집 보호도 없다. — 제안: 404면 'deleted'로 표시하고 재생성하지 않는다.
- [P2] [G3] namu.py:28-31 — Googlebot UA 위장(빈 셸 회피 목적, 의도된 설계). 간격 1.2s·서킷 브레이커 OK. 사용자 판단 사항이다.
- [P2 기존] bot.py:112-113 timed start==end — 이미 .62에 있다.
- [P3] 규칙 2 형태 위반(W6 결정). `.env`의 미사용 `GOOGLE_API_KEY`·`GOOGLE_CSE_ID`·`ANTHROPIC_API_KEY`. `FileHandler`라 회전이 없고(bot.log 329KB, 옛 로그 잔재) releases 테이블 정리도 없다. bot.py:246 클립 전 summary로 `cache.lookup`.
- 확인됨: A1 OK(지난 이벤트·파싱 실패 작품 불가침, 분류 실패 시 정리 생략). A7 OK(`문서명#첫 화`, 개명 시 migrate_page). A8·B4 OK. C1/C2 OK(migrate rollback :282). D1 OK. D3 OK(분기 롤오버 :151-156). E1~E3 OK. F3·G1 OK.

### laftelbot (bot.py, laftel.py)
- 실행 경로: daily `run_new.bat` → `bot.py new`, weekly `run_expire.bat` → `bot.py expire`. 둘 다 `state/laftel.db` + 토픽 `6X7pfGtRIOHw`. absent=delete, 창 new=(now-7d, now+60d) / expire=(now, now+90d), `absent_filter=key.endswith("|"+mode) and not protected`(:607). adopt_scope=topic(엔진 §0).
- A3/F4 OK: 같은 state·토픽을 쓰지만 접미 필터로 상대 모드와 `legacy|` key를 보호한다. 실행 시각도 분리돼 있다(new 11:35, expire 월 13:09).
- [P1] [A2] laftel.py:94 + bot.py:263-292 — **expire 목록이 2%까지 빠져도 complete=True다.** `if not total or len(out) < total * 0.98: raise`. 약 44쪽 offset 페이징 중 순위가 바뀌어 건너뛴 작품(최대 약 87건)의 `|expire`가 창 안이면 삭제된다. 다음 run은 1주 뒤라, 그 사이 만료일이 지나면 재생성되지 않는다(영구 누락). 빈도는 미확인(로그에 total 미기록). — 제안: absent 후보를 '이번 목록에 실제로 있는 iid'로 한정하고, len/total을 로그에 남긴다.
- [P1] [A2] bot.py:199-206 + 167-172 — 편성 공지 조회 실패를 `log.warning(…무시)`로 넘긴다. 그런데 `upcoming_date()`는 `notices`를 두 번째 날짜 소스로 쓴다. 그래서 공지로만 날짜가 잡히던 공개예정은 산출되지 않고 absent로 삭제된다(complete=True 유지). 다음 날 새 id로 재생성된다. — 제안: 실패하면 complete=False로 하거나 upcoming key를 protected에 넣는다.
- [P2] [D3] bot.py:152-156 — 연도 생략 날짜를 앞으로만 롤오버한다. today=1월 초에 "12월 28일 공개" 공지가 남아 있으면 다음 해 12/28이 된다. — 제안: `> today+300일`이면 year-1.
- [P2] laftel.py:209-223 — 1화 공개 시각을 찾으면 영구 캐시한다. 공개 연기를 반영하지 못한다(라프텔이 시각을 바꾸는지는 미확인). — 제안: pub>now면 TTL.
- [P3] list_hash에 notice가 없어 최대 6일 늦게 반영된다(README TTL 설계). `_LEAD_BRACKET`이 원제 `[...]`까지 제거한다(E1 허용 범위). "오전 12시"를 12시로 해석한다. `.env`의 미사용 키 5개.
- 확인됨: A1 OK(new 산출 `lo<=pub<=hi`와 창이 일치, expire는 start=만료일 00:00 KST라 당일·과거 보호). A4~A8 OK. B1 OK(discover 실패면 `[], False` + alert). C1/C2 OK(메모리 사본, cleanup-legacy는 클라이언트 DRY가 차단). D1 OK. E2·E3·F3·F5 OK.

### netflixbox (bot.py, sources.py, adopt.py)
- 실행 경로: daily `run_netflixbot.bat` → `bot.py` → uNoGS·JustWatch·TMDB. adopt.py는 state가 비었을 때만 돈다(:212-213). 토픽 `VOOirIQJ3hhQ`, absent=delete, absent_filter(new|는 start≥since, end|는 start≥내일, surplus|는 항상), adopt_scope=topic(**엔진 §0**: key 우선순위 imdb > nf > jw → imdb가 뒤늦게 채워지면 key 변경 → 이 경로).
- [P1] [A2/B1] bot.py:115-120, 201, 264 — TMDB 실패가 5건 미만이면 그 타이틀만 빼고 complete=True로 진행한다(`except SourceError: fails += 1; continue`, `tmdb_ok = (f1+f2) < TMDB_FAIL_LIMIT`). new| key에는 재조회가 없어 absent 삭제되거나, 첫 등록 run이면 아예 생성되지 않는다. 같은 run에서 `last_success`도 전진하므로(:264) since 경계 작품은 다음 창에서 빠진다(영구 누락). — 제안: 실패 key를 protected에 넣고, f1>0이면 last_success를 동결한다.
- [P1] [F1] sources.py:187-192 — TMDB `api_key`가 쿼리 파라미터다. `requests.RequestException` 메시지("…url: /3/…?api_key=…")가 `SourceError`로 감싸져 log.warning(bot.py:119·150)으로 남는다. 폴더 루트의 옛 `netflixbot.log`(git ignore, 미추적)에 `api_key=` 줄 5개가 실제로 있다(2026-05-08 TMDB 502). 현행 logs/에는 0건. → TMDB 3봇 통합 이슈.
- [P2] [A1/A4] adopt.py:56-76 + bot.py:212-213, 247-248 — state가 비면(삭제·손상) adopt가 **자동** 재실행된다. 토픽 -300~+400일을 (카테고리, 제목)으로 묶고 잉여를 `surplus|`로 넣는데, surplus는 창과 무관하게 삭제 후보다. 그래서 시기가 다른 정당한 재등록 과거 이벤트도 지워진다. 알려진 한계(README). — 제안: 명시 `--adopt` 플래그로만 실행.
- [P3] sources.py:59,72 uNoGS 페이징 종료가 date 내림차순을 가정한다(미확인). bot.py:73-79 평점 변동마다 PATCH.
- 확인됨: A1 OK(new [since,∞) = uNoGS 절단, end [내일,∞) + 단건 재조회). A2 OK(uNoGS·JW 실패면 complete False). A3·A5·A6·A8 OK. C1/C2 OK(첫 dry-run은 `:memory:`). D1 OK. E1~E3 OK. F3·F5 OK.

### dramabot (drama_bot.py)
- 실행 경로: daily `run_drama_bot.bat` → `drama_bot.py --apply`(botkit.run 미사용). 토픽 `excH0WFng4WM`. absent=delete, 창 [오늘 00:00 KST, +121일), 페이지 상한이면 창 축소(:415-419), 창 안 absent는 `/tv/{id}`로 재확인(:293-320), max(3, 10%) 초과면 keep(:440-442). adopt_scope=topic(엔진 §0).
- [P1] [F1] drama_bot.py:107-113, 125-128, 176-181, 409-410 — TMDB `api_key` 쿼리 + `r.raise_for_status()`라서, discover가 실패하면 `alert(f"TMDB discover 실패 …: {e}")`가 `[HEALTH-ALERT]` 줄에 **전체 URL(키 포함)**을 찍는다. health_check.py:200-202가 앞 200자를 Discord로 보내는데, 키는 약 130자 위치라 포함된다. 현재 로그에는 0건. → TMDB 3봇 통합 이슈.
- [P1] [A8] drama_bot.py:104-117 — discover 결과를 id로 dedupe하지 않는다(위 코드 확인: `results.extend(page)`). `sort_by=first_air_date.asc`는 같은 날짜 동점이 많아 페이지 경계에서 중복이 날 수 있다. 그러면 key `tv:{id}`가 중복 → 엔진 `ValueError("Event.key 중복")`이 난다. `except SyncAborted`만 잡으므로(:452) run 전체가 Traceback으로 실패하고, 데이터가 바뀔 때까지 매일 반복된다. 빈도는 미확인. — 제안: `{x["id"]: x}`로 dedupe.
- [P2] [규칙2/B6] drama_bot.py:199-231, 403-404 — `requests.post(…/categories/tree|create)`를 직접 호출한다(DRY 가드는 있음). try 밖이라 dal.wiki 오류가 Traceback 종료로 이어지고, botkit.run을 쓰지 않아 HEALTH-ALERT 규약도 적용되지 않는다(.58 ensure_categories 계열).
- [P2] [F2] drama_bot.py:73-77 — FileHandler라 회전이 없다(.58).
- [P3] Event에 link가 없다(TMDB 페이지 권장). overview를 escape 없이 넣는다. dry-run에서도 `../kdramabot/seed_candidates.yaml`을 쓴다(C1 누수, dal.wiki 무관).
- 확인됨: A1 OK(창 하한 = discover `gte=today`). A2 OK(discover 예외면 sync 전 return 1, 재확인 + 대량 가드). A3~A7 OK. C1/C2 OK(임시 state 사본). D1~D3 OK. E3 OK. F3 OK.

---

## 4. 드라마·영화·음반 (kdramabot · moviebot · album_bot)

### kdramabot (kdrama_bot.py, ott.py, snapshot.py)
- 실행 경로: daily `run_daily.bat` → `snapshot.py`(로컬 스냅샷만) → `kdrama_bot.py --apply`. 토픽 4개(드라마 통합 `K4V9xKVLkSxS`, 예능 3)에 엔진이 하나씩. absent=delete, source_window 없음, `absent_filter=absent_ok`, 공용 `sync_state.db`(PK 분리), adopt_scope=topic(엔진 §0).
- [P1] [F1] ott.py:51-56 + kdrama_bot.py:389, 612 — TMDB `api_key` 쿼리, `log.warning(f"TMDB 회차 조회 실패 …: {e}")`에 URL이 포함된다. → TMDB 3봇 통합 이슈.
- [P2] [A1/A2] kdrama_bot.py:593-594, 634-642 — EPG-only 작품과 결방 행의 삭제 창이 **채널 전체** 스냅샷 범위 `[epg_lo, epg_hi]`다. 숫자를 보면 10-22 스냅샷에서 KBS2는 10-21~10-25, 나머지 채널은 ~10-28이다. 지금은 안전하다. 하지만 한 채널이 통째로 빠지거나 일부만 오면 그 채널의 미래 EPG-only·결방 이벤트가 삭제된다(10% 가드 = 드라마 90행 기준 9건 이하). — 제안: 채널별 (min, max)로 판정하고, 레코드 0인 채널은 제외.
- [P2] [B1] run_daily.bat:7-8 + kdrama_bot.py:106-110 — snapshot.py가 실패해도 **가장 최근의 옛 스냅샷**으로 그대로 진행한다(`return files[-1]`). 신선도 검사와 alert가 없다. 결방 감지가 조용히 멈춘다. — 제안: 파일명 날짜 ≠ 오늘(KST)이면 alert.
- [P2] [A1] kdrama_bot.py:383-398, 636-641 — TMDB가 **예외 없이** 빈 회차를 주면 `TMDB_FAILED`에 들어가지 않고 `absent_ok`가 True가 된다. 그러면 EPG 범위 밖 미래 회차가 삭제 후보가 된다. — 제안: `tmdb_map`이 비면 실패로 취급.
- [P2] [규칙2] kdrama_bot.py:121-154 — `categories/tree·create`를 직접 requests로 부르고, 최상위만 읽는다(:126-127). .58에 있다.
- [P3] `_tmdb_status`가 예외를 삼켜 `(최종화)`가 깜빡인다. TMDB 회차와 EPG 결방이 같은 날 공존할 수 있다. FileHandler(.58). F5: docstring "DRY .env 기본 true"·"today+45"가 코드(argparse DRY, 63일)와 다르고 README가 없다. `alert()`에 `bot=`이 없다. snapshot.py:37-41이 xmltv 오프셋을 무시한다(KST 고정).
- 확인됨: A1 숫자 OK(예능 소스 [today-7, today+63] ⊇ 창 [today, today+63], OTT [today-3, today+60] ⊇ [today, today+60], 과거 행 보호 :624). A2 OK(실패는 absent_filter로 제외 + 10% 가드). A3/A6 OK(토픽당 1회). A4 OK(4토픽 단독). A7·A8 OK. C1/C2 OK(임시 사본 DB). D1·D3 OK. E1·E2 OK.

### moviebot (movie_bot.py)
- 실행 경로: `run.bat` → `movie_bot.py`. 토픽 `EsGKyUpMrKVm`. **SyncEngine 미사용**: `movie_bot.db` + `update_or_create_event` 직접 호출. absent 없음(.57에 이식 과제로 있음).
- [P1] [B1] movie_bot.py:187-196 — 메가박스 API 예외나 비200을 `log.error`만 남기고 `[]`로 바꾼다. 200인데 movieList가 비어도 같다. exit 0이고 HEALTH-ALERT도 없다. 신작·개봉일 변경 반영이 조용히 멈춘다. — 제안: 0건이거나 예외면 alert + exit 1.
- [P1] [B5] movie_bot.py:287-293 — `TopicCache.load`(strict) 실패를 `log.warning(…계속 진행)`으로 넘기고 빈 캐시로 진행한다(W1 이전 패턴). 그러면 404 복구가 무력해져, 저장된 id가 404일 때 중복 POST가 난다. — 제안: 실패하면 alert 후 쓰기 중단.
- [P2] [A8] movie_bot.py:375-398 — DB에 없는 movie_no는 TopicCache 조회 없이 곧바로 POST한다. 같은 영화가 movie_no 2개로 오거나 DB를 잃으면 중복이 생긴다('친숙한 손길' 09-23 완전중복 계열).
- [P2] [A5] movie_bot.py:336-343 — 사람이 지운 이벤트가 개봉일·등급·제목 변경 시 PATCH 404 → POST로 되살아난다.
- [P2] [G1/E] movie_bot.py:357-359 — 변경 판정이 description 재료를 보지 않는다("상영시간: 0분"이 고정됨). `if movie["play_time"]`는 "0"도 참이다.
- [P2] [B4] movie_bot.py:399-405 — 업서트 실패에 alert가 없다.
- [P3] reset.bat 전량 DELETE→POST(.57). naive `datetime.now()`. docstring "DELETE→POST"가 실제(PATCH)와 다르다. FileHandler, README 없음. 원본 링크가 link 필드에 없다.
- 확인됨: E1 OK(`[..]` 라벨 제거). E3 OK(등급미정 없으면 alert + 스킵, .57). D1·D2 OK. A7 OK(movie_no). C1 OK. G2 OK.

### album_bot (album_bot.py, sources/*, db.py, dalwiki.py, scripts/import_namuwiki.py)
- 실행 경로: `run.bat` → `botkit.run.run("album_bot", main)` → itunes/melon/musicbrainz/metacritic → 레지스트리 `releases.db` → SyncEngine 1회(absent=delete, 창 [내일, +450일), `absent_filter=vanished_keys`, strict 캐시). 토픽 `9LVm3HmCUSnN`. **폴더의 dalwiki.py·db.py는 dal.wiki를 직접 쓰지 않는다**(dalwiki.py는 제목·본문·카테고리 판별, requests import 없음. grep 결과 create/update/delete 직접 호출 0건) → 규칙 2·C1 OK.
- [P1 잠복] [A1/A2] sources/musicbrainz.py:46-55 vs album_bot.py:604-622 — MB는 관련도 정렬 `limit=100` 1쪽만 받는데(로그 "[MB] 100건 수집", 매 run 꽉 참) 소멸 판정은 [내일, +450일)의 MB 행 전체에 대해 한다. 상위 100 밖으로 밀린 정상 예고가 vanished → run당 최대 3건(`max(3, future//10)`) 삭제된다. **지금은 Metacritic 고장으로 source_complete=False라 비활성이고, Metacritic이 복구되면 즉시 P0가 된다.** .59에 "MB limit=100"으로 이미 있어 코멘트로 보강했다. — 제안: MB를 ABSENT_SOURCES에서 빼거나, 날짜 정렬 전 페이지를 받았을 때만 판정.
- [P2 기존] Metacritic 0건 → source_complete 영구 False + 매일 alert(.59).
- [P2] [A4/중복] import_namuwiki.py:256-261 vs album_bot.py:289-295 — 나무위키 행(`n:아티스트|앨범|날짜`)과 멜론·MB 행(`dz:{id}|날짜`)이 같은 앨범으로 두 행이 된다. summary가 같으면 엔진 topic 입양으로 id를 공유하고(엔진 §0), 다르면 중복 이벤트 2개가 된다. Mike D 중복(.59) 계열. — 제안: 삽입 전 정규화 조회 + `adopt_scope="category"`.
- [P2] [D2/D3] import_namuwiki.py:172,186 — 입력은 '2026년 가요계' 고정인데 연도는 `date.today().year`로 붙인다. 2027년에 register_all.bat을 돌리면 2026 항목이 2027 미래 날짜로 등록된다. 만료 alert도 없다(수동 경로). — 제안: 연도를 인자·헤더로 받고 불일치하면 중단.
- [P3] dry_run_itunes_only.bat·dry_run_melon_only.bat이 argparse에 없는 `--skip-transition`을 넘겨 즉시 종료된다(동작하지 않는 bat). mapping_failures에 매 run 중복 INSERT가 쌓이고 Deezer를 재조회한다. `_p()`가 escape하지 않는다. "Deezer 팬: N" 본문(출처 노출 여부 판단 필요). naive `date.today()`, MB `utcnow()`(KST 0~9시 하루 어긋남). 미사용 `DALWIKI_API`, README 없음.
- 확인됨: A1 OK(MB 제외. 과거·오늘·iTunes·나무위키 행은 ABSENT_SOURCES 밖). A2 OK(하나라도 실패·0건이면 source_complete=False). A3/A6 OK. A5 OK('removed' 표시). A7·A8 OK. B4·B5 OK. C1/C2 OK(`*_dryrun.db` 사본). D1 OK. E1~E3 OK. G3 OK.

---

## 5. 기타 A (naehanbot · figurebot · gamepassbot · gamingschedulebot)

**gamepassbot·gamingschedulebot은 실제 bat 실행 경로에서 SyncEngine을 쓰지 않는다.** 둘 다 폴더 안 자체 래퍼(gamepassbot `dalwiki.py`, gamingschedulebot `common/dalwiki_client.py`)가 공용 `dalwiki_client.create_event/update_event`를 직접 호출한다(규칙 2 위반). raw requests는 없고 삭제 경로도 없다(추가·patch 전용). 그래서 A1~A3 삭제 사고는 구조상 일어나지 않는 대신, "바뀐 데이터가 옛 이벤트로 남는" 문제와 조용한 실패가 생긴다.

### gamingschedulebot (freegame/main.py, freegame/sources/*, common/*)
- 실행 경로: weekly `run_freegame.bat` → `python -m freegame.main` → epic/gog/psplus/stove → `_register_pair` → 자체 래퍼. 토픽 `kdWPdGk04_gH`. 상태 `data/seen.db`(PK source, external_id).
- [P0] [D1/D3] freegame/sources/psplus.py:35-50, 136-141 — **PS Plus 라인업 월이 한 달 밀려 등록됐다(실데이터).** 월을 "게시일(UTC→KST) 다음 달"로 정한다(`base = …astimezone(KST)` → `month = now.month + 1`). 발표가 KST로 라인업 시작월에 걸리면 그 달 라인업이 다음 달로 간다. seen.db를 읽기 전용으로 확인한 결과:
  - `psplus:2026-08:*`에 **두 라인업**이 같이 들어 있다. 07-06 생성분(CoD MW3 크로스젠, For the King II, CrossCode)과 08-03 생성분(Dying Light 2, Big Walk, Signalis)이 모두 start 2026-08-04 KST다.
  - `psplus:2026-11:*`(F1 25, Hunt: Showdown 1896, EDF)는 10-05 생성인데 start가 11-03 KST다.
  - **2026-07·2026-10 키는 없다.** 7월·10월 라인업이 실제 달에 비어 있고, 다음 달 라인업과 겹친다.
  - 제안: start = 게시 시각(KST) 이후 첫 화요일. 잘못 들어간 2026-08(07-06 생성 3건 × 시작/종료)·2026-11 psplus 이벤트를 데이터 청소 대상으로 올린다.
- [P1→P0에 포함] [D1] psplus.py:44-49 — end = `_first_monday(다음 달) - 1일`이다. 다음 달 1일이 화요일이면 종료일이 다음 라인업 시작보다 6일 늦다. 실제 2026-08 키 end가 09-06 KST인데 9월 라인업은 09-01에 시작했다. docstring과 코드도 다르다. — 제안: end = 다음 라인업 시작 − 1일.
- [P0] [B2/B1] psplus.py:64, 91-105 — LLM 실패를 `except Exception as e: log.error("Claude extraction failed: %s", e); return []`로 삼킨다. 월 1회 글을 주 1회 실행이 보는 기회는 사실상 1~2번이다(로그: 10-05 `3 candidates`, 10-12·10-19 `No PS Plus monthly post found`). 그 run에서 실패하면 **그 달 라인업이 영구 누락**되고 alert도 없다. — 제안: `botkit.llm.llm_json(HAIKU)` + LLMUnavailable이면 alert와 비정상 종료. 이번 달 키가 없으면 아카이브를 재탐색.
- [P1] [B1/A2] main.py:134-154, epic.py:56-58, gog.py:51-53, stove.py:91-99 — 소스 실패가 전부 `log.error` 후 `[]`/`break`다. **봇 전체에 HEALTH-ALERT와 비정상 exit가 없다**(grep `alert|HEALTH|botkit|sys.exit` 0건). STOVE는 중간 페이지가 실패하면 부분 결과만 처리한다. — 제안: 소스별 0건·실패면 `botkit.run.alert`.
- [P1] [A4 유사] main.py:90-98 + common/dalwiki_client.py:145-178 — seen에 없는 항목은 ±48h 안에서 정규화 **부분문자열**(`norm_hint in n2`, 4자 이상)로 기존 이벤트를 흡수하고 그 id를 seen에 묶는다. 카테고리·플랫폼 조건이 없다. 이후 날짜가 바뀌면 흡수한 **남의 이벤트를 update_event로 덮어쓴다**(예: "Hades"와 "Hades II 시작일"). 실발생은 미확인. → gamepassbot과 통합 이슈.
- [P2] [규칙2/C1] main.py:12, 79-104 — 자체 쓰기 경로다. config.py:6 `load_dotenv(override=True)`라서 .env에 DRY_RUN 키가 생기면 bat의 `set DRY_RUN=1`을 덮는다(현재 키 없음).
- [P2] [A7] epic.py:86, stove.py:133 — ext_id에 시작 시각이 들어간다(`epic:{id}:{start}`). 예고 시각이 바뀌면 새 쌍이 생기고 옛 쌍이 남는다. psplus key의 LLM 제목도 같다.
- [P2] [D1] gog.py:70-73 — `end_date` "N/A"이면 시작+7일로 **종료일을 지어낸다**. `published_date` N/A면 `now()`라 run마다 PATCH된다.
- [P2] [E2] main.py:72·100-104 — 상점 URL이 description에만 있고 link 필드가 비어 있다.
- [P2 기존] config.py:30 `CLAUDE_MODEL = "claude-haiku-4-5-20251001"` 하드코딩(.60). 퇴역하면 위 P0 경로로 빠진다.
- [P3] Epic 종료 −1초가 서머타임 해제 후 KST 날짜를 하루 민다. 날짜별 로그가 쌓이기만 한다. 미사용 `DALWIKI_API`.
- 확인됨: A5 OK(404면 재생성하지 않음). D1 allDay OK. E1·E3 OK. G1 OK. F1·F3·F4 OK.

### gamepassbot (xbox_gamepass_bot.py, crawler.py, parser.py, dalwiki.py, db.py)
- 실행 경로: `run.bat`/`매일_run.bat` → `xbox_gamepass_bot.py --mode run` → RSS catchup → BS4(→ Haiku 폴백) → 자체 `dalwiki.py` → `create_event`. 생성만 한다(update/delete 없음). 토픽은 .env(단독).
- [P1] [A7 유사] xbox_gamepass_bot.py:58-66, 74, 82 — dedup 키가 `post_url|title|type|start_date`라서, 후속 글에서 날짜가 바뀌면(연기·coming soon 확정) **새 이벤트를 만들고 옛 날짜 이벤트가 그대로 남는다.** — 제안: 키를 (title, type)으로 하고 날짜 변경은 PATCH.
- [P1] [A4 유사] dalwiki.py:78-100, xbox_gamepass_bot.py:101-115 — ±48h 부분문자열 흡수로 "Doom"과 "Doom: The Dark Ages"가 이틀 안에 있으면 뒤 게임이 **영원히 생성되지 않는다**(dedup_key에 흡수 id가 영구 기록됨). event_type·카테고리 비교도 없다. → 통합 이슈.
- [P1] [D3] parser.py:157-161, 90, 197 + 프롬프트 :352 — 연도 없는 "Month Day"에 **글 URL의 연도**를 붙인다. 12월 웨이브 2 글의 "January 6"이 지난해 1월이 된다(매년 12월에 흔함). — 제안: 글 월=12 & 날짜 월 1~2면 +1.
- [P2] [규칙2] 자체 래퍼 쓰기(W6 .46 패치에서 유지됨).
- [P2] [E2] dalwiki.py:59-64 — 원본 글 URL을 본문 앵커와 link 필드에 **둘 다** 넣는다(규칙 3).
- [P2] [B1] parser.py:427-434 — BS4가 1건만 찾아도(`min_games=1`) LLM 보정 없이 처리 완료로 마킹한다. 구조가 바뀌면 나머지 게임이 영구 누락된다.
- [P3] 미국 날짜를 그대로 KST 종일로 쓴다(표기 기준 확인 필요). 날짜별 로그 174개가 정리 없이 쌓인다. README 없음. `매일_run.bat` 중복.
- 확인됨: B2 OK(LLM 실패면 "llm-failed" 보류 + alert, parser.py:383-391). B4 OK. C1/C2 OK(dryrun은 register를 호출하지 않음, db 쓰기 DRY 가드). A2 OK. D1 allDay OK. E1·E3·G1·F1 OK. 모델 HAIKU 상수.

### figurebot (bot.py, store.py, translator.py, scrapers/*)
- 실행 경로: daily `run_scrape.bat`(--scrape-only), weekly `run_translate.bat`·`run_delays.bat`. 셋 다 `state/figurebot.db` → `SyncEngine(N55OnVjoGg7N, absent="keep", on_missing="ignore", protect_human_edits)` run당 1회.
- [P2] [C2] bot.py:159-164 + store.py:131-155 — dry-run에서도 `upsert_release`는 products에 새 날짜를 저장하는데, 재판 보관 key 이동은 dry-run이라 건너뛴다. 다음 실제 run에서 `classify_change`가 "same"이 되어 재판을 감지하지 못한다. 그래서 옛 발매 이벤트가 `@옛날짜`로 보관되지 않고 새 날짜로 PATCH되어 **옛 발매일 기록이 사라진다**(재판 첫날 수동 dry-run이 먼저 돈 경우). — 제안: dry-run이면 Store를 임시 사본으로.
- [P2] [G1] bot.py:111-129, store.py:79-88·161-173 — 주간 `--check-delays`는 날짜를 뒤로만 반영하는데, 매일 스크랩은 출하 안내 날짜로 덮어쓴다. 그래서 역방향 `(1월→11월)` 라벨 PATCH가 주 단위로 오갈 수 있다. goodsmile 공지 정규식(goodsmile.py:133-135, DOTALL `.*?`)은 무관한 연월을 잡을 수 있다. 아직 발생하지 않았다(10-19 반영 0건).
- [P3] `--dry-run --translate`도 실제 LLM 호출 + name_cache를 저장한다. 예약오픈/종료 summary가 발매와 같다(일정명은 카테고리로만 구분). `.env`의 미사용 `DISCORD_BOT_TOKEN`(회수 권장). 세 bat이 같은 DB를 쓰는데 락이 없다(시각은 분리됨). vertex 상품 fetch 실패가 debug 로그뿐. 알려진 한계: JAN 없는 제조사는 원명 해시 key라 마크업이 바뀌면 대량 중복 위험(README).
- 확인됨: A1~A3·A5·A6·A8 OK(keep). B2 OK(LLMUnavailable이면 번역만 보류 + alert). B4 OK. D1~D3 OK(JST=KST, 연도는 페이지 헤더). E2·E3 OK. G1 OK(unchanged 985/1009). G3·F2·F3 OK.

### naehanbot (bot.py, fetcher.py, parser.py)
- 실행 경로: daily `run.bat` → `bot.py` → 올해·내년 페이지(12h 캐시) → `SyncEngine(8yjCWNnLFgVj, absent="delete", cache=strict)` + 10% 가드 → `sync(source_window=(올해 1/1, 내년+1 1/1))`. adopt_scope=topic(엔진 §0).
- [P1] [A1] bot.py:223-229, 263-264 — 창은 내년 전체를 덮는데, **내년 페이지가 404면 complete=True인 채 건너뛴다**(`if year == today.year: complete = False … else: log.info('페이지 없음 → 스킵')`). 내년 이벤트가 state에 들어간 뒤 그 페이지가 404가 되면(이동·개명·일시 404) 내년분이 전부 absent로 삭제된다. 10% 가드는 managed 전체(약 560건) 기준이라 약 56건 이하이면 통과하고, 연초 내년분은 수십 건 이하다. — 제안: state에 그 연도 행이 있으면 404를 complete=False + alert로 처리하거나, 창 상한을 실제로 받은 연도까지로 줄인다.
- [P2] [D3] bot.py:69-76, parser.py:56-73·200 — 다날짜 셀이 해를 넘기면("12월 31일, 1월 1일") 1/1이 같은 해(과거)로 등록된다. — 제안: 4분기 행에서 월이 감소하면 +1.
- [P3] 같은 공연명·날짜 두 번째 행은 `::아티스트` 접미가 순서에 의존한다. README 없음. `run.bat %*`로 `--cache-only`를 넘기면 위 P1 경로가 열린다.
- 확인됨: A1(올해) OK. A2 OK(FetchError·올해 404·0행이면 complete=False + alert, fetcher는 비200·50KB 미만이면 FetchError). A3~A6·A8 OK. B5 OK. C1/C2 OK(임시 사본). D1 OK. E1~E4 OK. G1(unchanged 560)·G3 OK.

---

## 6. 기타 B (onlinegamebot · runbot · doujin_bot · museum_bot)

### runbot (main.py, dalwiki.py, db.py, merger.py, utils.py, title_formatter.py, scrapers/*)
- 실행 경로: `run.bat`·`매일_4시_자동실행.bat` → `main.py scan` → 자체 `sync()` → `runbot/dalwiki.py` `DalwikiClient` → 패키지 create/update/delete. `resync_all.bat` → `resync-all`. **SyncEngine 미사용**(저장소 grep 0건). 상태는 `races.db`. 토픽 `MrblvLt48lWt`. 정기 scan에는 소멸 삭제가 없고, '소스 ID 연결 중복'(extras)만 지운다.
- [P0] [A7/A8] main.py:200-207, 317, 338-345 — **소스 글번호 재사용 때문에 지난 대회 이벤트 삭제가 예정돼 있다(실데이터 확인).** linked = 소스 ID가 같은 옛 DB 행 − 이번 records이고, 그 행의 이벤트는 `extras`로 `client.delete_event(x)` + `db.delete_merged(k)`된다. 제목·날짜는 보지 않는다. records는 `r["date"] >= floor`(오늘-30일)로 걸러진다(main.py:127).
  - races.db(읽기 전용): `f1c2b26906c896b3` 서울 대모-구룡산트레일런(2026-10-24, event `3-6p2EGnu_Os`)과 `1768d5094f7fc540` 대전보문산트레일런(2026-12-06, `YcsvpEWpG1FI`)의 sources_json이 둘 다 `roadrun/41642`다.
  - **2026-11-24 이후 첫 scan에서** 대모-구룡산이 floor 밖으로 나가 rec_keys에서 빠진다. 그러면 대전보문산의 linked에 들어가 그 이벤트가 '중복 삭제'되고 DB 행도 사라진다. 주석(:204-206)은 둘 다 records에 있을 때만 막는다.
  - 제안: linked 행이 floor 밖이면 연결하지 않거나 이름·날짜 근접 조건을 건다. 응급으로는 f1c2 행 sources_json에서 roadrun 41642를 뗀다.
- [P0] [B4] dalwiki.py:105-129 — **PATCH가 404가 아닌 이유로 실패해도 새로 POST한다**(`if ok: return event_id` / `if sc == 404 and self._cache: …` 뒤 무조건 `return _create_event(...)`). 5xx·403·429 소진·네트워크 실패 뒤 create가 성공하면 `set_dalwiki_id`가 덮어써 옛 이벤트가 고아 중복으로 남는다. 공용 `update_or_create_event`(client.py:521)는 `if sc != 404: return None`인데 runbot 래퍼만 다르다. `resync_all.bat`을 장애 중에 돌리면 대량 중복이 날 수 있다. — 제안: `sc != 404`면 None(실패).
- [P1] [B1] scrapers/marathongo.py:125-127 — 번들 fetch 예외가 `log.error` + `return []`이고 alert가 없다(gorunning·roadrun은 .27에서 추가됨). `.text`만 읽고 status를 확인하지 않는다. DB 스냅샷 보충 때문에 겉으로는 정상으로 보인다.
- [P2] [규칙2] 자체 래퍼·자체 판정 구조. 위 P0 두 건이 이 구조에서 나왔다. SyncEngine 이식 필요.
- [P2] [A5] dalwiki.py:109-129 + main.py:333 — 사람이 지운 이벤트가 내용 변경 시 재생성된다. 404 복구 캐시 키도 형식이 불일치한다(`%H:%M:%SZ` vs 서버 `.000Z`, utils.py:100 ↔ client.py:196-197). 그래서 v2 id 복구는 사실상 죽은 코드다.
- [P2] [B4] main.py:338-345 — extras 삭제가 실패해도 그 아래 `db.delete_merged(k)`가 linked 행을 지운다 → 영구 고아.
- [P2] [G1] scrapers/gorunning.py:196-199 — 상세 실패 시 조용히 return한다. 정보가 줄어든 채 PATCH되고 다음 run에 원복 PATCH된다.
- [P3] dry-run에서도 `upsert_raw`가 실행된다(로컬). merged_races가 계속 커진다(.62). resync_all.bat에 비ASCII echo가 있고 track이 없다. README 없음. `load_cache` 미사용, gorunning 상세 176건 전수 조회.
- 확인됨: A1 해당 없음(floor 30일 이전은 손대지 않음). A2 OK(실패 소스는 DB 스냅샷으로 보충, 삭제로 이어지지 않음). A3/A6 OK. D1~D3 OK. E1~E4 OK. B6·F1 OK.

### onlinegamebot (bot.py, core/*, sources/api·crawl 9개)
- 실행 경로: `run.bat` → `bot.py --days=3`(`backfill.bat`는 --days=30). 토픽 10개(게임 9 + 통합 `eznOF4IYU9ox`)에 엔진 10개, 공용 `state/state.db`. **전부 absent=keep**, protect_human_edits.
- [P1] [B1/A2] sources/api/lostark.py:64, 67 + bot.py:51 — 실패 감지가 로그 문구 "실패"에 의존하는데(`if "실패" in record.getMessage()`), lostark 실패 로그에는 그 단어가 없다(`log.error("Lost Ark %s %d: %s", …)`). 그래서 JWT가 만료되거나 네트워크 예외로 0건이 돼도 nfail=0, complete=True이고, 0건 alert도 나가지 않는다. 삭제는 없고 조용한 누락이다. — 제안: 소스가 (items, ok)를 반환.
- [P1] [운영] logs/onlinegamebot.log 2026-10-22 11:15 — 메이플 Open API 4개 엔드포인트가 전부 `400 Bad Request`, `maplestory fetched 0`(10-21은 17건)이다. HEALTH-ALERT는 정상 발송됐다. 원인(키 만료/파라미터 변경)은 미확인 — 곧 중단될 항목이라 등록한다.
- [P2] [B1] sources/crawl/pubg.py:81-93 — 목록 정규식 매치가 0이어도 cutoff 밖 0건과 구분되지 않는다. pubg가 10-17~10-22 6일 연속 0건이고 valorant도 10-22에 0건이다(실제 공지 없음 여부는 미확인). — 제안: 파싱 전 매치 수를 로그로 남기고, 0이면 alert(`_riot_next`도).
- [P2] [B2] core/classifier.py:170-173 + bot.py:225 — LLMUnavailable 보류 자체는 맞다. 하지만 창이 `--days=3`이라 장애가 3일을 넘기면 보류분이 창 밖으로 밀려 영구 누락된다. ParseError 반복에도 alert가 없다. — 제안: 보류 key를 저장해 창과 무관하게 재시도하거나, alert에 backfill 안내를 넣는다.
- [P3] confidence high + start==end timed. dnf `sha1(title)` 폴백 key. `_infer_time_from_title` 죽은 코드.
- 확인됨: A1~A6 OK(keep, 토픽별 1회, 게임 프리픽스 key). A7 OK(lostark 링크 경로 교정, TFT 시작 고정). A8 OK. C1/C2 OK. D1·D3 OK. E2·E3 OK(통합 summary `{유형} - {제목}`은 README 한계). F1·F2 OK.

### doujin_bot (bot.py, category.py, config.py)
- 실행 경로: `run.bat` → `bot.py` → `SyncEngine("akEt1KVzmve7", absent="delete")`, 창 [이번 달 1일, 마지막 스캔 달 다음 달 1일) = 스캔 6개월과 일치(:279-287). adopt_scope=topic(**엔진 §0**: comicw가 같은 제목·시작일로 새 id 재등록 시).
- [P2] [A2] bot.py:63-78, 233-239 — 월 목록이 200인데 `/c/` 링크가 0개면 그 달을 '성공 0건'으로 친다. 6개월 중 일부만 그러면 complete=True가 되어 그 달 시작 행이 전부 삭제된다(전부 비면 `not ids` 가드가 막음). 실관측은 없다. — 제안: state 행이 있는 달이 0건이면 complete=False.
- [P3] 상세 일정이 창 밖으로 연기되면 삭제 후 재생성. dry-run `import_legacy_state`(이관 완료).
- 확인됨: A1 OK. A2 OK(목록·상세 실패 1건이라도 complete=False + alert). A3·A5~A8 OK. D1~D3 OK(12→1 롤오버 :59). E1~E4 OK. F1·F3 OK.

### museum_bot (bot.py, sources.py, config.py)
- 실행 경로: `run_update.bat` → `bot.py` → `SyncEngine(ZDF49ygIhO15, absent="delete", cache ±400일)`, source_window 없음, `absent_filter=vanished`(라이브 end ≥ 오늘 && start ≤ 오늘+30일, :221-228). 소스 3개는 (items, ok).
- [P0 → 엔진 §0] bot.py:116-128, 216, 228 — 봇 자체 `dedupe`가 같은 (summary, start)에서 순위 높은 key만 남긴다. 진 key가 state에 있고 이긴 key가 NEW이면 입양 후 삭제가 일어나 영구 소실된다. 조건: (a) 국립중앙 통합 피드(rank 1)로 먼저 등록된 전시가 나중에 지역 국립박물관 피드(rank 0)에 나타날 때(시차 여부는 미확인), (b) KCISA LOCAL_ID 재발급. — 봇 쪽 제안: dedupe에서 기존 state key 우선 + `adopt_scope="category"`.
- [P3] 봇 필터가 버린 전시(시작 연기 >30일, 종료 앞당김, 365일 초과 연장)도 삭제 후보다. PKM 'December 5 – January 20, 2027'이면 s>e → None. SeMA 키가 http URL 경로에 평문으로 들어간다(로그는 redact). dry-run `import_legacy_state`.
- 확인됨: A1 OK(KCISA 9648 < 50쪽×1000, SeMA 880 < 20쪽×500, 종료·+30일 밖은 보호). A2 OK(소스별 ok, resultCode·페이지·0건). A3·A5~A7 OK. D1·D2 OK. E1~E4 OK. F1 OK. G1 OK.

---

## 7. 그룹 공통 패턴

1. **엔진 topic 입양 + 같은 run absent 삭제 = 영구 소실 (§0, P0).** absent=delete 봇 12개가 기본 adopt_scope 위에 있다. 트리거는 세 가지다. ① 소스가 같은 일정을 새 id로 재발급(berriz·timetree·mnet·festival·doujin·museum). ② key 체계 변경(netflixbox imdb 늦게 붙음, album_bot 이중 행). ③ 봇 자체 dedupe(museum). 엔진 한 곳(`_lookup` topic 모드에 `exclude=_claimed`, 또는 `_delete`가 이번 run에 저장된 id를 건너뜀)만 고치면 전부 막힌다.
2. **"일부만 실패/비었는데 complete=True" → 창 안 삭제.** festivalbot TourAPI 플래핑(실증), laftelbot expire 98% 허용·편성공지 무시, netflixbox TMDB <5건, naehanbot 내년 404, doujin 월 0링크, kdramabot TMDB 빈 200·채널 누락, animebot 부분 파싱. 좋은 선례로는 dramabot `/tv/{id}` 재확인, netflixbox end| 단건 재조회, berriz·mnet "달 하나라도 실패하면 False"가 있다. 처방은 (a) 실패·누락 항목 key를 protected/held에 넣고, (b) absent 후보를 '이번에 실제로 조회된 단위'로 한정하고, (c) 삭제 전에 단건 재확인을 하는 것이다.
3. **SyncEngine 밖 자체 쓰기 5봇(runbot·gamepassbot·gamingschedulebot·moviebot·animebot).** 이번 P0 4건 중 3건(runbot 2, gamingschedulebot psplus LLM)과 P1 다수가 여기서 나왔다. 공통 증상은 비404 POST 폴백(runbot), 사람 삭제 재생성(animebot·moviebot·runbot), ±48h 부분문자열 흡수(gamepass·gaming 복붙), HEALTH-ALERT 부재(gaming), TopicCache 실패 무시(moviebot)다. W2식 재구축 대상으로 묶을 것을 권장한다.
4. **시크릿이 예외 문자열로 샌다.** TMDB `api_key` 쿼리(netflixbox·dramabot·kdramabot)가 requests 예외 메시지 → 로그 → (dramabot은) HEALTH-ALERT → Discord로 나갈 수 있다. 옛 netflixbot.log에는 실노출 5줄이 있다(미추적 파일). festivalbot serviceKey는 앞 9~12자가 노출된다. 처방은 v4 Bearer 헤더, 또는 예외를 status·type 이름으로만 기록하는 것.
5. **연도·월 경계와 미국→KST 변환.** PS Plus 월 밀림(실데이터 사고), gamepass 글 연도 고정(12→1월), laftel 앞으로만 롤오버, naehan 4분기 다날짜, album 나무위키 연도, 그리고 종일 기간 일정 첫날 절단(berriz·timetree·qwerbot). 공통 처방: 연도 생략 날짜는 "기준일에 가장 가까운 연도"로 해석하고(dcbot `resolve_date`가 선례), 종일이면 end 날짜를 존중한다.

## 8. beads 등록

에픽 `dalbots-bmi` 자식, 제목 `[W7] <봇>: …`. P0/P1은 건별, P2는 봇별 묶음이다. 기존 이슈와 겹치는 항목은 새로 만들지 않고 코멘트했다(.57 moviebot, .58 kdrama/drama, .59 album_bot MB, .60 gamingschedulebot, .62 animebot, .68 엔진 topic exclude → .104로 격상).

| 심각도 | id | 요약 |
|---|---|---|
| P0 | .104 | botkit.sync: topic 입양 id를 같은 run absent가 삭제 → 영구 소실 (§0, 12봇) |
| P0 | .105 | gamingschedulebot: PS Plus 라인업 월 한 달 밀림(실데이터) + 종료일 |
| P0 | .106 | gamingschedulebot: PS Plus LLM 실패 [] 삼킴 → 월 라인업 영구 누락 |
| P0 | .107 | runbot: roadrun 41642 재사용 → 11-24 이후 대모-구룡산 삭제 예정 |
| P0 | .108 | runbot: 비404 PATCH 실패 시 POST → 고아 중복 |
| P1 | .109 | festivalbot: TourAPI 플래핑 삭제·재생성 |
| P1 | .110 / .111 | mnetplusbot: 리센느 단방향 중복 / 입양한 타봇 이벤트 삭제 |
| P1 | .112 / .113 | animebot: 부분 파싱 삭제 / 분류 1쪽 |
| P1 | .114 / .115 | laftelbot: expire 98% / 편성공지 무시 |
| P1 | .116 | netflixbox: TMDB <5건 실패 complete True |
| P1 | .117 | netflixbox·dramabot·kdramabot: TMDB api_key 로그·alert 노출 |
| P1 | .118 | dramabot: discover dedupe 없음 → key 중복 ValueError |
| P1 | .119 | moviebot: 무경보 [] + TopicCache 실패 무시 |
| P1 | .120 | naehanbot: 내년 404 complete True |
| P1 | .121 / .123 | gamepassbot: 키에 날짜 / 12월 글 연도 |
| P1 | .122 | gamepassbot·gamingschedulebot: ±48h 부분문자열 흡수 |
| P1 | .124 | gamingschedulebot: HEALTH-ALERT 전무 |
| P1 | .125 | runbot: marathongo 무경보 |
| P1 | .126 / .127 | onlinegamebot: lostark 실패 감지 / 메이플 400 |
| P1 | .59 코멘트 | album_bot: MB limit=100 (Metacritic 복구 시 P0) |
| P2 | .128~.148 | 봇별 묶음(gacha .128, qwer .129, collab .130, dcbot .131, berriz·timetree .132, mnet .133, festival .134, anime .135, laftel .136, netflix .137, drama .138, kdrama .139, movie .140, album .141, naehan .142, figure .143, gamepass .144, gaming .145, runbot .146, onlinegame .147, doujin .148) |
