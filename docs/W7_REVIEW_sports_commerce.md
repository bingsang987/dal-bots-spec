# W7 리뷰 — 스포츠·e스포츠·금융·생활캘린더·티켓·굿즈 24봇

읽기 전용 감사(2026-10-22). 기준: `W7_REVIEW_CHECKLIST.md`, `W2_REBUILD_BRIEF.md`, `src/botkit/sync.py`. 파일 수정·봇 실행 없음(state DB는 sqlite `mode=ro`로만 조회, 로그 읽기). 6개 서브리뷰를 병렬로 돌린 뒤 P0 전부와 P0~P2 근거 라인을 인용 코드로 재검색해 고쳤다(리뷰어 보고의 하위 모듈 라인 번호가 일부 어긋났음 — kfa.py, football_data.py, naver_esports.py, season.py, cpt_client.py, convinibot sources). P3 라인은 리뷰어 보고값이라 ±수 줄 오차 가능.

## 요약

| 봇 | P0 | P1 | P2 | P3 | beads |
|---|---|---|---|---|---|
| PLBOT | 0 | 0 | 1 | 4 | .186 |
| KBObot | 0 | 0 | 3 | 3 | .187 |
| kboticketbot | 0 | 0 | 1 | 5 | .188 |
| koreafootballbot | 0 | 2 | 0 | 3 | .158 .159 |
| koreansoccerguysbot | 0 | 2 | 3 | 2 | .160 .161 .189 |
| lckbot | 0 | 0 | 2 | 3 | .190 |
| lckteambot | 0 | 2 | 4 | 5 | .162 .163 .191 |
| msibot (+worlds_bot) | 0 | 2 | 5 | 5 | .164 .165 .192 |
| fgcbot | 1 | 4 | 5 | 4 | .149 .166–.169 .193 |
| stockmarketBOT | 1 | 1 | 3 | 3 | .150 .170 .194 |
| econcalbot | 0 | 1 | 1 | 1 | .171 .195 |
| housingbot | 1 | 1 | 1 | 1 | .151 .172 .196 |
| ipobot | 0 | 2 | 0 | 2 | .173 .174 |
| youthpolicybot | 0 | 1 | 1 | 2 | .175 .197 |
| taxcalbot | 0 | 0 | 1 | 1 | .198 |
| couponcalbot | 1 | 1 | 2 | 2 | .152 .176 .199 |
| campingcalbot | 0 | 0 | 1 | 2 | .200 |
| convinibot | 1 | 1 | 3 | 2 | .153 .177 .201 |
| tumblbugbot | 0 | 1 | 1 | 2 | .178 .202 |
| megasale_bot | 0 | 0 | 3 | 6 | .203 |
| ticketbot | 0 | 1 | 3 | 6 | .179 .204 |
| canceltickbot | 1 | 2 | 3 | 3 | .154 .180 .181 .205 |
| daisobot | 1 | 2 | 2 | 5 | .155 .182 .183 .206 |
| theatergoodsbot | 2 | 2 | 1 | 6 | .156 .157 .184 .185 .207 |
| **합계** | **9** | **28** | **50** | **78** | 59건 (`dalbots-bmi.149`~`.207`) |

beads: P0·P1은 건별, P2는 봇별 묶음(형제 세션 W7 관례). 엔진 쪽 기존 이슈와 겹치는 것은 참조만 했다 — 삭제 과다 가드(dalbots-bmi.66), adopt_scope=topic exclude 미전달(.68), CHANGED 경로 on_missing 우회(.65), URL 쿼리 키 노출(.117).

**지금 진행 중인 것 (코드 결함이 아니라 운영 이상)**
- 인터파크 수집이 2026-10-21 19:3x부터 ticketbot·canceltickbot 둘 다 0건(`[인터파크] API 호출 실패 (offset=0): Expecting value`, canceltickbot `실패 소스 ['인터파크','CONCERT','MUSICAL']`, 취소마감 21→0) — HEALTH-ALERT 한 번도 없음(.179 .180).
- theatergoodsbot event_id 공유 52건이 현재 state에 있음 — 체인 하나씩 캘린더에서 빠져 있음(.156, 데이터 패치 필요).
- daisobot·theatergoodsbot은 **SyncEngine 미이식**(W2 이전 자체 sync) — W7 범위 밖이지만 P0 3건의 공통 원인.

대상: PLBOT, KBObot, kboticketbot, koreafootballbot, koreansoccerguysbot, lckbot, lckteambot, msibot, fgcbot, stockmarketBOT, econcalbot, housingbot, ipobot, youthpolicybot, taxcalbot, couponcalbot, campingcalbot, convinibot, tumblbugbot, megasale_bot, ticketbot, canceltickbot, daisobot, theatergoodsbot.

## 엔진 의미 정리 (리뷰 기준)
- `engine.sync(events, source_complete=True, source_window=None, absent_filter=None)` — 기본값 True라 인자를 안 넘기면 항상 complete. 브리프의 `source_ok`는 엔진에 없는 이름.
- absent 후보 = 그 topic_id의 state 행 중 이번 events에 없는 key 전부. `source_window`를 안 주면 과거 행 포함 전체가 후보. 쓰기 실패(stats.failed)는 absent를 막지 않음.
- NEW는 `adopt_scope="topic"`이면 토픽 전체 (summary,start)로 입양 → 공유 토픽에서 타 봇 이벤트를 가져올 수 있음.


## 스포츠 (PLBOT·KBO·축구·LCK)

공통: 6봇이 쓰는 토픽 ID는 각 봇 단독(Documents 전체 grep, legacy 제외; `dalwiki-discord-bot/app/config.py:25`의 `vvSHkOrwL1Rh`는 알림용 읽기) → A3/A4 해당 없음. 티어: PLBOT 2x(11:00/19:30), DAILY(11:15) KBObot→kboticketbot 순(입력 DB 순서 OK), lckbot·koreafootballbot·koreansoccerguysbot DAILY.

### PLBOT (bot.py)
A 추적: 팀 토픽당 SyncEngine 6개(bot.py:289), absent=delete(사라진 경기 > max(2, 20%)면 keep), on_missing=ignore, adopt=topic, cache 45/120. sync 토픽당 1회(bot.py:299), `source_complete=True` 하드코딩 — 단 팀당 1콜이고 예외/HTTP≠200이면 fetch가 raise해 그 토픽 sync 자체를 건너뜀(:274-278), 200+0건은 엔진 source=0 스킵 → 실질 complete. 소스 today-7~today+90(UTC 날짜), absent 창 [today-6 00:00Z, today+90 00:00Z) — 과거·창 밖 보호. key=match id.
- [P2] [A2] bot.py:172,294,299 — 200 응답에서 특정 대회가 빠져도(README:66 "ECL/CWC는 구독에 따라 빠질 수 있다") 실패로 안 잡힘. 경기 15개면 사라진 3건까지는 가드 통과 → 바로 삭제, alert 없음 — `if events and len(absent) > max(2, MAX_ABSENT_RATIO * len(events))` / `source_complete=True` — 대회 코드별 건수를 이전 run과 비교하거나, 가드 미달 삭제도 alert.
- [P3] [B1] bot.py:300-302 — SyncAborted면 `continue`로 `engine.state.close()` 생략. 팀 하나 200+0건이어도 팀별 alert 없음(6팀 합 0일 때만, :315).
- [P3] [E3] bot.py:233-236 — 모르는 대회 코드(커뮤니티 실드 등)를 EPL 카테고리로 등록, warning만.
- [P3] [E1] bot.py:199 — 상대팀 name null이면 "None vs 아스날"(API null 여부 미확인) — `or "미정"`.
- [P3] [F1] bot.py:163 — 같은 football-data 키를 PLBOT은 `FOOTBALL_API_KEY`, koreansoccerguysbot은 `FOOTBALL_DATA_API_KEY`로 읽음 → 키 교체 시 한쪽 누락 위험.

### KBObot (bot.py)
A 추적: 통합 1 + 팀 10 = 엔진 11개(bot.py:346), state 파일 1개(topic_id로 분리), absent=delete(후보 >5 & >10%면 keep, :342), on_missing=ignore, adopt=topic. sync 토픽당 1회. source_complete: 4개월×시리즈 8 = 32회 fetch_month 중 하나라도 None(예외·raise_for_status)이면 False, 200+rows 빈 값은 True. 창 [전월 1일 KST, 마지막 월 다음 달 1일 KST) = 긁은 월과 일치. key=game_id(합성 포함, 번호 변경 시 migrate_keys).
- [P2] [B1] bot.py:302-310,312-314 — 일부 fetch 실패 → complete=False + log만, alert는 소스 전부 0일 때만 → 삭제 판정이 조용히 계속 스킵 — `if got is None: complete = False` — 실패한 (월, sr) 목록 alert.
- [P2] [A2] bot.py:105,112-131 — 200에 `rows: []`가 오거나 HTML 구조가 바뀌어 parse_play가 None이어도 정상 → 그 (월, 시리즈) 경기 삭제, 막는 건 ">5 & >10%" 가드뿐 — `rows = r.json().get("rows", [])` — 시즌 중 정규(sr=0) 월 0경기는 실패 처리.
- [P2] [A1] bot.py:295-297 — `--months 2026-03,2026-05`처럼 비연속이면 창이 빠진 달(4월)까지 덮어 4월 state 행이 absent 후보 — `win_from = datetime(months[0][0], ...)` / `ny, nm = months[-1]` — 연속 구간만 허용 또는 월별 창.
- [P3] [G3] bot.py:302-304 — KBO POST 32회 무간격.
- [P3] [E1] bot.py:144,180-182 — relay 없는 올스타전은 약칭(나눔/드림 등)이 SHORT_TO_CODE에 없어 prefix 8자 → warning 후 skip(실제 표기 미확인).
- [P3] [F2] bot.py:332-355 — forget_before 미호출. 통합 787행 중 630행, 팀당 126행이 start_iso 빈 legacy 행으로 영구 잔존(kboticketbot 입력에도 섞임).

### kboticketbot (bot.py, schedule.py, config.py)
A 추적: 엔진 1개(bot.py:150) delete/ignore/topic, cache 60/400. sync 1회, `source_complete = not no_cat`(CAT_* 누락 시만 False). 입력=KBObot state DB(ro); 통합 토픽 캐시 ListFailed면 alert 후 return(:110-112). 창 [today-3 00:00 KST, today+31 00:00 KST) = 오픈일 범위 [ws, we]와 일치, 필요한 첫 경기일(today+5~+38)은 KBObot 창 안. key=`홈|원정|ISO주`(#n), 중복 방지 OK.
- [P2] [A2] bot.py:97-103,152 — 입력(KBObot state) 신선도 검사 없음. KBObot이 며칠 멈춰도 낡은 일정으로 생성·삭제 계속, alert 없음 — `games = schedule.load_games(KBO_STATE_DB, KBO_UNIFIED_TOPIC)` — 통합 토픽 행 max(last_seen) > 2일이면 alert + source_complete=False.
- [P3] [C1] bot.py:104→39-57 — migrate_legacy가 dry-run에서도 실 state에 import_legacy(현재 70행이라 비작동) — `if not ctx.dry_run`.
- [P3] [D2] config.py:202 — `TICKET_VENDOR_SEASON = 2026`. 만료 가드(alert + '확인 필요') 있음(bot.py:116-124) → 2027 시즌 첫 오픈(~2027-03)부터 매 run alert 예정 — 시즌 전 표 갱신.
- [P3] [F5] bot.py:15 "9~3월" / config.py:225-226 "10~3월" / README:21 "4~8월" — 비시즌 정의 불일치. 코드 `SEASON_OPEN_MONTHS = {4,5,6,7,8}` → 9월 오픈 0건이어도 alert 없음.
- [P3] [F3] run_sched.bat:1-5 — `pause` 없음(README:34에 의도 명시, 티어는 `<nul`).
- [P3] [E2] schedule.py:329 — 본문 "확정되면 갱신됩니다"인데 확정 PATCH 경로 없음.

### koreafootballbot (bot.py, kfa.py)
A 추적: 엔진 1개(bot.py:169) delete/ignore/topic, cache max(60,…)/max(400,…)로 긁는 범위 포함. sync 1회, `source_complete = not failed` — failed는 월 페이지 RequestException 3회 소진(raise_for_status 포함)뿐, 200+카드 0개는 성공. 창 [today-3개월 1일, today+10개월 1일) KST = 긁은 13장과 일치. key=`kfa:<id>`, fetch_months setdefault로 중복 제거.
- [P1] [A2] kfa.py:137-139 / bot.py:174 — 한 달 페이지가 200인데 카드 0개(점검 페이지·부분 구조 변경)면 정상 → 그 달 이벤트 삭제, 다음 정상 run에 새 id로 재생성(사람 편집 소실). 전체 0건만 막힘(bot.py:159) — `matches = list(parse_month_html(html, year, month)); return matches` — 그 달 state 행 ≥2인데 파싱 0이면 그 달 failed.
- [P1] [A2/E3] bot.py:119-122,156-157,174 — 팀 매핑 실패는 alert하지만 source_complete 그대로 → 그 팀 기존 이벤트(13개월 창) 전부 삭제(KFA 팀명 변경 시) — `if not cat: unmapped.append(m.team); continue` … `source_complete=not failed` — `not failed and not unmapped`(kboticketbot 방식).
- [P3] [F5] bot.py:65-66 vs README:39 — README "비치사커는 alert 후 스킵", 코드는 팀명에 "국가대표"면 남자A(KFA 표기 미확인).
- [P3] [F2] .env에 미사용 `FOOTBALL_API_KEY`(README:21), `state/state.json`·`.bak` 잔존(gitignore됨).
- [P3] [D1] bot.py:145 — `date.today()` 호스트 로컬 기준(KST 호스트 가정).

### koreansoccerguysbot (bot.py, sources/espn.py, sources/football_data.py)
A 추적: 엔진 1개(bot.py:254) delete/ignore/adopt=category. sync 1회, `source_complete = not failed` — FD None(키 없음·4xx·재시도 소진), ESPN None(2콜 중 하나 실패); 200+빈 목록은 성공. 창 [today-3 00:00 KST, today+91 00:00 KST); FD는 lo-1~hi+1 조회, ESPN은 시즌 전체 받아 add()에서 창 필터. key=`src|match_id|선수`, (source,match_id) 병합 + 선수 dedupe로 중복 없음.
- [P1] [A2] sources/football_data.py:156 + bot.py:84-101 — FD가 200에 matches 빈 값(또는 키 없음)이면 failed 아님, stale 검사도 `if raws:`에 막혀 alert 전무 → 그 대회 FD 선수 전원 창 안 이벤트 조용히 삭제 — `return (r.json() or {}).get("matches") or []` / `if raws: stale += ...` — 시즌 중 빈 목록은 failed.
- [P1] [A2] sources/espn.py:284-292 + bot.py:113-114 — ESPN 200+events 빈 값(스키마 변경)이면 stale alert만, failed 아님 → 그 선수 이벤트 삭제 — `if not sched: stale.append(...)` — failed에 추가.
- [P2] [E3] bot.py:175-177,243-244,257 — category_id 없는 선수는 skip+alert, complete 그대로 → 기존 이벤트 삭제(yaml 오타 1회로) — `no_cat.add(name); continue` — `source_complete=not failed and not no_cat`.
- [P2] [A1] sources/espn.py:281-283 — 결과 콜이 "이번 시즌" 범위라 시즌 롤오버(MLS·스코틀랜드) 때 직전 3일 내 종료 경기가 응답에서 빠지면 삭제 가능(미확인) — `for params in ({}, {"fixture": "true"})` — 창 lo를 today로.
- [P2] [D2] config/players.yaml (README:55) — 황희찬 소속 낡음 → 매 run "팀 미매칭" alert(alert 둔감화) — yaml 갱신.
- [P3] [C1] bot.py:234→192-214 — migrate_legacy가 dry-run에서도 실 state에 씀(현재 272행이라 비작동).
- [P3] [F2] state/state.json, state.json.pre-w6.bak 잔존.

### lckbot (bot.py, leaguepedia.py)
A 추적: 엔진 1개(bot.py:159) delete(가드 시 keep)/ignore/topic, cache 올해 전체+7일. sync 1회, `source_complete=True` 하드코딩 — Cargo HTTP≠200·403/429·비JSON·호출 상한·기타 예외는 모두 raise → sync 전 alert 후 return 0(:134-142) → 실질 complete. 창 [Y-01-01Z, Y+1-01-01Z) = 올해 LCK 토너먼트 전 경기. key=MatchId.
- [P2] [A1] bot.py:170-172 — 삭제 과다 가드 분모가 **전 연도 state 행**인데 forget_before가 없어 해마다 커짐. 현재 188행 → 18건까지 통과(Road to MSI 통째 누락도 삭제), 내년 ~37건 — `len(absent) > max(3, int(len(state_rows) * ABSENT_GUARD_RATIO))` — 분모를 올해 창 state 행(또는 len(events))으로 + `forget_before(y0)`.
- [P2] [B1] bot.py:147-149 — 토너먼트는 있는데 경기 0건이면 달 무관 info로 끝 → 시즌 중 Cargo 빈 결과에도 alert 없음 — `log.info(f"비시즌: ...")` — 오늘이 토너먼트 DateStart~Date 안이면 alert.
- [P3] [C1] bot.py:161-165 — legacy import가 dry-run에서도 실 state에 씀(현재 비작동).
- [P3] [A7] bot.py:54-58 — MatchId 없을 때 fallback key에 Team1/Team2 → TBD가 팀명으로 바뀌면 새 이벤트+옛것 삭제(평소엔 MatchId 있음).
- [P3] [F2] 루트 legacy `lck_bot.log`(239KB) 잔존.

## e스포츠·격투게임 (lckteambot·msibot·fgcbot)

공통: 토픽 비공유(lckbot `EZuS4T1OGjb2`, lckteambot 팀 10 + `BzGUT0wOWsgx`, msibot `nTCdDehWCBKq`/`U20Zns8YfaAc`, fgcbot `VBxX9AHcxhiO`/`aG892YPuMZXD`). `BzGUT0wOWsgx`는 dalwiki-discord-bot `app/config.py:24`에 알림 구독으로만 등장(쓰기 여부 미확인). 토픽 내 혼합(lckteambot `match|`+`feed|`)은 `absent_filter`로 분리됨.

### lckteambot (lck_team_bot.py, matches.py, feed.py, config.py)
A 추적: 경기 엔진 = 토픽 11 × `SyncEngine(absent="delete", on_missing="ignore", adopt_scope="topic", back=max(60, 1/1부터 일수+7), fwd=200)`, 토픽당 1회. `source_complete=True` 하드코딩(Cargo 예외·HTTP≠200·비JSON은 sync 전 return, :80-85). `source_window=(max(now-60d, 1/1), now+180d)` = 오늘 2026-08-23~2027-04-20; 소스는 올해 LCK·MSI·EWC 전체 + 내년 2/1 이전 시작 LCK 전부라 창이 소스 안. 12→1월엔 lo=1/1로 작년 행 보호. `absent_filter`=`match|`. 피드 엔진 = 토픽 11 × `absent="keep"`, `source_complete=False`, 메시지마다 sync.
- [P1] [A1/A2] lck_team_bot.py:105-107 — 10% 삭제 가드 분모가 창 밖 포함 올해 경기 행 전체 → 가드 사실상 무효 — `state_rows = [r for r in engine.state.all().values() if is_match_row(r)]` / `len(absent) > max(3, int(len(state_rows) * ABSENT_GUARD_RATIO))` — 실측: 통합 토픽 창 안 15행인데 임계 23, 팀 토픽 창 안 0~5행에 임계 3~5 → Cargo 부분 응답이면 창 안 경기 전부 alert 없이 삭제(다음 정상 run에 새 id 재생성) — 분모를 창 안 행으로.
- [P1] [B1] lck_team_bot.py:167-168 — catchup 채널 조회 실패(강퇴·권한 상실)는 warning뿐 → 그 채널 영구 조용히 0건 — `if stats.errors and stats.failed == 0: log.warning(...)` — `channel ` 오류면 alert.
- [P2] [A2] lck_team_bot.py:95-97 — 토너먼트는 있는데 MatchSchedule 0행(Cargo 재생성 중 등)이면 "비시즌" info. 소스에 날짜 제한이 없어 시즌 중 0건은 이상 신호 — `tours` 비어있지 않은데 0행이면 alert.
- [P2] [A7] feed.py:309,324 — 피드 key에 LLM 출력(`|stream|{s['date_iso']}`, `{c['label']}`). 팀 토픽 성공 후 통합 토픽 쓰기 실패(lck_team_bot.py:161-162 raise) → 재시도 큐가 LLM 재질의 → 시각·라벨 달라지면 새 key, keep이라 옛 이벤트 영구 중복(+LLM 비용) — 파싱 결과 message_id 캐시 또는 key=tweet_id+순번.
- [P2] [D1] feed.py:262-264,314,317-318 — 시각 없는 분류 결과가 00:00 KST timed, start==end timed — `hh, mm = ... if data.get("time_kst") else (0, 0)` / `end = start` — 시각 없으면 all_day, 그 외 기본 지속시간.
- [P2] [D3] feed.py:216 — 스트리밍 스케줄 날짜를 메시지 발송 월로 고정 — `datetime(ref.year, ref.month, int(s["day"]), ...)` — 10/28 게시 10/28~11/3 스케줄의 11/1~3이 10/1~3(과거)로, 12월 말 게시 1월 날짜는 같은 해 12월로 — day가 게시일보다 많이 작으면 다음 달(해).
- [P3] [A6] lck_team_bot.py:156 — 피드 sync를 메시지마다 호출(`source_complete=False`, keep이라 지금은 안전; 매번 `state.all()` ~700행, 정책 바뀌면 즉시 삭제 사고).
- [P3] [A8] lck_team_bot.py:110-114 — key 중복 ValueError는 `except SyncAborted`가 못 잡아 run 전체 중단, 피드(B)도 skip.
- [P3] [C1] lck_team_bot.py:88-89,140 — dry-run에서도 `cargo_snapshot.json` 기록, `tempfile.mkdtemp()` 미정리.
- [P3] [B1] feed.py:147-148 — t.co HEAD 예외 `except Exception: pass`, UA 없음 → 실패 시 유튜브 알림이 HAIKU 분류 경로로 새 LLM 비용.
- [P3] [F5] README.md:66 — "T1 토픽에 플옵 카테고리 없음"인데 config.py:206 `"플옵": "mbRhKhjYZvdG"` 존재.

### msibot (msi_bot.py, naver_esports.py, worlds_bot.py)
A 추적: msi_bot `SyncEngine(absent="delete", on_missing="ignore", adopt_scope="topic", back=max(60, 창 시작부터 일수+2))`, run당 1회; source_complete 미전달(조회 예외는 :134-136 alert 후 return); `source_window=month_bounds(months)` = 전월 1일~다다음 달 1일 KST = 조회 월과 일치; 비시즌(4~7월 미포함)엔 미호출. worlds_bot은 **SyncEngine 미사용** — `create_event`/`update_or_create_event` 직접 쓰기 + 자체 JSON state, 삭제 로직 없음.
- [P1] [A2] naver_esports.py:34 + msi_bot.py:153 — 월별 응답이 빈 배열이어도 정상 → 시즌 중 한 달만 `[]`(점검·일시 오류)면 다른 달이 비어있지 않은 한 그 달 경기 창 안 삭제(재생성 시 새 id, 사람 편집 소실) — `return r.json().get("content", {}).get("matches", []) or []` / `engine.sync(events, source_window=month_bounds(months))` — 월별 0건인데 state에 그 달 행이 있으면 `source_complete=False`.
- [P1] [B1/B6] worlds_bot.py:135,174-177 — botkit `run()` 미사용 → `[HEALTH-ALERT]` 전무. 네이버 예외는 Traceback 종료, 쓰기 실패는 카운트만 — `matches = [g for g in ne.fetch_matches(...)...]`(try 없음), `if not eid: n["FAIL"] += 1; continue` — 대회 기간(~11/22) 실패가 조용 — `run("worldsbot", main)` + FAIL>0·조회 실패·0경기 alert.
- [P2] [A1] msi_bot.py:107-112,153 — `--months` 비연속이면 창이 첫 월~마지막 월 전체(예: `2026-05,2026-07` → 미조회 6월 경기 absent 삭제, 수동 백필 CLI) — 연속 월만 허용 또는 월별 창 합집합.
- [P2] [D2] msi_bot.py:36,128-130 — `SEASON_MONTHS = {4, 5, 6, 7}` 하드코딩, 비시즌은 info만 → MSI가 8월로 밀리면 alert 없이 영구 skip — 비시즌에도 월 1회 조회 또는 창 밖 MSI 발견 시 alert.
- [P2] [규칙/A3] worlds_bot.py:174 — SyncEngine 밖 쓰기, absent 처리 없음 → 경기 취소·gameId 재발급 시 고아(재발급 여부 미확인) — 2027 일반화 때 SyncEngine 전환(README:29 후속 명시).
- [P2] [A5] worlds_bot.py:174 (client.py `update_or_create_event` 404→create) — 사람이 지운 경기도 스코어 확정으로 sig가 바뀌면 재생성(사실상 recreate) — 404면 db에서 forget.
- [P2] [D2] worlds_bot.py:28-30,127-129 — `MONTHS = ["2026-10", "2026-11"]`, `RETIRE_AFTER = date(2026, 11, 22)` 만료 후 info만, 스케줄러는 계속 → 2027엔 무신호 0건 — RETIRE 후 첫 run alert 1회.
- [P3] [C2] msi_bot.py:142(95-102) — `migrate_legacy`가 dry-run 포함 매 run, state 비면 import_legacy로 DB 기록(현재 무해).
- [P3] [F3] run_msi_bot.bat — `pause` 없음(run_worlds_bot.bat엔 있음).
- [P3] [F2] worlds_bot.py:46-52 — `logging.FileHandler(LOG_FILE)` 회전 없음.
- [P3] [E1/E2] worlds_bot.py:93,112 — 양 팀 미정이면 `미정 vs 미정`(일정명 없음), description 양식이 msi_bot과 다름.
- [P3] msi_state.json — 미참조 잔재.

### fgcbot (bot.py, startgg_client.py, parsers/startgg_parser.py, bne_client.py, cpt_client.py, stl_sheet.py, season.py)
A 추적: 게임별 2개 `SyncEngine(absent="delete", on_missing="ignore", adopt_scope="topic", back=60, fwd=400)`, 게임당 1회. `source_complete = not problems`(bot.py:237) — start.gg 검색·라인업 예외면 그 게임 skip; 검색 max_pages 절단·직접 fetch 실패·STL 예외면 False; 빈 응답(BNE 0 slug, STL 0행, CPT 일부 티어 누락)·시즌 yaml 폴백은 미반영; GraphQL `errors`/HTTP≠200은 예외. `source_window=(lo, hi-1d)`(bot.py:238), lo=now-7d, hi=now+240d(bot.py:219-220); 소스 = start.gg `afterDate/beforeDate`=[lo,hi](startgg_client.py:114 "토너먼트 startAt 범위") + BNE·강제 slug 직접 fetch(창 무관) + STL 시트. 주간 실행(월 13:00).
- [P0] [A1] bot.py:131,219,238 + parsers/startgg_parser.py:113 + bot.py:79-82 — 창 하한 필터 기준과 저장 start가 다르다. 검색은 `tournament.startAt >= lo`로 거르는데 저장 start는 `e.get("startAt") or t.get("startAt")`(종목 이벤트 시작, 여러 날이면 그날 00:00 KST). 대회 시작 < lo ≤ 종목 start_iso인 run에서 그 대회가 검색에서 빠지는데 state 행은 창 안 → 삭제, 다음 run부터는 과거라 소스·창 모두 밖 → **영구 누락**. 보호: BNE 라인업 slug·강제 메이저(직접 fetch). 노출: SF6 CPT 매칭분·KR 통과분. sgg_cache 실측 64명 이상 & gap>0 대회 19건(`nantarena-26` 7h, `momocon-2026-5` 51h, `kayane-session-octobre-2026` 407h); 주간 실행이라 확률은 낮음. afterDate 의미는 docstring 근거(API 실측 미확인) — 창 하한을 lo+최대 gap(예 +3d)으로, 또는 창 판정용 start를 tournament.startAt으로.
- [P1] [A2] bne_client.py:64 + bot.py:57-60 — BNE가 200인데 slug 0개(구조 변경)면 빈 라인업 정상 처리 → 해외 TWT 대회 전부 필터 탈락 → 창 안 이벤트 삭제, 빈 결과 12h 캐시 — `return set(SLUG_PATTERN.findall(r.text))` — 0개·급감이면 problems.
- [P1] [A2] stl_sheet.py:97-105 + bot.py:168-173 — 시트가 비공개되면 로그인 HTML(200) → 파싱 0건 정상 → 창 안 STL 이벤트 삭제, HTML 12h 캐시 — `r.raise_for_status(); text = r.content.decode("utf-8")` — content-type csv 아님/0건이면 problems.
- [P1] [A2] bot.py:237,242-243 + season.py — 시즌 폴백 경고(`ssn.warnings`)가 sync **이후** alert만, source_complete 미반영 → 지난 시즌 league/탭으로 돌면서 삭제 판정 — warnings 있으면 그 게임 incomplete.
- [P1] [D3] season.py:39,59 + bot.py:57,63-71 — 1/1 연도 전환 시 라인업이 올해 league 하나(`discover_league_id(year)`)·CPT 페이지 하나(`Capcom_Pro_Tour/{year}`, 성공한 첫 페이지만)만 → 새 해 페이지가 생기면 1~3월 지난 시즌 결승(TWT Finals, Capcom Cup)이 창 안인데 라인업에서 빠져 삭제, 해외라 재생성도 안 됨(결승 시기·페이지 수록 여부 미확인) — 1~3월엔 전년 league·페이지 합집합.
- [P2] [A2] cpt_client.py:105 — 티어 헤더 하나만 바뀌어도(예: "World Warrior" 개명) 그 티어 빠진 부분 라인업 통과(0건일 때만 예외) — `tier = TIER_HEADER_MAP.get(header_text); if not tier: continue` — Premier·World Warrior 둘 다 나오는지 검사.
- [P2] [A8] stl_sheet.py:188-203 + bot.py:114 — 같은 셀 텍스트가 비연속 날짜에 두 번(다른 주말 "STL TEAM BATTLE") → 청크 2개가 같은 key `stl:{year}:STL:{name}` → sync ValueError로 run 전체(SF6 포함) 중단 — key에 시작일 또는 #n.
- [P2] [A7] bot.py:114 — STL key에 시트 셀 원문 → 문구 수정 시 삭제+재생성. 로그 실측: `DELETE(absent) stl:2026:STL:STL S2`(DAY1~5 분할) — (연도, 시작일, 시리즈) key.
- [P2] [E2] parsers/startgg_parser.py:109-113 — TWT 매칭으로 임계 무시 시 첫 번째 종목 이벤트를 대표로 — `for code, e in cands: if code in seen_codes: continue` — 실측 MOOR1NG 2026 → `fighther-tekken-8-women-tournament`(30명), TGU 2026 → `tekken-8-3v3-team`(link·참가자·start가 사이드 이벤트 기준) — numEntrants 최대 선택.
- [P2] [F2/G2] startgg_client.py:52-56,68-73 + bot.py:131 — 검색 캐시 key에 now 기반 타임스탬프(`"after": int(after_ts)`) → 재사용 0%, 파일 무한 증가(실측 `data/sgg_cache` 1,771개, run당 ~47) — 검색 캐시 끄기 또는 TTL 정리.
- [P3] [C2] bot.py:222(178-197) — `migrate_legacy` dry-run 포함 매 run, 토픽 state 비면 옛 state.json(59행) 재이관.
- [P3] [D1] bot.py:84-85 — endAt 없거나 이르면 timed start==end (`elif end < start: end = start`).
- [P3] [E4] parsers/startgg_parser.py:38 — `COUNTRY_KO` 미등록 국가는 코드 그대로(`MG`, `CI`, `BO`).
- [P3] data/_cpt_probe.html, _sfl_probe.html, _sheet_probe.csv, state.json*.bak 잔재.

## 금융·주거·정책 (stockmarketBOT·econcalbot·housingbot·ipobot·youthpolicybot)

공통: 5봇 모두 DAILY 티어(`run_tier_daily.bat:23-37`) `call :RUN` 순차 → F4 없음. stockmarket kr/us는 `state/stockmarket.db` 공유하나 topic_id로 분리. 토픽 쓰기는 각 봇 단독 → A4 없음. 전부 sync run당 1회, on_missing=ignore, ListFailed→[] 없음, naive datetime 없음. 엔진 사실: `SyncState.import_legacy`는 dry-run 무관 commit(sync.py:221-239), 가드 미만 absent는 alert 없이 삭제.

### stockmarketBOT (bot.py, sources_kr.py, sources_us.py)
A 추적: `SyncEngine(topic, state/stockmarket.db, absent="delete", cache 120/400)` bot.py:123, `sync(events, source_complete=complete, absent_filter=flt)` bot.py:151, window 없음. complete = 소스별 ok AND(bot.py:87-94,101-108) — DART HTTP≠200·status≠000/013·60페이지 초과·소집공고 문서 예외·KIND 예외·AV 비정상/0행·FMP 비정상은 False, "0건 정상 응답"은 True. prefix별 filter 창: dart 공시일 [today-30, today](조회와 동일), agm 총회일 ≥ today(소집공고 90일 조회), ir ≥ 이번 달 1일(조회 1일~3개월 뒤 말일, 12→1월 OK), earn ≥ today(AV 3month), div/split 키 날짜 [+2,+28](FMP [0,+30] 조회).
- [P0] [A2] sources_kr.py:329-331,387 — KIND 응답에 표가 없거나(점검·차단 페이지) 데이터 행 0개여도 `[]` + complete=True → DART/주총 이벤트는 있으므로 absent 판정 실행 → 이번 달 1일 이후 `ir:` 행(~170건) 전부 삭제, 가드 없음 — `for tr in (table.find_all("tr") if table else []):` … `return events, refs, True` — `table is None`이면 SourceError, 0행이면 complete=False.
- [P1] [A2] sources_us.py:133,143-145,179 — FMP 배당 `[]` 또는 AV 행이 S&P500 필터 후 0건이어도 complete=True → [+2,+28] div 행 또는 오늘 이후 earn 행 전부 삭제 — `if not isinstance(data, list): raise …`(빈 list 통과), `return events, refs, True` — 배당·실적은 필터 후 0건이면 False(분할은 0 정상 가능).
- [P2] [E/F5] sources_kr.py:277-290 — 정정 소집공고로 총회일이 바뀌어도 원 공고가 90일 목록에 남아 `(corp, 옛 날짜)` 그룹 계속 생성 → 옛 날짜 이벤트 유지(README:50 "옛 것은 absent 삭제"와 다름) — `groups[(it["corp_code"], parsed["date"])].append(...)` — corp_code별 최신 rcept_no 날짜만.
- [P2] [B1] sources_kr.py:281,286-289 — 문서에서 날짜를 못 찾은 결과(None)가 `agm_docs.json`에 영구 캐시 → 재시도 없음, complete 미반영 → 파서를 고쳐도 영구 누락 — `cache[rc] = parse_agm_document(...)` — None 미캐시 또는 TTL.
- [P2] [F1] sources_kr.py:86,150,271 / sources_us.py:92,139 — `crtfc_key`/`apikey`가 쿼리스트링 → `log.error("DART %s 조회 실패: %s", ty, e)`의 HTTPError/ConnectionError 문자열에 전체 URL → 키가 stdout(lastrun .out)·로그 파일에 남음(alert로는 안 나감) — 예외 메시지 키 마스킹.
- [P3] [C1] bot.py:144 — dry-run에서도 매 run `import_legacy` commit(주석 "dry-run 에서도 수행").
- [P3] [A7] sources_kr.py:372 — `key = f"ir:{corp}:{iso}"`에 회사명 → 사명 변경 시 delete+create(W2에 만호제강→MH머트리얼즈 1건 실제 발생).
- [P3] [규칙] cleanup_orphans.py:82 — 수동 `--apply` 도구가 `delete_event` 직접 호출, :68-69 US split을 배당 창 기준 판정.

### econcalbot (bot.py)
A 추적: `SyncEngine(R6_y96_olqnS, state/econcalbot.db 또는 dry-run 복사본, absent="delete", cache 45/120)` :332, `sync(events, source_complete, source_window=window)` :348. source_complete=False: `failed` 날짜 ≥1(fetch_day None = 예외·HTTP≠200 2회 재시도 후) 또는 6개국 중 창 전체 0행 국가(:299-302), 전체 실패 캐시 폴백도 False. Nasdaq date today-7~+28(ET 발표일 today-8~+27), window [today-7 00:00(-4), today+27 00:00(-4)) — 양 끝 하루씩 안쪽. 설명 안 되는 absent > max(3, 10%)면 keep(:342).
- [P1] [A2] bot.py:85-86 — HTTP 200이면 `data` null/`rows` 없음도 `[]` 성공, `status.rCode` 미확인 → 하루치 빈 응답이면 그날 High 지표 absent 삭제, 가드 상한 max(3, 7.5)라 하루치(CPI 묶음)는 통과 — `if r.status_code == 200: return (r.json().get("data") or {}).get("rows") or []` — `data is None`/status 오류면 None(실패). Nasdaq 실제 오류 응답 형태 미확인, 주말 0행과 구분 필요.
- [P2] [A/B] bot.py:334 — `remap_moved`가 source_complete 무관 sync 전 실행, state 직접 변경 → 실패한 날 때문에 absent가 된 key가 21일 안 같은 지표 신규 key로 옮겨지면 기존 이벤트 날짜가 PATCH 이동, 원래 날짜는 복구 후 새로 생성(id churn) — `moved = remap_moved(engine.state, events, lo, hi)` — `if source_complete:`일 때만.
- [P3] [C1] bot.py:293 — dry-run에서도 `save_cache(days)`가 `nasdaq_cache.json` 덮어씀.

### housingbot (bot.py, source.py, regions.py)
A 추적: `SyncEngine(KiHFceW2e9N8, state/housingbot.db, absent="delete", state=state, cache 200/400)` bot.py:118, `sync(events, source_complete=True(하드코딩), source_window=(오늘-30d, None))` bot.py:131. fetch는 전부 아니면 전무(SourceError·0건이면 return, :90-95). 조회 창 모집공고일 [-180d, +90d], 이벤트는 start ≥ -30d만. 가드 >5 & >10%(창 안 ~234건 → ~23건). categoryId 항상 매핑(기타 폴백).
- [P0] [A1] bot.py:31-33,131 — 창 보호가 숫자상 안 맞음. 공고 A가 오늘-180일보다 오래되면 소스에서 빠지지만 그 공고의 이벤트 S가 오늘-30일 이후면 absent 후보 → S-A > 150일인 이벤트(계약 등)는 공고가 창 밖으로 나가는 날 삭제(이미 10~30일 지난 과거 이벤트). README:26 "공고→계약 종료 최대 ~170일" → 150일 한계 초과 사례 존재(해당 공고 수 미확인) — `ANNOUNCE_BACK_DAYS = 180`, `EVENT_BACK_DAYS = 30`, `source_window=(event_lo, None)` — `ANNOUNCE_BACK_DAYS` ≥ 170+30+여유(예 210).
- [P1] [F1] source.py:54,63 + bot.py:91 — `serviceKey`가 쿼리 → `raise_for_status`/ConnectionError 문자열의 URL이 그대로 `alert()`에 실려 stdout·로그·Discord 웹훅으로 — `raise SourceError(f"청약Home API 실패 page={page}: {e}")` → `alert(f"…: {e}")` — 키 마스킹.
- [P2] [A2] bot.py:131 + source.py:70 — source_complete 하드코딩 + 빈 batch면 matchCount 미달이어도 정상 종료 → 부분 결과면 가드 상한(~23건)까지 삭제(현재 perPage 1000·1페이지라 낮음) — `if not batch or len(items) >= match: break` — `len(items) < match`면 SourceError.
- [P3] [C2] bot.py:112-113 — dry-run에서도 legacy 이관 commit(state 빈 경우만, 현재 비작동).

### ipobot (bot.py, sources/ipo38.py)
A 추적: `SyncEngine(O2gLoYrjMM7e, state/ipobot.db, absent="delete", state=state, protect_human_edits=True, cache 200/400)` bot.py:203, `sync(events, source_complete=complete, absent_filter=absent_ok)` :223. complete = 상세 페이지 예외 ≥1이면 False(ipo38.py:173-175), 목록 예외·0건이면 return. 이벤트 창 [today-7, today+90), 목록은 청약 시작 ≥ today-60(상장까지 53일 여유). `orphan:` key는 창 무관 삭제. 가드 >3 & >10%(~81건 → 8건).
- [P1] [A2] sources/ipo38.py:139-155 — MAX_PAGES(4) 다 읽고도 cutoff 미도달이면 warning만, complete=True → 5페이지 이후 종목의 창 안 이벤트가 가드 상한(8건)까지 삭제(README:66 한계 명시) — `else: log.warning(f"목록 {MAX_PAGES}페이지까지 cutoff {cutoff} 에 못 닿음")` — 그 경우 complete=False.
- [P1] [A2] sources/ipo38.py:162-172 — 상세 페이지 200인데 라벨 셀 없음(구조 변경·오류 페이지)이면 `_detail_pairs` `{}`인데 `detail_ok=True` → 수요예측·환불·상장 None → 3건 absent, 종목 1개=3건이라 `> 3` 가드 미달로 삭제 — `item.detail_ok = True` — "공모청약일"/"종목명" 없으면 실패.
- [P3] [F3] run_dry.bat:4 — dry-run이 live와 같은 track 라벨 `ipobot`, run_apply.bat에 `chcp 65001`/`PYTHONIOENCODING` 없음(botkit이 stdout 재설정).
- [P3] [C2] bot.py:4,197-198 — dry-run에서도 최초 이관 저장(문서화, 현재 비작동).

### youthpolicybot (bot.py, source.py, config.py)
A 추적: `SyncEngine(LvzVxU_W6tDi, state/youth.db, absent="delete", state=state, protect_human_edits=True, cache 400/400)` bot.py:115, `sync(events, source_complete=complete, absent_filter=lambda r: r.key in candidates)` :135. complete=False: 2페이지 이후 SourceError, 고유 건수 < totCount(source.py:168), 창 안 이벤트 0(bot.py:101-103); 첫 페이지 실패·0건이면 return. 후보 = start [now-7d, now+180d) 안이면서 소스에 없는 행 + 같은 plcyNo의 다른 key(창 밖 포함) = 이벤트 창(신청기간 ∩ [today-7, today+180])과 일치. 가드 >5 & >10%.
- [P1] [F1] source.py:124,138 + bot.py:89 — `apiKeyNm` 쿼리 → RequestException 문자열의 URL이 SourceError에 실려 첫 페이지 실패 시 `alert()`로 stdout·Discord — `params = {"apiKeyNm": key, …}` / `raise SourceError(f"page {page} 실패: {last}")` — 키 마스킹.
- [P2] [A2] source.py:146,150,168 — `pagging.totCount` 없거나 0이면 루프 미실행, 첫 100건만으로 complete=True → 대부분 가드(>5)가 막지만 5건 이하 삭제는 통과 — `total = int((first.get("pagging") or {}).get("totCount") or 0)` — total==0이면 SourceError.
- [P3] [G3] source.py:150-160 — ~35페이지 무간격(sleep은 실패 시만).
- [P3] [E1] source.py:112 — 제목 없으면 `"(제목없음)"` summary.

## 생활 캘린더 (taxcal·couponcal·campingcal·convini·tumblbug)

공통: 토픽 5개 모두 다름(YXJVydUEl9m6 / 33wyyta_Fqdd / yPNcS1cyLfj0 / WZ8xOfyIJxLz / 3sEDrwwRTo75), 카테고리·state도 봇별 → 데드라인 3봇 간 공유 토픽 A3/A4 없음. 정상 확인: 전부 aware KST, allDay 00:00~23:59:59 KST, prefix 없음, 출처 문구 없음, categoryId None 경로 없음, 연도 하드코딩 없음, .env gitignore, bat ASCII+track 라벨+pause, sync run당 1회, `source_ok=` 오타 없음.

### taxcalbot (bot.py, seed.py)
A 추적: `SyncEngine(TOPIC, absent="delete", on_missing=ignore, adopt=topic, protect_human_edits=True, cache=cache_days())` bot.py:182, sync 1회(bot.py:187), source_complete 미전달(정적 seed, 0건이면 alert 후 return bot.py:163). `source_window=(min연도-01-01 KST, max연도+1-01-01 KST)` + `absent_filter`=key 연도 ∈ 생성 연도. 오늘 years=[2026], cache back 301/fwd 78일. 12월엔 [Y,Y+1], 1월엔 [Y+1] — filter가 Y년 key를 보호 → 롤오버 정상. key `{연도}|{seed}|{phase}` 안정.
- [P2] [A5] bot.py:98 / sync.py:450-451 — 이관 때 사람이 지운 2026년 이벤트 3건을 현재 seed 해시로 state에 넣어 MISSING(ignore)으로 막아둠. seed.py 문구·_DISCLAIMER·link가 하나라도 바뀌면 CHANGED → update 404 → 캐시 미스 → create로 부활(2026 key가 events에 남는 연말까지). README:42가 인정한 한계, 코드 가드 없음 — `state.put(key, eid, ev.content_hash(), ...)  # 사람 삭제` — 사람 삭제 key를 events에서 제외하는 표식.
- [P3] [C2] bot.py:172-176 — dry-run에서도 실 state 파일 열고 `import_legacy`가 커밋(현재 state 비어있지 않아 잠복) — couponcal처럼 `:memory:` 복사본.

### couponcalbot (bot.py, source.py, articles.py)
A 추적: `SyncEngine(33wyyta_Fqdd, absent="delete", on_missing=ignore, adopt=topic, protect_human_edits=True, cache 120/400)` bot.py:274, sync 1회(bot.py:288). `source_complete = not failed`(목록 8개: 최신+검색어 7, `fetch_html` None 또는 `list_type` 없음 → 실패) `and not LLMUnavailable`(bot.py:239). 본문 실패는 미반영이나 이벤트를 articles.db 캐시 전체에서 만들어 삭제로 이어지진 않음. window 없음, 대신 `absent_filter = key ∈ legacy-dup|* ∪ start_iso ≥ 오늘 00:00 KST`(bot.py:277-278) = 생성 조건 `ds >= today`(bot.py:83-86)와 일치 → 과거 보호. 과다 삭제 가드 >5 & >10% → keep(bot.py:281).
- [P0] [A4/G1] bot.py:296-306 + src/ingest_core/approval.py:140-150 — 승인 큐와 자동 등록 경쟁으로 중복. 저신뢰 묶음이 key K로 enqueue → 이후 같은 사업 고신뢰 보도자료가 묶음에 들어와 K가 ready → SyncEngine create(대표 g[0] 같아 summary/start 동일). 큐 항목은 회수되지 않고 남아, 사람이 나중에 ✅하면 러너 `_register_approved`가 `update_or_create_event(None, ...)`로 dedup 없이 create → 2건. 봇은 K가 events에 있으니 `approved_events`에서 제외(bot.py:133 `if k and k not in keys`) → 러너 사본은 state 밖이라 영구 잔존 — `if ev.key in keys: continue`(enqueue만 생략) — ready가 된 K의 큐 항목을 superseded 처리, 또는 `_register_approved`가 TopicCache (summary,start) lookup 후 없을 때만 create(승인 큐 쓰는 모든 봇 공통).
- [P1] [B1] source.py:85-88, bot.py:229-232 — 상세 페이지 200인데 셀렉터 없음(점검·차단 페이지)이면 `fetch_body`가 None이 아니라 `""`/잡음 → LLM 구조화 → 결과 캐시 → `_parse_fail` 없으면 재수신 안 함 → 일정 영구 누락, alert 없음 — `return "\n".join(p for p in parts if p)` — parts 비면 None + 본문 실패 집계 alert.
- [P2] [A7] bot.py:119, articles.py:51,59-61 — key `{route}|{g[0].prog}|{날짜}|{phase}` 변동 경로 2개: (a) `KEYWORD_ROUTES` 수정 → route 변경, (b) 파싱 실패했던 기사가 나중에 성공하면 옛 first_seen 유지(ON CONFLICT가 갱신 안 함) → `ORDER BY first_seen, published, news_id`상 묶음 g[0]가 바뀌어 prog 변경. → 옛 이벤트 absent 삭제 + 새 create, 엔진 `_delete`는 protect_human_edits를 안 봐 사람 편집 소실, 5건 이하면 가드 미달 — 대표를 "state에 이미 있는 key"로 고정.
- [P2] [A7] bot.py:67-87, articles.py:4-5 — 이벤트를 캐시 전체에서 영구 생성 → 일정 연기·정정 시 정정 보도자료가 새 날짜 key를 만들고 옛 날짜 이벤트도 그 날짜까지 공존(absent로 안 지워짐) — `if ds and ds >= today: out.append(Cand(phase="start", day=ds, **common))` — 같은 (route, prog, phase)는 최신 게시분 날짜만.
- [P3] [B2] bot.py:229,241-246 — LLM 파싱 실패 3회 누적 보도자료 alert 없이 영구 제외(warning만).
- [P3] bot.py:126-133 — private `approval._DEFAULT_DB` 직접 사용, sqlite 연결 미종료; 승인 항목은 날짜가 지나도 매 run Event로 투입.

### campingcalbot (bot.py, rules.py, rules.toml, watch.py)
A 추적: `SyncEngine(yPNcS1cyLfj0, absent="delete", on_missing=ignore, adopt=topic, protect_human_edits=True, cache 60/400)` bot.py:78, sync 1회(bot.py:82), source_complete 미전달(규칙 생성, fetch 없음). `source_window=(오늘-3 00:00 KST, 오늘+100 23:59+1분)` = rules.py:91-92 필터(`ws <= e.start <= we`)와 일치 → 오늘 2026-10-19~2027-01-31 KST, 창 밖 보호. `_months` 12→1월 정상. key `{src}|{rule}|{명목 anchor}` 순연 무관 안정.
- [P2] [D/E] rules.toml:91-116, rules.py:61 — KNPS `lottery_close`/`result`는 `shift=true`, `pay_deadline`은 `shift=false` → 5일이 주말·공휴일인 짝수달엔 결제 마감이 발표보다 먼저. 현재 창 실례: 2026-12-05(토) → 마감·발표 12/07(월) 10·13시, 결제 마감 12/06(일) 22시. README:63-64는 숲나들e만 기록 — `d = shift_forward(base) if r.get("shift") else base` — pay_deadline을 순연된 result +1일 22시로 파생, 또는 KNPS 순연 여부 확인 후 통일.
- [P3] [D1] rules.py:75-77 — `forest_period_monthly`는 시작만 순연, 끝은 명목일 → 4~8일 연휴면 start > end 가능, 가드 없음.
- [P3] [C2] bot.py:74-76 — dry-run에서도 `import_legacy`가 실 state에 커밋(잠복).

### convinibot (bot.py, store.py, extract.py, sources/*.py)
A 추적: `SyncEngine(WZ8xOfyIJxLz, absent="keep", on_missing="ignore", adopt=topic, protect_human_edits=True, back=70, fwd=400)` bot.py:143, `sync(events)` 1회(bot.py:158), keep이라 삭제 경로 없음. events = 기준일 ≥ 오늘-60일 묶음. key `{매장}|{정규화명}|{anchor}`는 매 run 전체 occurrence를 id 순 재계산해 결정론적, 같은 nk+anchor는 ±3일 병합.
(라인 번호는 리뷰어 보고가 어긋나 직접 재확인한 값)
- [P0] [B1] sources/bgf_press.py:24-34 — BGF 커서가 일시 오류 ID를 건너뜀. ID N이 네트워크/HTTP 오류(err=1)여도 miss로만 세고, N+1이 성공하면 `new_last = current_id`(N+1) → N은 다시 조회되지 않아 영구 누락. 200인데 제목·본문·날짜가 빈 경우(bgf_press.py:54-67, err=0)도 동일(Google News CU 쿼리가 일부 보완) — `else: consecutive_miss += 1` … `new_last = current_id` — 오류 ID가 있으면 커서를 첫 오류 ID-1에서 멈추거나 재시도 목록 저장.
- [P1] [B1] bot.py:68-72, sources/google_news.py:99, sources/newswire.py:70 — alert는 세 소스 전부 실패일 때만. GS25는 Google News로만 들어오는데(README:13) 8쿼리 전부 실패해도 `log.warning` → GS25 수집 0 미감지. 뉴스와이어는 셀렉터 0건(레이아웃 변경)이어도 `return articles, True` — `log.warning("일부 소스 실패: %s", failed)` — 소스별 실패·N일 연속 0건 alert.
- [P2] [G2/G3] sources/google_news.py:73,78 — known 판정 전에 모든 entry(최대 8쿼리×15)를 매 run `gnewsdecoder(entry.link, interval=0)`로 디코딩(Google 연속 요청), `known(source, real_url)`의 source가 매장별이라 다른 매장 쿼리로 잡힌 같은 기사가 신규로 들어가 필터·추출 LLM 재비용 — entry.link로 known 선판정 + source 통일.
- [P2] [A8/G1] bot.py:113-120, store.py:294-297 — 이름 판정이 LLMParseError로 2회 실패하면 None("별개") → `match_cache`에 `same=0` 영구 저장 → 같은 상품 영구 중복 이벤트 가능 — `return None  # 두 번 다 실패 → 별개로 판정(캐시됨)` / `cached[k.norm] = 1 if k is hit else 0` — 파싱 실패는 캐시 말고 held.
- [P2] [B1] sources/bgf_press.py:24,46-47 — 404는 오류 아님 → BGF ID에 10개 넘는 공백이 생기면 매 run 같은 구간에서 멈추고 `ok=True`라 alert 없음(실제 ID 간격 사례 미확인).
- [P3] [F2] store.py:222-246,248+ — `items()`/`resolve()`가 전 기간 occurrence 매 run 재계산, resolve O(n²), state.db 정리 경로 없음.
- [P3] store.py:216-219 — `pending_counts`가 llm_fail≥3 영구 제외 행까지 미처리로 세 alert 숫자 부풂.

### tumblbugbot (bot.py, sources/tumblbug.py)
A 추적: `SyncEngine(3sEDrwwRTo75, absent="delete", on_missing=ignore, adopt=topic, protect_human_edits=True, cache 60/400)` bot.py:143, sync 1회(bot.py:157). `source_complete = res.complete and bool(events)` — complete는 페이지 예외 3회 소진·MAX_PAGES 소진·재수집 실패면 False. `source_window=(now, now+60일)`; 소스는 진행 중 전체, 신규 등록은 마감 오늘~+30일, sticky는 무제한 → 마감 당일(start=당일 00:00 < now)·과거 보호. 삭제 후보 있으면 재수집 합집합 + 가드(>5 & >10%). key `tb|{pid}` 안정, `out.setdefault(pid)`. forget_before 120일(dry-run 생략).
- [P1] [A2] sources/tumblbug.py:68-75,119-120 — 완결 판정이 휴리스틱: (a) 200인데 `body`/`result` 없으면 `contents=[]`, `has_next=False` → PageError 없이 complete=True; (b) "진행 중 0건인 꽉 찬 페이지"를 목록 끝으로 봄 → 정렬 가정(README:20) 깨지면 중간에서 멈추고 complete=True, 결정론적이라 재수집도 똑같이 잘림 → 5건/10% 이하 손실은 가드 통과해 실삭제 — `body = r.json().get("body") or {}` … `if not has_next or (contents and not ongoing): return FetchResult(..., True, ...)` — `body.result` 없으면 PageError, 수집 건수 급감 시 complete=False.
- [P2] [A2] sources/tumblbug.py:84-91,115-118 — 진행 중인데 `endDate` 없음/형식 변경이면 `_parse` None으로 조용히 빠짐 → complete=True에서 그 key 건별 absent 삭제(가드 미달) — `except ValueError: return None` — parse 실패 ≥1이면 complete=False.
- [P3] [C2] bot.py:118-120 — dry-run에서도 `import_legacy`가 실 state에 커밋(잠복).
- [P3] [F3] run_dry.bat:5 — dry-run bat도 `track.py tumblbugbot` 호출 → 대시보드 실행 기록 오기록.

## 굿즈 (daisobot·theatergoodsbot) — **둘 다 SyncEngine 미이식**

두 봇 모두 W2 이전 자체 sync(state.json `{key: event_id, payload…, missing, cancelled}`, 자체 `sync()`, `create_event`/`update_event`/`delete_event`/`_post('/categories/create')` 직접 호출, botkit.run 미사용)다. A는 자체 로직의 대응물로 추적. 토픽(daiso `L8fI7uBj-6jZ`, theater `VBMiAMPv37Oy`)은 각 봇 단독, 둘 다 `run_tier_2x.bat:31-32`에서 순차 실행.

### daisobot (main.py, config.py)
A 추적: key=`exbtNo`(dict라 중복 없음), sync run당 1회(main.py:523). 부재 = state 전체 중 이번 `live`(필터 A/B 통과 + 시각 파싱 성공)에 없는 key; 2회 연속 부재 & `start > NOW`일 때 DELETE → 과거 보호. 소스 완전성 = fetch 예외(RequestException/SchemaError/ValueError) 시 early return(L492-503)뿐, 응답은 왔는데 쓸 수 있는 건 0인 경우 미방어. 입양 = 토픽 `[NOW-7d, max(start)+2d]`에서 summary 단독 매칭.
- [P0] [A2/B1] main.py:236-238,429-435 — 응답은 왔는데 파싱/필터 결과 0건이면 정상 부재 처리 → 2 run(~12h) 뒤 미래 이벤트 전부 삭제. 스키마 검사(L99-102)는 키 존재만 봄 → `exhStDtm` 형식이나 `PRE_` 태그 접두사가 바뀌면 `live`가 비고 경고는 stderr WARN 한 줄. 엔진의 "events 0건이면 absent 스킵"에 해당하는 가드 없음 — `print(f"  [WARN] {no} exhStDtm 파싱 실패 → 스킵…", file=sys.stderr); continue` / `if rec["missing"] < C.MISSING_STREAK_TO_CANCEL or not start or start <= NOW:` — 기획전 ≥1인데 등록 대상 0 또는 파싱 실패 ≥1이면 [HEALTH-ALERT] + 부재 판정 스킵; 장기적으로 SyncEngine 이식.
- [P1] [A4/A8] main.py:364,370 — 새 key는 summary만 같으면 기존 이벤트 입양(시작 시각 미비교). 같은 이름 기획전 2개 동시면 event_id 공유 → 각 rec가 자기 payload와 같다고 보고(L378) 스킵 → 한 건 영구 누락. 143 run 동안 공유 0건이라 P1(같은 메커니즘이 theatergoodsbot에선 실제 발생) — `by_summary.setdefault(ev["summary"], ev)` / `eid = rec.get("event_id") or (by_summary.get(e["summary"]) or {}).get("id")` — (summary,start) 매칭 + 다른 key가 쥔 id 제외.
- [P1] [E3] main.py:319-323,353-355,372-377 — `/categories/tree` 실패 → `{}` → ensure_categories가 "사전예약" 카테고리 재생성(중복), cid None이면 categoryId 없이 등록, payload 비교에 category가 없어 영구 미교정. stderr WARN만 — `if not isinstance(body, list): return {}` — tree 실패와 "없음" 구분, 실패면 쓰기 중단+alert, payload에 category_id.
- [P2] [B1/G1] main.py:149-151 — `selPrirNtcnCnt`가 200인데 `data` null/빈 값이면 모든 기획전 알림수 0 → 미래 이벤트 전부 description "50명 미만"으로 PATCH, 다음 run 원복 PATCH; B 경로 판정도 무력화 — `for x in (data.get("data") or []):` — 요청 소재가 있는데 응답 0이면 이전 ntcn 유지 + WARN.
- [P2] [규칙/C1] main.py:387-413,439,335 — SyncEngine 밖 직접 쓰기(update/create/delete_event, `_post('/categories/create')`) — W2 규칙 위반, 위 P0/P1의 구조적 원인 — SyncEngine 이식(key=`daiso|{exbtNo}`, `source_window=(NOW, +∞)`, `adopt_scope="category"`).
- [P3] [C1] main.py:508,284-300 — dry-run에서도 `ntcn_log.jsonl` append·회전(캘리브레이션 데이터 오염).
- [P3] [C] main.py:304-315 — `read_call`이 모듈 상수 `_dwc.DRY_RUN`만 끔. 클라이언트는 환경변수도 읽고(client.py:79-81) `/categories/tree`는 `_READ_ONLY_PATHS`에 없음 → dry-run에선 가짜 응답, 매번 "[DRY] 카테고리 생성", cid 검증 불가.
- [P3] [B4] main.py:439-444 — 사람이 지운 이벤트는 delete 404 → 시작 시각 지날 때까지 매 run "DELETE 실패".
- [P3] [D2] config.py:25 — 캘리브레이션 TODO 주석 낡음(W3에서 1000 유지 결론).
- [P3] [D1] config.py:31 — `EVENT_DURATION_MIN = 0`(start==end), W3에서 월간뷰 확인한 의도된 설계 — 기록만.

### theatergoodsbot (main.py, sources.py, filters.py, config.py)
A 추적: key=`{source}:{sid}`(`_dedupe`), sync run당 1회(main.py:418). 부재 = `ok_sources`(이번 run 예외 없는 체인, main.py:384)의 key 중 `live`(필터·registerable 통과)에 없는 것; 2회 연속 부재 & `start > NOW`면 DELETE. 소스 완전성은 체인 단위 예외로만 — 빈 결과·블록 파싱 실패·페이지 상한은 "ok". 입양 = 토픽 `[NOW-120d, max(end)+2d]` summary 단독.
- [P0] [A4/A8] main.py:246-257 + filters.py:241-245 — **현재 발생 중.** summary에 체인명을 안 넣는 규칙 때문에 같은 영화·같은 주차 CGV/롯데/메가박스 항목이 같은 summary → 두 번째 체인이 첫 번째 event_id를 입양해 PATCH, 이후 양쪽 다 "payload 동일"로 스킵(L268-271) → 체인 하나만 캘린더에 남고 categoryId·link는 마지막 쓴 체인 것. 한쪽이 취소로 DELETE하면 공유 이벤트가 사라지고 다른 key는 스킵 상태라 재생성 안 됨. **state.json 628 key 중 52개 event_id가 2개 이상 key에 공유**(제거된 goodstrades 포함; 공식 체인끼리 ~36그룹). 진행 중 예: `ps5dHyldaDWT`(lotte:201010016926713 / cgv:202610206105, 10/21~10/27 "스트리트 파이터 2주차 특전 배부"), `wUhRZwXa5at-`(lotte/megabox), 같은 체인 다른 날짜 `L8dG6asl4SKL`(cgv 8/22·9/5) — `by_summary.setdefault(ev["summary"], ev)` / `eid = rec.get("event_id") or (by_summary.get(e["summary"]) or {}).get("id")` — 입양을 (summary,start,categoryId) + 다른 key가 쥔 id 제외로; 기존 공유 그룹 분리·누락 체인 재생성(데이터 패치).
- [P0] [A2/B1] sources.py:98-103,145-150,183-196 + main.py:97,323 — 체인 응답은 왔는데 행 파싱 전부 실패하면 `[]` + `fails[name]=0`(ok) → 2 run 뒤 그 체인 미래 이벤트 전부 DELETE. 메가박스는 `tit`/`date` 정규식 실패 블록을 조용히 skip, 롯데·CGV는 첫 행에 키만 있으면 날짜 형식이 바뀌어도(`_d_dots`/`_d_compact` None) 전 행 버림 — `if not mt or not md: continue` / `fails[name] = 0` — 원본 행 ≥1인데 정규화 0 또는 버림 비율 임계 초과면 SchemaError, 0건 체인은 부재 판정 제외.
- [P1] [A2] sources.py:77,114-117,158-161 — 부분 페이지를 완전 결과로 처리. 롯데 `LOTTE_PAGES=2`(최대 100건) 상한, TotalCount >100이어도 경고 없이 멈춰 넘친 건이 부재 삭제 가능(현재 45건). CGV `totalCount` 없으면 0 → 첫 페이지(100)에서 멈춤, 중간 페이지 빈 응답이면 break — `for page in range(1, C.LOTTE_PAGES + 1):` / `total = int(data.get("totalCount") or 0)` — 수집 < total이면 그 소스를 ok_sources에서 제외 + WARN.
- [P1] [E3] main.py:209-228,258 — `/categories/tree` 실패 → `{}` → 체인 카테고리 4개 재생성(중복), cid None이면 경고 없이 categoryId 없이 등록, payload(L261-267)에 category 없어 영구 미교정 — `cid = cats.get(C.CHAIN_LABEL.get(e["chain"], "기타"))` — tree 실패면 쓰기 중단+alert, payload에 category_id.
- [P2] [규칙/C1] main.py:278-302,332,225 — SyncEngine 밖 직접 쓰기(W2 규칙 위반), 위 P0 두 건의 구조적 원인 — SyncEngine 이식(`adopt_scope="category"`, 체인별 `absent_filter` + 체인별 source_complete).
- [P3] [E1] filters.py:233-249,284 — `split_title` 실패(movie=None, goods=원제) 시 summary "원제 원제" 반복, description `&lt;&gt;`(현재 0건).
- [P3] filters.py:102-110,252-276,291 / main.py:168 — 제거된 goodstrades 잔여 코드.
- [P3] [F5] main.py:420 vs README.md:84 — README "exit 0", 코드는 쓰기 실패 시 `return 1`. README.md:53 giveaway 설명도 코드(filters.py:137-140)와 다름.
- [P3] [B4] main.py:332-337 — 이미 지워진 이벤트 DELETE 404 → 시작일까지 매 run 실패 + exit 1.
- [P3] [C] main.py:195-206 — daisobot과 동일한 `read_call` dry-run 무효.
- [P3] [F4] run_sched.bat — 수동용 bat이 티어 run.bat과 같은 state.json을 락 없이 씀(겹칠 때만), track.py 라벨이 있어 수동 실행이 헬스 기록에 섞임.

## 티켓·세일

### megasale_bot (bot.py, sources/amazon.py)
A 추적: `MegasaleEngine(doMQDPF5gu5l, absent="keep", protect_human_edits=True, cache 60/800)` bot.py:321-323, on_missing/adopt_scope 기본(ignore/topic) + run 내 (summary,start) 충돌만 막는 `shared` 가드. `engine.sync(events)` run당 1회(bot.py:328), source_complete/window/filter 없음 — keep이라 삭제 경로 없음. key `{seed_id}|{year}`(월별 `|{year}-{MM}`) 안정, seed id 39개 유일.
- [P2] [A4] bot.py:321 — adopt_scope 기본 "topic". `adopt_existing`(카테고리 일치)이 놓친 NEW·404 복구가 엔진의 토픽 전체 `cache.lookup`(카테고리·점유 무시)으로 가 다른 머천트 이벤트에 붙을 수 있다 — `cache.lookup = lambda summary, start_iso: (None if (summary, _iso_z(start_iso)) in self.shared else lookup(...))` (bot.py:207) — `adopt_scope="category"` + lambda가 kwargs 받도록(그대로면 TypeError).
- [P2] [D1] bot.py:187 — 종료를 시드 tz와 무관하게 KST 23:59:59 고정 → PT 판매처 1일 세일(블프 등)이 KST 17:00~23:59:59 ≈7시간 이벤트(실제 종료 다음날 16:59 KST). `end_time_local` 미사용 — `start=_local(s, sch["start_time_local"], tz), end=_eod(e)` — 현지 EOD→KST 환산 여부 결정(README:27은 현 규칙 명시).
- [P2] [B1] sources/amazon.py:162,166-169 — 기사 fetch 실패 조용히 `continue`, 키워드 기사 0건도 info만. 로그상 10-16 이후 4 run 전부 `amazon: 키워드 기사 0건`(옛 코드는 9월 히트) — 셀렉터 깨짐 여부 미확인 — `if not page: continue` — 예상 창 ±30일 내 0건이면 alert.
- [P3] [B2] amazon.py:192-194 — LLMParseError는 캐시 안 해 매주 같은 페이지 재질의.
- [P3] [G2] bot.py:228-229 — adopt_existing이 TopicCache 별도 로드 + 엔진 재로드 → `cache=` 공유.
- [P3] [E3] bot.py:151 — `MERCHANT_CATEGORY_IDS[seed["bracket"].strip("[]")]` 새 bracket이면 KeyError로 run 전체 사망(None 등록은 아님).
- [P3] [D2] seed_events.json description_template — "과거 일정: 2025년…" 하드코딩, 해마다 낡음.
- [P3] [F5] README.md:7 — "38개 시드" vs 실제 39건.
- [P3] [F2] amazon.py:71 `llm_page`, bot.py:87 `confirmed` 정리 없음(현재 소량).

### ticketbot (bot.py, scraper_interpark.py, scraper_yes24.py, scraper_melon.py)
A 추적: 토픽별 엔진 2개(MTG8oh05P0iv, 46z005GBfOYB) `absent="keep", protect_human_edits=True`, on_missing/adopt_scope 기본, cache 60/400 (bot.py:289-290). `sync(to_sync, source_complete=not bad)` 토픽당 1회(bot.py:292) — `bad`는 소스 예외/소스 전체 0건만, 페이지·장르 일부 실패 미반영(keep이라 영향 없음). window/filter 없음. key `{소스}|{공지id}` 안정, 중복 bot.py:265에서 제거. 두 토픽을 쓰는 다른 봇 없음(canceltickbot은 eDQQHdy1bN4d).
- [P1] [B1] bot.py:233-235,247 — 소스 하나 0건은 warning뿐, alert는 3곳 전부 0일 때만. 스크래퍼들은 예외를 삼키고 `[]`(scraper_interpark.py:57-59, scraper_yes24.py:98-102, scraper_melon.py:60-62). **2026-10-21 19:3x부터 인터파크 0건(`[인터파크] API 호출 실패 (offset=0): Expecting value`)인데 HEALTH-ALERT 없음** — `if not got: log.warning(f"{name} 0건"); bad.append(name)` — 소스별 0건/예외 alert.
- [P2] [B1] scraper_interpark.py:57-59 — 중간 offset 실패 시 `break`로 부분 반환, `bad` 미반영. 멜론 장르·페이지 실패(scraper_melon.py:161-164), YES24 장르 색인 실패(scraper_yes24.py:160-162 STATS만)도 동일. 장르 색인 누락 → 제목 키워드 분류 → 잘못된 토픽이 bot.py:261 토픽 고정으로 굳음.
- [P2] [A4/G1] bot.py:289 — adopt_scope "topic"이라 404 복구가 토픽 전체 (summary,start) 조회 → 같은 제목·시각의 다른 소스 key가 쥔 이벤트를 PATCH(카테고리·link 덮어씀, 두 key가 한 id 공유) — sync.py:387 `return self.cache.lookup(summary, start)` — `adopt_scope="category"`.
- [P2] [G3/F4] scraper_yes24.py:284→166-176 — 매 run 공지 ~100건마다 axRead POST 무간격. canceltickbot이 같은 스크래퍼를 같은 2x 티어(11:00·19:30)에 또 돌려 YES24·인터파크 부하 2배 — sleep/캐시/시각 엇갈림.
- [P3] [G2] bot.py:114-115 — plan_adopts가 TopicCache 별도 로드(토픽당 2회).
- [P3] [F2] bot.py:188-189 — `yes24_perf`·`sync_state`(keep, forget_before 없음) 무한 증가.
- [P3] [D1] utils.py:90-94, scraper_melon.py:188/194 — 멜론 날짜만 주어지면 00:00 KST, 당일 오픈은 `pre_dt < now`로 skip. 현 state에 멜론 00:00 시작 0건 → 실제 발생 미확인.
- [P3] [A8] scraper_interpark.py:82 — `str(item.get("noticeId", ""))` null이면 "None" key 충돌 후 조용히 탈락(API null 여부 미확인).
- [P3] scraper_interpark.py:45 — `while True` 페이지 상한 없음(주석은 "최대 500건").
- [P3] [F5] README.md:13 — "공연장 없을 때만 /Perf"인데 코드는 장르 없을 때도(scraper_yes24.py:292).

### canceltickbot (bot.py, interpark.py, verdicts.py, names.py, tb.py)
A 추적: `SyncEngine(eDQQHdy1bN4d, absent="keep", protect_human_edits=True)` bot.py:322, on_missing/adopt_scope/cache 기본, 단독 토픽. `sync(..., source_complete=complete)` run당 1회(bot.py:324), `complete = not (bad or rank_bad or held)` — playSeq/notice_goods/remain 실패 미반영(keep이라 영향 없음). window/filter 없음. 중복은 bot.py:213-219 `uniq`에서 key·(summary,start)로 제거. **W1 key 불안정(아래 P0).**
- [P0] [A7] bot.py:90,115-129 — W1(미입금표) key·summary가 "지금 같은 방출 시각을 공유하는 플랫폼 집합"으로 정해지는데, collect가 오픈 지난 공지를 버린다(`rows += [t for t in got if t.open_dt >= now]`). 예: 같은 날 YES24 12:00·멜론 20:00 오픈 → 방출 D+2 00:00 동일 → 카드 1장 `YES24|yid|미입금표` "(YES24·멜론)". 19:30 run에서는 YES24 행이 빠져 key `멜론|mid|미입금표`·제목 "(멜론)"으로 NEW → (summary,start) 조회 빗나감, adopt_legacy도 이미 쥔 id 건너뜀(bot.py:252) → 같은 시각 카드 추가 생성, 옛 카드는 keep이라 잔존. 공지가 PLATFORMS 뒤 플랫폼부터 올라와도 동일 — `ev = Event(key=f"{plats[0]}|{tb.notice_id(main)}|미입금표", summary=w1_summary(name, plats), ...)` — key를 공연 단위(`미입금표|{show id}|{start:%Y%m%d%H%M}`)로, 또는 오픈 지난 행도 W1 계산엔 유지. 현재 state(W1 21행)엔 실중복 없음.
- [P1] [B1] bot.py:88-89, interpark.py:108-112 — 소스 0건·랭킹 실패는 `bad`/`rank_bad`뿐 alert 없음. 로그상 10-21 19:37부터 `실패 소스 ['인터파크','CONCERT','MUSICAL']`, 취소마감 21→0건인데 알림 없음. notice_goods(interpark.py:36)·playseq(:74)도 예외 삼키고 `{}`/`None`(bot.py:140-142 warning) — alert 추가.
- [P1] [B2] verdicts.py:115-117 — LLM 응답 누락 항목을 `low`로 캐시, `get()`이 조회마다 last_seen 갱신(verdicts.py:78) → 180일 정리에도 안 걸림 → 부분 응답이면 그 공연은 제목이 바뀌기 전까지 영구 미등록(P0 경계, 현재 `응답 누락` 0건) — `vmap.get(j, {"tier": "low", "reason": "응답 누락"})` — 누락은 캐시 말고 held.
- [P2] [G1] bot.py:126,132 — W1 본문 장르(`tb.genre_of(main)`)·location(`main.venue`)이 YES24 원시값. ticketbot의 `yes24_perf` 안정화(ticketbot bot.py:159-162 "매 run 값이 흔들리면 PATCH")를 안 거쳐 반복 PATCH 가능.
- [P2] [C1] bot.py:301-303→281-292 — `--refresh-adopted`는 `--dry-run`과 함께여도 `sync_state` UPDATE·`adopted_legacy` DELETE — `db = sqlite3.connect(STATE_PATH)` dry_run 확인 없음.
- [P2] [G3] interpark.py:92-99 (bot.py:151) — 뮤지컬마다 21일 창 회차 전부 잔여석 API 무간격 호출. notice_goods(interpark.py:28-46)가 같은 notice-list 재수신(G2).
- [P3] [B6] interpark.py:119 — `date(int(e[:4]), ...)` try 밖 → playEndDate 이상하면 run 전체 크래시.
- [P3] bot.py:314-316,325-327 — ListFailed/SyncAborted에 alert 후 `return 0`(티어 RETRY 안 걸림). bot.py:306-308은 매진급 0건(정상)에도 매번 alert.
- [P3] [G2/F2] bot.py:236-237 — adopt_legacy가 TopicCache 별도 로드(이관 완료 README:160), `ip_goods`·`sync_state` 정리 없음.

## 그룹 공통 패턴

1. **"HTTP 200인데 비었거나 구조가 이상함"을 완전한 소스로 본다 (P0·P1의 대부분).** `source_complete`는 예외·HTTP≠200 경로만 False로 만들고, 하위 조각(월 페이지, 대회, 체인, 상세 페이지)의 0건·표 없음·라벨 없음·totCount 없음은 성공으로 흘러 그 조각만큼 absent 삭제된다. 엔진은 "events 전체 0건"만 막는다. 해당: KFA 월 페이지, FD/ESPN, KBO (월,시리즈), 네이버 월별, BNE·STL·CPT, KIND·FMP·AV, Nasdaq `data` null, 38 상세 `{}`, 온통청년 totCount, 텀블벅 body, daiso/theater 파싱 0건. → 규칙 제안: "그 조각에 state 행이 있는데 응답이 0이면 실패", "원본 행 ≥1인데 정규화 0이면 SchemaError".
2. **삭제 과다 가드가 봇마다 따로이고 분모가 틀리거나, 가드 미만 삭제는 무경보.** PLBOT(events 20%·최소 3), KBObot/ipobot/econcal/housing/tumblbug/coupon(>N & >10%), lckbot·lckteambot(분모가 창 밖 포함 전체 state → 가드 무효). 1번의 부분 손실이 "소량 조용한 삭제"가 된다. `stats.absent_skipped`도 어느 봇도 alert로 올리지 않아 삭제 판정이 며칠씩 꺼져 있어도 모른다. → 엔진 공통 가드(dalbots-bmi.66) + absent_skipped·삭제 건수 alert.
3. **경고·매핑 실패가 그 run의 쓰기 판단에 반영되지 않는다.** 팀/카테고리 매핑 실패(koreafootball, koreansoccerguys), 시즌 폴백(fgcbot), 부분 소스 실패(ticketbot·canceltickbot·convini·KBObot), MAX_PAGES 미도달(ipobot)이 alert/warning만 남기고 absent 판정은 그대로. 반대로 소스 하나가 죽어도 "전부 0건"일 때만 alert라 인터파크 장애가 하루째 무경보. kboticketbot의 `source_complete=not no_cat`이 맞는 예.
4. **창·key가 실제 소스 범위/안정 식별자와 어긋난다.** 창: housing 180-30=150 < 170일, fgcbot 검색 기준(tournament.startAt) ≠ 저장 start, 비연속 `--months`(KBObot·msibot). key: 플랫폼 집합(canceltickbot W1), 셀 원문(fgcbot STL), LLM 출력(lckteambot 피드), 묶음 대표 g[0](couponcal), 회사명(stockmarket ir). summary 단독 입양(daiso·theater)도 같은 뿌리로 id 공유·영구 누락.
5. **SyncEngine 밖 쓰기·state 변경과 dry-run 누수.** 미이식 봇(daisobot·theatergoodsbot·worlds_bot)과 승인 러너(`ingest_core.approval._register_approved`, dedup 없이 create — couponcal P0)가 엔진 보장을 우회한다. `SyncState.import_legacy`가 dry-run에서도 commit(taxcal·camping·tumblbug·kboticket·koreansoccer·lck·msi·fgc·stockmarket·housing·ipo, 대부분 잠복), econcal `remap_moved`, canceltickbot `--refresh-adopted`. → botkit에 "dry-run이면 메모리 복사본" 헬퍼, 승인 러너에 TopicCache dedup.

부수: API 키가 URL 쿼리에 있고 requests 예외 문자열을 그대로 alert/로그에 쓰는 봇(housing·youth → Discord까지, stockmarket → 로그) — botkit 공통 마스킹 필요(.117과 같은 패턴).
