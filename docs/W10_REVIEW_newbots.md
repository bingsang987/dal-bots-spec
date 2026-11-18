# W10 리뷰 — 신규 8봇 + 기존 4봇 토픽 라우팅 diff

읽기 전용 감사(2026-11-18). 기준: `W7_REVIEW_CHECKLIST.md`(A~G), `W2_REBUILD_BRIEF.md`, `W8_PLAN.md` 5·6절, `NEW_BOT_BRIEF.md`, `handoff_topics_2026-10-29.yaml`, `src/botkit/sync.py`. 파일 수정·봇 실행·dal.wiki 쓰기 없음. state DB는 sqlite 읽기, 로그 읽기만. dcpostbot·dcapibot은 범위 밖(읽지 않음).

체크리스트 변경 1건 반영: dal.wiki `link` 필드는 프론트에 안 보이는 안 쓰는 필드 → "link 누락"은 지적하지 않고, 사용자에게 필요한 링크가 `link`에만 있고 본문에 없을 때만 P3. 본문 '출처:' 블록 금지(E2)는 그대로.

## 요약

| 봇 | P0 | P1 | P2 | P3 | beads |
|---|---|---|---|---|---|
| kbocanceltickbot | 0 | 1 | 1 | 5 | .235 .241 |
| mlbplayersbot | 0 | 0 | 1 | 3 | .242 |
| bookreleasebot | 0 | 1 | 0 | 3 | .236 |
| legobot | 0 | 0 | 1 | 0 | .243 |
| ipgoodsbot | 0 | 1 | 1 | 4 | .237 .244 |
| ipnewsbot | 0 | 1 | 1 | 3 | .238 .245 |
| premierebot | 0 | 0 | 1 | 4 | .246 (dalbots-jzz 관련) |
| chzzkarchivebot | 0 | 2 | 1 | 4 | .239 .240 .247 |
| convinibot (diff) | 0 | 0 | 1 | 0 | .248 |
| collabcafebot (diff) | 0 | 0 | 1 | 0 | .249 |
| koreansoccerguysbot (diff) | 0 | 0 | 1 | 0 | .250 |
| animebot (diff) | 0 | 0 | 0 | 3 | — |
| **합계** | **0** | **6** | **10** | **29** | 16건 (`dalbots-bmi.235`~`.250`) |

beads: P1은 건별, P2는 봇별 묶음(W7 관례). P3는 보고서에만.

**P0 없음.** 지금 실제로 지워지고 있는 데이터는 찾지 못했다. P1은 전부 "소스가 부분적으로 깨지면 엔진이 그걸 완전한 소스로 보고 지운다/조용히 잃는다" 형태다.

## 엔진 의미 정리 (리뷰 기준, `src/botkit/sync.py`)
- state는 sqlite에 (topic_id, key)로 분리 → 한 봇의 absent 판정은 자기 state 행만 건드린다. 공유 토픽에서 **다른 봇 행을 직접 지우는 A3는 구조상 없음**.
- absent 후보 = 그 토픽 state 행 중 이번 events에 없는 것 → `source_window`(start 기준)·`absent_filter`로 거름. 보류 조건: events=0이고 `allow_empty` 아님(source=0), `source_complete=False`, 후보 > max(10, 0.3 × 범위 안 행 수)(mass_absent) (sync.py:761-775).
- `absent="keep"` 엔진은 후보가 있어도 source=0/incomplete 판정 전에 빠진다(:763-765) → **keep 엔진은 소스 실패로 HEALTH-ALERT를 절대 내지 않는다**.
- `_alert_absent`는 보류됐을 때만 alert, 정상 삭제는 로그만(:802-813, 관제탑 결정 2026-10-22) → 가드 한도(최소 10) 아래의 부분 손실은 무경보 삭제.
- 입양(NEW 경로 `_lookup`): `adopt_scope="topic"`이면 토픽 안 (summary, start) 일치, `"category"`면 카테고리까지 일치. 이번 run 자기 key가 쥔 id만 제외되고 **다른 봇의 state는 모름** → 공유 토픽에서 봇 A가 봇 B의 일정을 입양할 수 있다(A4). 이후 B가 소스에서 빠져 지우면 A는 on_missing=ignore로 영구히 놓친다.
- `botkit.run` mask_secrets는 `key=`류 쿼리 패턴만 가린다. URL 경로에 든 토큰(디스코드 웹훅)은 안 가린다.

---

## 1부 — 신규 8봇

### kbocanceltickbot (bot.py, config.py, openings.yaml)
A 추적: 토픽 `rtWGH1oRAKc4` 단독. `SyncEngine(TOPIC_ID, absent="delete", alert_absent=complete)`(bot.py:300), `sync(events, source_complete=complete, source_window=(now, hi_day+1일 00:00), allow_empty=complete)`(:301) run당 1회. 입력은 KBObot이 쓴 KBO 토픽 `vvSHkOrwL1Rh`를 list_events로 읽음 — ListFailed면 alert 후 `return 0`(:263-265). complete를 내리는 경로는 openings.yaml 파싱 실패(:268-272)와 RULES_SEASON 불일치(:274-277) 두 개뿐. key=`KBO|날짜|원정홈|dh|K1/K1p`(:166).
- [P1] [B1/A2] bot.py:91-96, 267, 301 — KBO summary 정규식 해석 실패는 warning+skip만 하고 complete에 안 들어감. KBObot이 summary 형식을 바꾸면 games=0 → events=0 → `allow_empty=True`라 창 안 state 행 전부 absent 삭제(10행 이하면 대량 가드도 통과, 정상 삭제는 무경보) — `log.warning(f"KBO 경기 summary 해석 불가 → 건너뜀: ...")` / `stats = engine.sync(events, source_complete=complete, source_window=window, allow_empty=complete)` — 해석 불가 1건 이상이면 `complete=False`+alert. (.235)
- [P2] [B1] bot.py:301 — `allow_empty=complete`라 시즌 중(3~10월) 입력 토픽이 비어도(KBObot 장애·토픽 비움) 정상 0으로 삭제. 비시즌엔 맞는 동작 — `allow_empty=complete` — 시즌 기간이면 events=0을 불완전으로. (.241)
- [P3] [D2] bot.py:274-277 — RULES_SEASON=2026 가드는 좋다(alert+complete=False). 다만 2027년 3월부터 갱신 전까지 매 run alert 반복.
- [P3] [A7] bot.py:109, 166 — 더블헤더 한 경기가 취소돼 dh가 1→0으로 바뀌면 key가 바뀜(삭제+생성). 실제 KBO summary의 DH 표기 변화 여부 미확인.
- [P3] [C1] openings.yaml — 날짜를 따옴표 없이 쓰면 YAML이 date로 읽어 00:00이 됨(파서가 받는지 실제 값으로는 미확인). 예외는 ValueError/TypeError/YAMLError로 잡혀 complete=False — OK.
- [P3] [B1] bot.py:263-265 — ListFailed 시 재시도 없이 `return 0`(alert는 함).
- [P3] [G] GitHub 원격 저장소 미생성(형제 세션 인계 노트 `kbocanceltickbot_build_handover.md`).

### mlbplayersbot (bot.py, config.py, sources_espn.py)
A 추적: 선수 토픽마다 `SyncEngine(tid, absent="delete")`(bot.py:161), `sync(events, source_complete=not err, source_window=(lo, hi), absent_filter=key not in tbd)`(:171-172). events=0이면 sync 생략, 창 안 state 행이 남아 있으면 alert(:164-169). FA(소속팀 없음)는 경기 0·보류(:91-92). 시즌 집합 `{lo.year, today.year, hi.year, today.year+1}`(:124) — 연도 하드코딩 없음, 시즌 경계 D3 OK. 로그: 11월 매일 "시즌 387경기, 창 안 0경기", 2026/2027 조회 정상, 오류 없음 → 비시즌 0경기 경로 정상(D2 OK).
- [P2] [B1] bot.py:88-90 — ESPN 선수 조회 실패 시 config 팀으로 대체하면서 err를 비워 complete=True. 트레이드 후 config가 낡았으면 옛 팀 경기 생성·새 팀 경기 삭제(대량 가드는 일부만 막음) — `if not ok: team_id = p["espn_team_id"]` — 대체 경로에서 err 설정. (.242)
- [P3] [E] bot.py:75 — `link=g.link`(ESPN 경기 페이지)가 link 필드에만 있고 본문엔 없음.
- [P3] [B1] "소속팀 변경" alert가 config 갱신 전까지 매일 반복.
- [P3] [B1] bot.py:164-167 — 창 안 state 행이 남은 채 0경기면 매 run alert(행이 창 밖으로 나갈 때까지).

### bookreleasebot (bot.py, config.py, sources.py; survey/는 실행 경로 밖)
A 추적: 종류(만화/라이트노벨)별 엔진, state/sync.db, `absent="keep" if --back else "delete"`, `sync(source_complete=complete, source_window=(today-back, today+91))`(bot.py:332-334). complete=False는 상세 실패 > max(5, 10%)일 때만(:268-270). 이벤트는 현재 목록이 아니라 books.db 캐시 전체에서 만든다.
- [P1] [B1/A2] sources.py:108-111, bot.py:262, 128-130 — 상세 페이지가 제목은 읽히고 발행일·ISBN만 안 읽혀도(HTML 문구 변경) 실패로 세지 않고, `save_book`이 이전 정상 pubdate를 None으로 덮음 → 그 책은 "발행일·ISBN 없음"으로 제외 → absent 삭제. 실패 집계는 SourceError만 셈 — `b.pubdate = f"{m[1]}-{m[2]}-{m[3]}" if m else None` / `if not b.get("pubdate") or not b.get("isbn13"): why["발행일·ISBN 없음"] += 1` — 이전 캐시에 있던 필드가 비면 실패로 세고 덮어쓰지 않기. (.236)
- [P3] [A7] bot.py:147-156 — 한정판 묶음 key: `groups[(kind, base.replace(" ",""), vol)]`, 대표=일반판 우선·없으면 가장 이른 것, key=대표 ISBN. 특장판이 먼저 등록되고 일반판이 나중에 나오면 대표가 바뀌어 key 변경. 같은 날짜면 엔진 ADOPT_RELINKED로 이어지지만 날짜가 다르면 삭제+생성. 묶음 키 자체(공백 제거 제목+권수)는 타당.
- [P3] [C1] bot.py:262 — `save_book(..., ctx.dry_run)`의 dry 인자가 쓰이지 않아 dry-run에서도 books.db·교보 캐시·이력이 기록됨.
- [P3] [G2] 발행일 없는 76권이 매 run 상세 재조회.

### legobot (bot.py, config.py, sources.py, store.py)
A 추적: `SyncEngine(adopt_scope="category", alert_absent=complete)`, `sync(source_complete=complete, source_window=(today, +3y))`(bot.py:213-217). complete = 목록 3종 전부 complete이고 신제품·단종예정 목록이 비지 않음(:177). 3종 전부 실패/0건이면 alert 후 종료(:175-176). **단종 판정**: 단종예정 목록에 보인 세트는 retire_first/last 갱신, 목록이 complete이고 이번에 1건 이상 보였을 때 목록에서 빠진 세트에 `retire_gone=today`(:126-130) → "판매 종료 확인" 종일 일정(:147-148). store dry-run은 메모리 사본(OK). 정직한 UA, 2초 간격(OK).
- [P2] [A1/E] bot.py:126-130, 147, 213; sources.py:53 — 단종예정 목록에서 세트가 일시적으로 빠지거나(페이지네이션·재고 표시 변동) pieceCount가 비어 `is_set`이 False가 되면 가짜 "판매 종료 확인" 일정이 생김. 다시 나타나면 `retire_gone=None`으로 이벤트는 소스에서 빠지지만 start가 과거라 창(today~) 밖 → 영구 잔존. "목록 이탈 = 판매 종료" 가정 자체도 미확인(목록이 단순 재정렬·카테고리 이동일 수 있음) — `row.retire_gone = t  # 목록에서 내려감 = 판매 종료 확인` / `return bool(SET_CODE_RE.match(self.code)) and bool(self.pieces)` — 연속 N일 이탈 후 확정, retire 일정은 창 하한을 retire_first까지 내려 정리 가능하게. (.243)

### ipgoodsbot (bot.py, config.py, sources_card.py, sources_store.py, notices.py, http_util.py)
A 추적: 포켓몬 토픽 `BkkG5CbRyCOa`. `SyncEngine(absent="delete", on_missing="ignore", protect_human_edits=True, protect_human_absent=True, adopt_scope="category")`(bot.py:338-340), `sync(source_complete=complete, source_window=(today-30, today+730), absent_filter=key.startswith(("tcg|","store|","resv|")))`(:342-345). complete = `not errors and notice_complete and card is not None and series is not None`(:327). LLMUnavailable → alert + notice_complete=False(:321-323). 공지 1건 조회 예외·None → complete=False(:206-210). 카테고리: 상품 출시 `47NawcWaMoAs`, 예약·응모 `ESD08gsKQ32B` — **ipnewsbot과 같은 두 카테고리**라 category 입양 범위가 겹친다(A4는 공통 패턴 참조; 이 봇 자신은 "타 봇이 이미 가진 것 건너뛰기"가 없음).
A3/A4: state는 자기 key 접두로 absent_filter → 타 봇 행 삭제 없음(A3 OK). 입양 방향은 공통 패턴 2 참조.
- [P1] [B1/B2] notices.py:66-74, 82-85, 98 — 이미지 다운로드가 non-200이거나 5KB 이하면 조용히 버리고, 하나도 없으면 `no_image` + `rounds=[]` 결과를 **영구 캐시**. 일시 장애(CDN 403/5xx) 한 번이면 그 공지의 예약·응모 일정을 영원히 못 만든다(현재 캐시 1건, no_image 0건 — 아직 발생 안 함) — `if r.status_code == 200 and len(r.content) > 5000: out.append(r.content)` / `result = {"product": "", "rounds": [], "notes": [], "no_image": True}` — 이미지 URL이 있었는데 전부 실패면 None(캐시 안 함 → complete=False). (.237)
- [P2] [A1] bot.py:287, 344; sources_card.py:173 — absent 창 하한이 today-30인데 소스는 "목록마다 상위 8개"(CARD_LIST_TOP)·스토어 NEW ARRIVAL 시리즈 = 현재 진열 기준. 지난달 출시가 목록에서 밀려나면 과거 일정 삭제 — `source_window=(_allday(since)[0], ...)` / `if i < config.CARD_LIST_TOP:` — 창 하한=today. (.244)
- [P3] [A7] bot.py:215 — resv key가 LLM rounds 인덱스 기반(캐시라 안정적이지만 재추출 시 순서 바뀌면 key 변경).
- [P3] [A7] bot.py:222-224 — 두 채널 공지 dedup에서 한쪽 공지가 실패하면 승자가 바뀌어 key가 흔들림.
- [P3] [C1] notices.json이 dry-run에서도 기록됨.
- [P3] [문서] config.py:18, bot.py:8 — "같은 토픽 타 봇" 주석에 ipnewsbot(`news|`)이 빠짐.

### ipnewsbot (bot.py, campaigns.py, cluster.py, extract.py, ip_tags.py, store.py, sources/newswire.py, sources/naver_news.py, config.py)
A 추적: IP 토픽(포켓몬·산리오 등)마다 `SyncEngine(absent="delete", on_missing="ignore", cache=, adopt_scope="category", protect_human_edits/absent)`, `sync(source_complete=complete, source_window=(today 00:00, now+400d), allow_empty=complete, absent_filter=key.startswith("news|"))`(bot.py:233-257). complete = `not reasons and not ex.down and not ex.stats["parse_fail"]`(:211). sync 전에 `cache.lookup_all`로 타 봇이 이미 같은 (summary, start)를 가졌으면 자기 이벤트를 버림(:238-246) → **ipnewsbot이 남의 것을 입양하는 방향은 막혀 있음**. 반대 방향(남이 ipnews 것을 입양)은 무방비(공통 패턴 2). state: 포켓몬 10행, 산리오 15행. 최근 run 포켓몬 source=0, 기사 1551 → 묶음 827.
- [P1] [A2] campaigns.py:83-84; bot.py:202-204, 211 — convinibot state.db를 못 열면 `dup()`이 모든 편의점 캠페인에 True → 전부 제외되는데 warning만 하고 complete 유지 → 이미 등록된 편의점 캠페인(convinibot이 안 가진 것)이 absent 삭제. 토픽당 10~15행이라 대량 가드 한도 10 이하로 통과 — `if not self.ok: return True` / `log.warning("convinibot state 를 못 읽음(%s) — 편의점 캠페인은 전부 제외", ...)` — `if not convini.ok: complete = False` + alert. (.238)
- [P2] [A1] config.py:33, 39; bot.py:180 — RETAIN_DAYS=150 < HORIZON_DAYS=365. 시작 150일 넘게 앞서 발표된 캠페인은 기사가 만료되면 묶음이 사라지고 미래 일정이 absent 삭제 — `arts = [a for a in st.articles((today - timedelta(days=config.RETAIN_DAYS)).isoformat())` — 등록된 캠페인의 기사는 시작일까지 보존, 또는 RETAIN ≥ HORIZON. 같은 이슈에 공유 토픽 입양 경쟁(공통 패턴 2)의 ipnews 측을 함께 적음. (.245)
- [P3] [E] extract.py:74, 97 — official_url을 추출하지만 본문에도 link에도 안 씀(사용자에게 가장 유용한 링크).
- [P3] [문서] config.py:4 — "'예약·응모' 카테고리는 이 봇만 쓴다" — 포켓몬은 ipgoodsbot도 같은 `ESD08gsKQ32B`를 씀.
- [P3] [C1] dry-run에서 기사·LLM 캐시 기록(문서화돼 있음).

### premierebot (bot.py, config.py, sources.py; scripts_merge_once.py는 일회성)
A 추적: 시사회 토픽 `LdaiIa8ug_0u`. `SyncEngine(absent="delete", adopt_scope="category", cache=)`(bot.py:238), `sync(events, source_complete=True, source_window=(now, now+60d), absent_filter=lambda r: not bad)`(:243-247) — 체인 하나라도 실패하면 전 체인 absent 보류(묶음 일정에 체인이 섞여서 의도된 설계). forget_before 120일.
**CGV 403 경로(dalbots-jzz)**: `raise_for_status`(sources.py:218) → collect가 잡아 `bad["cgv"]` → alert(:240-242) + absent 전체 보류 → 데이터 손실 없음, 정상. 로그상 403 6회(간헐), 최근은 200+0건. dalbots-jzz는 open 유지.
- [P2] [B1] sources.py:219-232, 293-295; bot.py:244 — CGV 200 + 빈 `data.list`, 메가박스 블록 0개 + `data-no` 없음은 완전 조회로 통과 → 소프트 차단·필터 변경과 구분 불가, 해당 체인 일정 삭제. CGV 건수 10-29 3 → 11-13 0(정상 감소인지 미확인) — `rows = data["list"]` / `if not blocks and "data-no" in r.text: raise SchemaError(...)` — 직전 run 대비 체인별 N→0 전환은 bad 처리(하루 유예). (.246)
- [P3] [B1] bot.py:244 — `source_complete=True` 하드코딩이라 엔진의 보류 alert 경로를 안 탐(봇 자체 alert로 대체됨).
- [P3] [A7] bot.py:129-131 — `key = m|{단계}|{날짜}|{작품}` → 마감·발표일이 바뀌면 삭제+생성(같은 날 묶음 설계상 불가피, 2026-11-11 사용자 결정).
- [P3] [G] config.py:4 — 브라우저(Chrome) UA 위장.
- [P3] [A2] bot.py:247 — 한 체인 403이 모든 체인 삭제를 멈춤(설계상 의도, 403이 길어지면 다른 체인 정리도 멈춤).

### chzzkarchivebot (bot.py, discord_feed.py, live_replay.py; collector/collector.py는 노트북 실행, dist/는 gitignore)
A 추적: 멤버별 개인 토픽 `SyncEngine(absent="keep", protect_human_edits=True)`(bot.py:455) + 메인 `v1c2BaT1zEvn` `SyncEngine(absent="keep", protect_human_edits=True, adopt_scope="category")`(:462-463), 각각 run당 1회 `sync(..., source_complete=complete)`. complete는 VOD 목록·live 이벤트·다시보기 조회 결과의 AND(:375-424). key=`{cid}:{open_ms}` 또는 `{cid}:yt:{date}`.
**chzzkvodbot과 중복 여부**: 채널 id 16 vs 16 겹침 없음, 메인 토픽 안 카테고리 겹침 없음, 개인 토픽 겹침 없음(chzzk_vod_bot.py MEMBERS·DB 대조). chzzkvodbot은 메인 토픽 이벤트를 자기 멤버 카테고리로 걸러(chzzk_vod_bot.py:892) 그것만 prune → **현재 중복 등록·상호 삭제 없음**. read_feed는 수집기 피드의 모든 채널(비멤버 포함) 방송을 테이블에 넣음(무해, P3).
- [P1] [A7] bot.py:333-350 — 방송 기록 없는 다시보기는 key `cid:yt:date`(종일 또는 다시보기 생방송 시각)로 등록되는데, 나중에 수집기 방송 기록과 짝지어지면 key `cid:open_ms`로 새로 생성. absent=keep이라 옛 일정이 남고 start·제목이 달라 입양도 안 됨 → 같은 방송 2건(현재 yt 키 64행 중 겹침 0 — 미발생) — `key = f"{m['channel_id']}:yt:{d}"` / `emit(f"{m['channel_id']}:{start}", title, start, start, False, desc, "live", vids)` — 같은 날·같은 cid의 yt 행을 방송 key로 relink(state rekey) 또는 yt 행 삭제. (.239)
- [P1] [B1] bot.py:73-75; live_replay.py:77-79 — VOD 목록·live-status·YouTube 실패는 warning만. complete=False가 되지만 keep 엔진은 alert하지 않음(sync.py:763-765). live-status는 직전 방송만 주고 치지직 VOD는 지워지므로, 며칠 조용한 장애면 그 기간 아카이브를 영구히 잃음 — `log.warning(f"VOD 목록 실패 {channel_id} page={page}: {e}")` / `log.warning(f"[{m['name']}] live-status 실패: {e}")` — 실패 사유를 모아 run 끝에 ctx.alert. (.240)
- [P2] [F1] discord_feed.py:172-173; collector/collector.py:95-96, 127-128, 139-140 — 웹훅 URL(경로에 토큰)이 requests 예외 문자열로 로그에 남음. mask_secrets는 `key=` 쿼리만 가림 — `log(f"[{ch['name']}] 웹훅 실패({attempt + 1}): {e}")` — 예외 문자열에서 웹훅 URL 치환. (.247)
- [P3] [D1] 시각 일정 start==end(chzzkvodbot 관례와 같음).
- [P3] [G2] broadcasts·replays 테이블 무한 증가(비멤버 채널 포함).
- [P3] [C1] VodMeta(bot.py:371)·replays가 dry-run에서도 기록(주석으로 문서화).
- [P3] [A] discord_feed read_feed가 비멤버 채널도 저장.

---

## 2부 — 기존 4봇, 2026-10-26 이후 diff (토픽 라우팅)

확인 관점: 한 사건이 두 토픽으로 갈 때 key·state가 토픽별로 분리되는지, 한쪽 실패가 다른 쪽 absent 판정에 새지 않는지.
결론: **4봇 모두 state는 (topic_id, key)로 분리, 라우팅 sync는 원 토픽 sync 뒤에 try/except로 격리** → 한쪽 실패가 다른 쪽 absent로 새는 경로 없음. 문제는 공유 IP 토픽의 입양 범위(convini·collabcafe)와 슬러그 일치 의존(koreansoccer).

### convinibot (ip_campaign.py; beb024b, 989fe21)
A 추적: `sync_ip_topics` — IP 토픽마다 `SyncEngine(spec.topic_id, state_path, absent="keep", on_missing="ignore", protect_human_edits=True)`(ip_campaign.py:97-98), `engine.sync(evs)`(:99). key=`{ip}|{store}|{ISO주}`(:72), summary "{매장} {IP} 신상 N종"(:60-61). 같은 state.db, topic_id로 분리. 원 토픽 sync 이후 try/except+alert로 격리.
- [P2] [A4/A7] ip_campaign.py:97-99, 72 — 공유 IP 토픽(포켓몬·산리오)에 adopt_scope 기본값 "topic"으로 씀 → 같은 (summary, start)면 ipgoodsbot·ipnewsbot·collabcafebot 일정을 카테고리 무관하게 입양, 원 봇이 지우면 이쪽은 on_missing=ignore로 영구 손실. 또 anchor가 다른 ISO 주로 넘어가면 새 key가 생기고 옛 일정은 keep으로 잔존 — `engine = SyncEngine(spec.topic_id, state_path, absent="keep", on_missing="ignore", protect_human_edits=True,` — `adopt_scope="category"`, 주차 대신 안정 key(첫 상품 id) 또는 relink. (.248)

### collabcafebot (ip_route.py; 1d06492, b7d7298)
A 추적: `SyncEngine(spec.topic_id, state_path, absent="keep", on_missing="ignore", protect_human_edits=True)`(ip_route.py:73-75), `sync(evs, source_complete=False)`(:76). key는 원 토픽과 같은 key, state는 토픽별 분리. 카테고리 매핑: 포켓몬 굿즈→상품 출시. try/except 격리. b7d7298로 링크를 본문으로 옮김(OK).
- [P2] [A4] ip_route.py:73-76 — convinibot과 같은 topic 범위 입양. 포켓몬 "상품 출시"로 매핑되므로 ipgoodsbot·ipnewsbot과 같은 카테고리에 들어간다 — `engine = SyncEngine(spec.topic_id, state_path, absent="keep", ...)` — `adopt_scope="category"` 이상, 타 봇 id 제외. (.249)

### koreansoccerguysbot (bot.py sync_player_topics; 7f748c7, dd0defe, 1ce53a6)
A 추적: 선수 토픽마다 `SyncEngine(absent="delete", adopt_scope="category")`, `absent_filter=in_scope` — 같은 run의 실패 목록(소스·선수 이름별 `held`)과 누락 카테고리를 공유(bot.py:330-374). 토픽별 state 행, 실패한 소스·선수의 행만 보류 → 격리 올바름. ESPN 200+0경기도 실패 처리(:135-137, W7 .161 반영).
- [P2] [A8] bot.py:127, 138-139 — FD 선수의 ESPN 보조 조회는 `fx.league_slug != skip`으로 FD 리그만 뺌 → `espn_skip_league`가 비었거나 오타·ESPN 슬러그 변경 시 같은 리그 경기가 `fd|`·`espn|` 두 key로 생성(그 선수 토픽 둘 다) — `sched = [fx for fx in sched if fx.league_slug != skip]` — skip 슬러그가 일정에 한 번도 안 나오면 alert, 또는 킥오프·상대로 fd/espn 중복 제거. (.250)

### animebot (works.py, jump.py, works.yaml; 9ef5718…a72e17f)
A 추적: works.py `route()` — 작품마다 `SyncEngine(w.topic_id, state/work_topics.db, absent="delete", on_missing="ignore")`(works.py:320), `sync(events, source_complete=complete, source_window=window)`(:324), complete = 발견 성공 and 그 작품 소유 페이지가 깨지지 않음, 창 today~+400. 통합 토픽은 releases 테이블 기반, works footer는 topic_id가 있을 때만 통합 해시를 바꿈. 원피스: 제목에 "원피스" 포함 → 연재 나무 페이지 "원피스(만화)/연재본"을 소유, serial_broken이 삭제를 막음(OK). 점프 key `jump|{연}-{호}`가 회차 key와 같은 작품 토픽을 쓰고 absent_filter는 없지만 complete 플래그가 둘 다 덮음(OK). CUTOFF 이전 백필은 작품 토픽에만, 창 밖.
- [P3] [D2] works.yaml:33 — 연재 화수 시드 `{issue: 2026-47, ch: 1196}` 하드코딩(출처 주석 있음). 나무 확정 시 보정 필요.
- [P3] [B1] jump.py — 화수 체인이 끊기면 화수 없는 제목으로 등록하고 로그만.
- [P3] [D3] jump.py:55 — `year - 1 if month == 12` — 신년 합병호 표기 가정. 실제 표기와 맞는지 미확인.

---

## 그룹 공통 패턴

1. **"파싱은 됐는데 비었거나 필드가 빠짐"을 완전한 소스로 취급.** kbocanceltick(summary 해석 불가 → skip), bookrelease(상세 필드 None → 캐시 덮어씀), premierebot(CGV·메가박스 200+빈 목록), legobot(pieceCount 빠지면 단종 목록에서 빠진 것으로 봄), ipgoods(이미지 non-200 → no_image). 예외는 잘 잡지만 "정상 응답인데 내용이 이상함"은 대부분 통과한다. 엔진 대량 가드 최소 한도가 10이라 토픽 행이 적은 신규 봇은 거의 보호받지 못한다. → 봇마다 "직전 run 대비 N→0" 또는 "파싱 실패 ≥1 = 불완전" 규칙.
2. **공유 IP 토픽 입양 경쟁(A4).** 포켓몬·산리오 토픽에 ipgoods·ipnews·convini·collabcafe 4봇이 쓴다. state 분리로 A3는 없지만, convini·collabcafe는 adopt_scope 기본값 "topic"이고 ipgoods도 "다른 봇이 가진 것 건너뛰기"가 없다(ipnews만 있음). 입양 후 원 봇이 absent 삭제하면 입양한 봇은 on_missing=ignore로 영구 손실. 아직 관측 사례 없음(미확인). → 공유 토픽 쓰는 봇은 adopt_scope="category" + ipnews식 `lookup_all` 선제외를 기본으로, 가능하면 엔진에 "타 봇 소유 id 입양 금지" 옵션.
3. **absent 창이 소스가 실제로 덮는 범위보다 넓다.** ipgoods 창 하한 today-30 vs "현재 목록 상위 8", ipnews RETAIN 150일 vs HORIZON 365일, legobot은 반대로 과거로 생긴 가짜 단종 일정이 창 밖이라 영원히 안 지워짐. → 창 = 소스가 매 run 다시 보여주는 범위(대부분 today~).
4. **조용한 실패와 영구 캐시.** keep 엔진은 소스 실패 alert가 없다(chzzkarchive), 정상 삭제는 무경보, ipgoods는 일시 실패를 영구 캐시, ipnews는 ConviniIndex 실패를 warning만. → 봇이 "보류/제외 사유"를 모아 run 끝에 alert 한 줄, 실패 결과는 캐시하지 않기.
5. **absent=keep + 흔들리는 key = 중복.** chzzkarchive yt→방송 key 전환, convini 주차 key, (delete 엔진이지만) bookrelease 대표 ISBN·premiere 날짜 포함 key. keep 엔진에서는 key 변경이 곧 영구 중복이다. → keep 엔진은 key 안정성 검토 필수, 바뀔 수 있으면 state rekey 경로.

## 가장 먼저 고칠 것

1. **kbocanceltickbot .235** — summary 해석 실패 시 complete=False. 입력이 다른 봇 토픽이라 KBObot 수정 하나로 창 안 일정이 전부 지워질 수 있고, 2027 시즌 전에 KBObot이 바뀔 가능성이 가장 높다. 한 줄 수정.
2. **chzzkarchivebot .240 + .239** — 소스 실패 alert(아카이브는 다시 못 긁음) 그리고 yt→방송 key 중복(keep이라 생긴 중복은 사람이 지워야 함). 현재 state/sync.db: yt 키 64행(전체 946), 같은 날·같은 채널 방송 key 행과 겹치는 것 0 → 아직 발생 안 함, 수집기 방송 기록이 늦게 들어오는 순간 생긴다.
3. **공유 IP 토픽 소유권 — .238 + .248 + .249** — ipnews ConviniIndex 실패 → complete=False(한 줄), convini·collabcafe adopt_scope="category". 4봇이 한 토픽에 쓰는 구조라 한 번 어긋나면 원인 추적이 가장 어렵다.

차순위: bookreleasebot .236(캐시 덮어쓰기라 복구도 재조회가 필요), ipgoodsbot .237.
