# W7 리뷰 — VOD/미러 5봇 + 공통 운영

읽기 전용 감사(2026-10-22). 기준: `W7_REVIEW_CHECKLIST.md`, `W2_REBUILD_BRIEF.md`, `W4_PLAN_vod.md` §2 R-A~R-H. 파일 수정·봇 실행 없음(스케줄러는 `Get-ScheduledTask` 조회, agent.db·봇 DB는 `mode=ro`로만 읽음). VOD 5봇은 봇별 서브리뷰 후 P0/P1 근거 줄을 직접 다시 확인했다. 설계 변경 제안은 하지 않고 규칙 위반·실패 경로만 적는다.

## 요약

| 대상 | P0 | P1 | P2 | 비고 |
|---|---|---|---|---|
| chzzkvodbot | 1 | 2 | 4 | MAIN_REUSE 실패 → 공지 고아 삭제 / 시각 미반영 / 교차 VOD 315건 |
| stellivebot | 1 | 2 | 6 | 자기 고아삭제가 MissingGuard 발동 → 메인 영구 누락 / 메인 고아정리 indiv_ok 미확인 / dedup-main 전 카테고리 |
| aesthervodbot | 0 | 1 | 4 | 락은 정상(OS 락). 치지직 실패 무음 |
| cluezvodbot | 0 | 1 | 4 | aesther와 같은 치지직 무음 + 수동 bat 락 없음 |
| streamersyncbot | 0 | 0 | 5 | SyncEngine 호출 정확. 수동 겹침 시 영구 중복 |
| botkit.vod | 0 | 0 | 1 | safe_summary 끝 공백 → 영구 PATCH |
| bot_scheduler·health_check | 0 | 0 | 8 | 라벨 62개 대조 일치, 감시 공백·재시도 경로 |
| bot_dashboard | 0 | 0 | 0 | 경로 62개 존재 |
| dalwiki-agent health | 0 | 0 | 2 | 레지스트리 미등록 토픽 0 |

## VOD/미러 5봇

### chzzkvodbot (chzzk_vod_bot.py, HEAD ed6109b)
- [P0] [R-D] chzzk_vod_bot.py:896-907 — MAIN_REUSE PATCH가 실패하면 후보가 `orphans`에 남아 같은 run의 prune(928-942)이 지운다. 후보는 `_notice_rank`로 **카페 공지가 있는 고아부터** 고르므로 사람이 넣은 공지 이벤트가 삭제되고, 다음 run은 MAIN_CREATE(새 id, SEO 손실). 400처럼 결정적 실패면 매 run 반복 — `if new_main_id: ... orphans.remove(cand)` / `continue` (else 없음). — else에서 `orphans.remove(cand)` + `synced_main_ids.add(cand["id"])` + 경고(MAIN_SYNC 실패 처리 831-835와 동일).
- [P1] [G1] chzzk_vod_bot.py:811-817 — 메인 미러 변경 판정이 summary·description뿐이라 indiv의 시작시각·end·allDay가 바뀌어도 `main_unchanged` → 메인 캘린더 시각이 조용히 옛 값으로 남는다 — `if m_summary == main_summary and desc_same: stats.main_unchanged += 1 ... continue`. — 비교에 start/`_end_for`/allDay 추가(`.000Z` 정규화 기준).
- [P1] [A8] chzzk_vod_bot.py:538-547 — VOD 매칭이 '이 이벤트에 이미 붙었는지'만 보고 다른 이벤트에 붙은 VOD도 ±6h면 또 붙인다 — `if db_vod_exists(conn, m.name, ev_id, video_no): continue` / `if diff <= tolerance_ms: matched_vods.append(v)`. 실사례: 따효니 10-21 CREATE_NEW 2건에 서로의 VOD가 교차 APPEND(log 35571-35572). vod_map에서 (멤버, video_no)가 2개 이상 이벤트에 걸린 경우 315건(10월 이후 17건), 메인 미러로 전파. — 다른 이벤트에 이미 쓴 video_no와 이번 run 배정분을 후보에서 제외(series 분할편은 1부 이벤트일 때만).
- [P2] [R-A] chzzk_vod_bot.py:782-793, 819-830 — complete=False여도 30분 근사 매칭+PATCH+`db_main_save`가 돈다. 이건 '기존 매핑 PATCH'가 아니라 새 매핑 생성 — 청크 실패로 진짜 주인 indiv가 `claimed_by`에 안 들어가면 다른 indiv가 그 메인을 가져가 덮어쓴다(발생 미확인) — `if mev_id in synced_main_ids or claimed_by.get(mev_id, ev_id) != ev_id: continue`. — 근사 매칭은 `complete`일 때만.
- [P2] [F4] chzzk_vod_bot.py:1025 — 티어는 순차라 안전하지만 수동 run.bat/sync_main*.bat/run_one.bat에 락이 없고 `sqlite3.connect(DB_PATH)` 기본 5s·busy_timeout 없음. 티어와 겹치면 같은 need_main으로 MAIN_CREATE 중복(다음 run prune으로 정리되지만 id 변경). — aesther처럼 파일 락 + busy_timeout.
- [P2] [R-B] chzzk_vod_bot.py:744-745 — sweep 윈도우를 indiv 시각으로만 정해, 매핑된 메인이 윈도우 밖(사람이 이동·경계 근사)이면 MISSING 기록 → 913 MAIN_CREATE 영구 생략, 흔적은 info 로그뿐(현재 missing_keys 0행, 발생 미확인) — `missing_now = guard.sweep(live_map, set(main_by_id))`. — 새 MISSING 발생 시 alert 1줄 또는 `get_event`로 재확인.
- [P2] [G2] chzzk_vod_bot.py:691 — 메인 토픽 전체(3일 청크 ≈8회)를 멤버마다 재조회(15회) + 매칭 메인 `events/get` 매 run ≈224건. — 메인 조회 run당 1회.
- [P3] [R-B/B4] dalwiki client.py:521-562(event_update 경유) — 메인 PATCH 404 → create 폴백이 `is_missing`을 안 거치고, 메인 캐시 로드 실패(1038-1042)는 warning만. 캐시 lookup에 카테고리 조건 없음(W4 문서 '휴방' 함정). 조회 직후 PATCH라 경합 때만.
- [P3] [C1] chzzk_vod_bot.py:128, 743, 266 — dry-run에서도 DROP/CREATE DDL 실행(무해). dry-run `event_create`가 상수 `"DRY_RUN_ID"`라 미러 시뮬레이션 정확도 저하.
- [P3] [R-E] chzzk_vod_bot.py:1100-1106 — 전원 실패 원인이 치지직 쪽이어도 'dal.wiki 조회 전원 실패' 문구. `--members` 미일치 시 exit 0.
- [P3] [A7] chzzk_vod_bot.py:113-123, 743 — vod_map/main_map 키·MissingGuard scope가 표시명(member_name). 개명 시 전부 미매핑 → 재생성 경로. channel_id 권장.
- OK: R-A(fetch는 197 한 곳, CREATE_NEW는 `indiv_ok and not n_desc_failed` 593, 불완전 시 REUSE/CREATE/PRUNE 873-878 return, sweep/forget `if complete` 744) · R-B(Tombstone 잔재 없음, scope `main:{멤버}`, CREATE 직전 is_missing 913) · R-D(MAIN_SYNC 실패·`_desc_failed` 보존 831-835·795-798) · R-F(1053-1076) · R-E(1100-1102 alert+exit 1) · C(쓰기 전부 dry_run 조기반환 248/265/274, db 저장 `_DRY_RUN` 가드 140/167) · B4(572/638/670/823/921, 삭제 bool 확인 934-941) · 30분 근사 중복 제외(787) · end=start 규칙(`_end_for` 199-210).

### aesthervodbot (aesther_vod_bot.py, HEAD 722076e)
- [P1] [B1] aesther_vod_bot.py:518-523, 543-545 — 치지직 VOD API 예외·응답 형태 변경이 warning 한 줄로 끝나고 alert가 없다 → VOD append가 조용히 영구 정지 가능(삭제·중복은 없음) — `except Exception as e: log.warning("Chzzk fetch error ..."); break` / `if not videos: break`. — 예외 또는 page 0 공백이면 `alert()` 1줄. (cluez도 같은 코드 — cluez 절 참조)
- [P2] [F4] aesther_vod_bot.py:1404-1407 — 전체 run(2x)이 600s 대기 후에도 락을 못 잡으면 `log.info` + exit 0, alert 없음. hourly가 걸려 락을 쥐면 VOD run이 무음 스킵되고, 순차 2x 티어의 cluez/gacha/qwer까지 10분 밀린다 — `if lock is None: log.info("LOCK_BUSY ..."); return`. — quick-sync가 아닐 때의 LOCK_BUSY는 `alert()`.
- [P2] [G1] aesther_vod_bot.py:1102·1132·1156, 1260·1289 + client.py:450-454 — 메인 description이 비었거나 2자 미만이고 사본에 본문이 있으면 매 run(hourly 포함) PATCH가 나가지만 client가 `description=None`을 생략해 아무것도 안 바뀐다(영구 무의미 PATCH). 현재 로그 55/55 unchanged라 미발생 — `unchanged = meta_same and (ex_desc or "") == (indiv_desc or "")`. — 메인 본문 2자 미만이면 description 비교 제외.
- [P2] [R-B] aesther_vod_bot.py:1011-1013 — 윈도우 밖(-14일 이전)이던 메인을 사람이 윈도우 안으로 옮기면 옛 날짜의 사본이 조회되지 않아 MISSING 기록 → 이후 PATCH·재생성 모두 영구 정지(메인이 윈도우에 있는 동안 forget 없음) — `guard_indiv.sweep({mid: iid ... if mid in current_main_ids}, set(indiv_fetched))`. — 기록 전 `events/get` 404 확인(stellive 방식). 드묾.
- [P2] [B4] aesther_vod_bot.py:891, 1110-1119 + client.py:545-562 — MAIN_STRIP·VOD append가 `update_or_create_event`로 메인에 쓰는데 404면 메인에 새 이벤트를 만들고 반환 id를 버린다(`ok, _ =`), `db_set_vod`는 옛 eid로 기록 → 메인 토픽에 매핑 없는 중복(조회~쓰기 사이 삭제 경합 때만). — 메인 쓰기는 404 시 생성하지 않는 `update_event`로.
- [P3] [R-B] 1161-1178, 1294-1311 — 사본 PATCH 404 → 자동 CREATE가 MissingGuard를 안 거침(경합 때만).
- [P3] [G1] 888-889 vs 909 — VOD append가 메인 end=start를 강제하지만 메모리 `best_ev["end"]`는 그대로 → 다음 run 사본 PATCH +1회/VOD.
- [P3] [F4] 1389-1394 → 1404 — RotatingFileHandler를 락보다 먼저 붙여, 겹친 두 프로세스가 5MB 회전 시 rename 실패(Windows) 가능.
- [P3] 529 — `ChzzkClient.fetch_vods`의 지역 변수 `vod`가 모듈 `vod`를 가림(현재 무해). [C1] 175 dry-run에서도 `drop_tombstones` 실행(이미 DROP 상태라 무영향).
- [P3] [B4] client.py:412-415 — 2xx인데 body에 id가 없으면 None → 생성됐는데 매핑 미저장 → 다음 run 중복 CREATE. API가 항상 id를 주는지 미확인.
- OK: R-A(전부 `fetch_events_chunked` 415, DAY_WIPE `main_complete and indiv_complete` 1059/`…group_complete` 1218, orphan `allow_orphan_create and main_complete` 705, 매핑-미조회 CREATE 보류 1179/1312, 불완전 alert 687-690, R-E 651-655) · R-B(scope `indiv:{name}`/`group:{name}` 1009-1010, sweep·forget complete 가드 1011/1016/1021, Tombstone 잔재 없음) · R-D(update 실패 시 매핑 유지 1171/1304, `delete_event` 실제 bool 491) · R-F(700-718) · R-C(1127/1265/949) · C(쓰기 래퍼 423/452/486, db 쓰기 `_DRY_RUN` 가드, MissingGuard dry_run 전달) · **F4 락 OK**(busy_timeout 10000 639, msvcrt OS 락이라 원자적·프로세스 종료 시 자동 해제 1339-1350, run.bat·sync_hourly.bat·run_one 모두 `main()` 경유; 로그 12:05:16 실제 LOCK_BUSY) · §3.2 역이식 3건·이중 prefix 수정 · G1 실측 연속 4 run 55/55 unchanged.

### cluezvodbot (cluez_vod_bot.py, HEAD f8da316)
- [P1] [B1] cluez_vod_bot.py:509-511 — aesther와 같은 코드: 치지직 VOD 조회 예외를 삼키고 `break`, alert 없음 → VOD 첨부가 무음 정지 — `except Exception as e: log.warning(...); break`. — page 0 실패 시 alert.
- [P2] [R-B/B4] cluez_vod_bot.py:846, 1083, 1134, 1267 + client.py:547 — 래퍼 `update_event`가 cache 없이 `update_or_create_event`를 불러 404면 즉시 CREATE(`is_missing` 미경유). 메인(846·1083)에서는 list~PATCH 사이 사람이 지운 이벤트를 되살리고, 846은 새 id를 버린 채(`ok, _ =`) 860에서 옛 eid로 vod_map 기록, 1083은 반환값 무시 → 메인 토픽에 매핑 없는 중복(경합 때만). — 메인 PATCH는 404를 실패로.
- [P2] [R-B] cluez_vod_bot.py:985 (+1113/1152) — aesther와 같은 윈도우 경계 오판: 윈도우 밖 메인을 안으로 옮기면 옛 날짜 사본이 MISSING → INDIV_SKIP_MISSING 영구. 드묾.
- [P2] [G1] cluez_vod_bot.py:1095 vs 1232 — MAIN_STRIP이 지역 변수 `summary`만 바꾸고 `ev["summary"]`는 그대로 → 그룹 미러가 `[멤버] X`로 PATCH됐다가 다음 run에 다시 PATCH(쓰기 2회). 1083 실패도 무시돼 그룹은 prefix가 남을 수 있다.
- [P2] [F4] run.bat / quick_sync.bat — 락 없음. 2x 티어 run과 수동 quick_sync가 겹치면 같은 미매핑 메인을 둘 다 CREATE, `INSERT OR REPLACE`로 하나만 매핑 → 나머지는 다음 full run의 DAY_WIPE가 지움(`--no-wipe`면 잔존). 티어끼리는 안 겹침.
- [P3] 1005-1008, 1162, 1292 — `_desc_failed` 메인에 대해 로그는 "MIRROR_SKIP"인데 실제로 건너뛰지 않음: CREATE는 본문 없이(다음 run PATCH로 복구), 매핑분은 description 뺀 PATCH 반복(본문 소실은 없음).
- [P3] [C1] 141, vod.py:234 — dry-run에서도 DDL 커밋. dry-run에서도 `alert`가 Discord로 나감(run.py:76 — 5봇 공통).
- [P3] 495 — `fetch_vods` 지역 변수 `vod`가 모듈을 가림(무해). 823 `f"/video/{vno}" in existing_desc` 부분 문자열 비교(이론상 접두 videoNo 충돌).
- OK: R-A(DAY_WIPE 1032/1191, orphan 656/895, 매핑-미조회 CREATE 보류 1152/1285, sweep 984/989, fetch 전부 `fetch_events_chunked` 381) · R-B(mapping `current_main_ids` 한정 985/990, scope `indiv:/group:{멤버}` 982-983, forget `main_complete` 994, Tombstone 잔재 없음) · R-D(update 실패 매핑 유지, `delete_event` 실제 bool 457, 삭제 실패 시 매핑 유지 1051-1052) · R-E(599-603) · R-F(651-670) · R-G(5MB×3 1336) · C(쓰기 래퍼 389/418/452/1082, db·vod_map `_DRY_RUN` 가드, dal.wiki 직접 requests 없음).

### streamersyncbot (bot.py, HEAD 1c1532c — `adopt_scope="category"` + 공유 cache)
- [P2] [A5] src/botkit/sync.py:453-468 + bot.py:133-143 — `protect_human_edits`는 PATCH(`_update`)만 막고 `_delete`는 검사하지 않는다(docstring 329도 PATCH 전용). 사람이 편집한 미러, 그리고 (summary,start,카테고리)가 같아 `adopt_existing`·`_lookup`·404 복구로 state에 들어간 **사람이 만든 타겟 이벤트**도 소스에서 사라지면(삭제·카테고리 이동·윈도우 밖 이동) absent=delete로 지워진다 — `ok = dw.delete_event(row.event_id)`. 규칙 1(소스 소멸=삭제)과 §4 의도엔 맞지만 이름이 주는 기대보다 좁다. — 사람 편집 이벤트는 삭제 대신 state만 forget하는 옵션 검토(패키지).
- [P2] [F4] run.bat / quick_sync.bat — 락 없음. hourly run과 수동 quick_sync가 겹치면 같은 NEW key를 둘 다 CREATE → state엔 마지막 것만 → 다른 하나는 state 밖이라 absent로도 안 지워지는 **영구 중복**(수동 겹침 때만). — 파일 락.
- [P2] bot.py:216-254 — 소스가 HONEYZ↔ACAXIA·멤버 간 이동하면 옛 엔진 DELETE + 새 엔진 CREATE(새 id, 타겟 사람 편집 소실). 중복은 안 남음.
- [P2] [G1] bot.py:95-97 + client.py:445-455 — 소스 본문/link/location을 비우면 `or None` → `update_event`가 None 필드를 안 보내 hash만 바뀌고 타겟엔 옛 본문이 영구 잔존(PATCH 1회, 무한 아님).
- [P2] [G2] sync.py:425-428 — PROTECTED는 `_save`를 안 해 매 run CHANGED로 남아 시간마다 histories 호출 반복(쓰기 0).
- [P3] bot.py:224-266 엔진 루프에 try 없음(예외 하나가 나머지 엔진 정지). sync.py:559 한 멤버의 윈도우 안 소스가 0건이면 그 엔진 absent 영구 스킵(마지막 남은 이벤트 삭제 미전파). bot.py:201 dry-run 임시 디렉터리 미삭제, bot.py:183 alert 문구 "생성 보류"인데 실제로 CREATE·입양 진행(타겟 완전 조회라 안전).
- 미확인: events/list from/to가 종일 이벤트를 KST 날짜로 거르면 하한 경계에서 state 행이 조회에 안 잡혀 DELETE→다음 시간 CREATE churn 가능.
- OK: 엔진(토픽×카테고리) 10개 각 run당 `sync()` 1회(216-254) · source_complete에 fetch·hydrate·adopt 본문 실패 반영, 본문 실패 이벤트는 sync에서 제외(173-180, 248) · source_window = fetch 창(173, 252) · 타겟 캐시 strict, `ListFailed`면 alert+return 1(189-195) · MissingGuard sweep 캐시 윈도우 한정·forget은 `src_complete`일 때만(232-241) · dry-run은 `DRY_RUN` env를 main 전에 세팅, state·입양·guard 모두 임시 사본(200-206) · G1: hash가 소스 Event만으로 계산돼 sanitizer 차이로 무한 PATCH 없음.

### stellivebot (stellive_bot.py, group_mirror.py, reconcile.py — HEAD a05a89c+8de5f62)
- [P0] [R-B/R-A] stellive_bot.py:469 ↔ 1310, 1846-1847, 1158-1167, 1762-1764, 1905 — **자체 고아삭제가 MissingGuard를 발동시켜 메인 미러가 영구 누락**. 체인: (1) stellight 창 필터는 원본 `startDateTime`(비고정이면 00:00) 기준 `if window_from <= dt <= window_to`(469)인데 개별 조회는 `window_to_ms`까지라, D+28에서 멤버 기본 시각(s_dt)이 창 끝을 넘는 항목은 매핑된 이벤트가 안 보여 중복 CREATE(실로그: 아카네 리제 09-03 11:00·19:30 `CREATE 10-01 20:00` 2회 → 09-04 `ORPHAN_DELETE 2026-10-01`, stellive_bot.log.1:36547). (2) 중복 run에서 새 ind2가 기존 메인 M1에 시간 매칭으로 백필(1847). (3) 다음 run ind1 고아 삭제가 메인 미러 M1까지 지우는데 행 정리는 `DELETE FROM main_sync_map WHERE stellar_id=? AND dalwiki_event_id=?`(1164)뿐이라 `ind2→M1` 행이 남음. (4) 같은 run sync_member_to_main의 sweep이 M1 부재를 보고 ind2를 MISSING 기록 → `SKIP_MISSING MAIN_CREATE`로 그 일정이 창을 벗어날 때까지 메인·그룹 미러 영구 누락. 옛 코드는 재생성했으므로 W4 이후 생긴 유실 경로. — 1164에 `main_event_id=?` 행 삭제 추가(1280과 동일) + 개별 조회 to_ms를 window_to+1일로(또는 stellight를 s_dt로 필터).
- [P1] [R-A] stellive_bot.py:1934 — 메인 고아정리가 `main_ok`만 본다 — `if main_ok: mo = reconcile_main_orphans(m, member_main, used_main_ids, ...)`. indiv_ok=False면 빠진 청크 항목이 1577에서 `continue`돼 used_main_ids에서 빠지고, 청크 경계가 run 시각(11:00/19:30)이라 같은 날짜가 갈려 정상 메인 미러가 MAIN_ORPHAN_DELETE(다음 run 재생성되지만 id·수동 편집·그룹 미러 id 유실). — indiv_ok를 넘겨 `if main_ok and indiv_ok`.
- [P1] [§7] stellive_bot.py:1975, 1994-2008, 2032-2059 — 수동 `--dedup-main`이 공유 메인 토픽(v1c2BaT1zEvn) 전 카테고리를 대상으로 삼아 다른 봇(chzzk·streamersync 소스 등) 이벤트·매핑 id를 지울 수 있다(§7 '메인 삭제 범위 확장 금지' 위반) — `evs = list_events(MAIN_TOPIC_ID, from_=f, to=t, take=1000)` / `key = (e.get("categoryId") or "", start_raw, ...)`. — `categoryId in {m.main_category_id for m in MEMBERS}`로 한정. (take=1000이 서버 상한 500에 걸려 조용히 잘린다는 지적은 미확인 — client는 1000까지 허용, 잘림 감지는 `len>=take`라 상한이 더 낮으면 분할 안 됨.)
- [P2] [R-D] stellive_bot.py:1443 → 1516/1564 → 1804, 1916 — `_desc_failed` 개별 이벤트로 MAIN_CREATE하면 메인이 빈 본문(VOD·카페 공지 없음)으로 생성되고 이후 새 VOD만 append돼 기존 본문은 영구 미전파 — `current_desc_html = existing.get("description") or ""`. — desc_failed면 MAIN_CREATE 보류.
- [P2] [R-D] stellive_bot.py:1395-1397 — `used_video_nos`가 `_desc_failed` 이벤트의 VOD를 몰라 같은 VOD가 근접 이벤트에 붙을 수 있다(미확인) — `for no in extract_vod_video_nos(e.get("description") or "")`.
- [P2] [R-A] stellive_bot.py:1415-1436 — indiv_ok=False일 때 매핑 이벤트가 실패 청크에 있으면 `find_matching_event`(±90분)가 경계 너머 다른 이벤트를 잡아 `db_upsert`+PATCH(제목·시각 덮어쓰기). — `not indiv_ok and mapped_id`면 2차 매칭 생략.
- [P2] [R-F] group_mirror.py:186-187, 214-217, 232-233 + stellive_bot.py:2189 — `sync_group`의 `fail` 통계(원본 get·update·create 실패)를 무시, alert 없음. — `fail>0`이면 alert.
- [P2] [F4] stellive_bot.py:345, reconcile.py:121/176-196 — 락·busy_timeout 없음(`sqlite3.connect(DB_PATH)` 기본 5s). reconcile은 첫 UPDATE부터 마지막 멤버까지 HTTP를 하며 쓰기 트랜잭션을 쥔다(commit 196 한 번). 둘 다 StartWhenAvailable 따라잡기나 수동 backfill/dry_run과 겹치면 생성 직후 `db_upsert`에서 `database is locked` → 매핑 유실(대개 다음 run 시간 매칭으로 자가치유). 실제 겹침 미확인. — aesther 락 이식.
- [P2] [R-B] group_mirror.py:204-229 — 그룹 쪽엔 MissingGuard가 없어 사람이 지운 그룹 미러가 원본 수정 시 UPDATE 404 → CREATE로 부활. '메인이 진실원본' 의도일 수 있음(미확인).
- [P3] [R-E] 2181 — dal.wiki 전면 장애면 `fetch_events_chunked`가 `([], False)`라 `member_failed`=0, 멤버별 '조회 불완전' alert N건(쓰기 0, 안전). 1건으로 합치기.
- [P3] [R-F] 1317/630 — 치지직 API 실패가 그 멤버의 stellight 일정 동기화까지 막음. VOD 조회만 try.
- [P3] [B4] client `update_or_create_event` 404 폴백 — 조회~PATCH 경합 때 MissingGuard·R-A 우회 재생성(5봇 공통).
- [P3] [C1] dry_run.bat — `--log-file stellive_bot.log`로 dry-run의 CREATE/MAIN_UPDATE 줄이 실 run과 같은 형식으로 실 로그에 섞임(대시보드 today_writes 오염 가능, 미확인).
- OK: R-A 개별(CREATE `if not indiv_ok` 보류 1572, 개별 고아정리 `if indiv_ok` 1686, sweep 1382)·메인(MAIN_CREATE `not main_ok` 보류 1910, sweep 1761)·그룹(`_list` 불완전 시 예외로 전체 보류 62-69)·reconcile(불완전·`dexists` 미상·urllib 예외 시 commit 전 중단 73-74/49/55) · R-B(Tombstone 잔재 없음 393, scope `indiv:{id}`/`main:{id}` 1380/1759, 창 밖 id는 404일 때만 missing 328-338, CREATE 직전 is_missing 1567/1905) · R-D(PATCH 실패분 used 유지 1438/1851, `_desc_failed`면 description 제외·VOD append 금지 1531/1866/1451/1863, 고아정리도 본문 실패 시 보류 1098/1109/1234) · R-F(2168-2178, 전원 실패 2181) · C(`--dry-run` 분기, 쓰기 전부 `if not dry_run`, DB `_DRY_RUN` 가드 406/430, group save no-op, reconcile 기본 dry-run) · G1(메인 제목을 개별 절단본 기준으로 1779-1782, 2회차 개별181·메인181·그룹191 unchanged) · B4/A7.
## 공통 운영

### bot_scheduler (run_tier_*.bat, crash_check.py, health_check.py, hidden_launch.vbs)
- [P2] [B6] run_tier_2x.bat:53-57 (daily:71-75, weekly:41-45, hourly:24-27 동일) — crash_check가 자기 오류로 실패해도 '크래시'로 읽혀 **모든 봇이 2번 돈다** — `python ...crash_check.py "%_out%" >nul 2>&1` / `if errorlevel 1 (` ; crash_check.py:12 `from health_check import _real_crash`가 try 밖. health_check.py를 고치다 문법 오류가 나거나 python이 PATH에 없으면(9009) 종료코드 ≥1 → 티어 전체 재실행(LLM 비용·비멱등 봇 이중 쓰기). — crash_check는 import 실패 시 0 반환, bat는 정확히 1일 때만 재시도(`if errorlevel 1 if not errorlevel 2`).
- [P2] [B6/F3] run_tier_*.bat:56 — 재시도가 첫 run 출력을 덮어써 첫 run의 Traceback·`[HEALTH-ALERT]`가 사라진다 — `cmd /c "%DOCS%\%~1\%~2" <nul > "%_out%" 2>&1` (재시도도 같은 `>`). 재시도가 성공하면 health_check는 크래시를 영영 모른다. — 재시도는 `>>`로 이어 쓰거나 첫 출력을 `lastrun_…crash1.out`로 보존.
- [P2] [F3] run_tier_*.bat:58 + health_check.py:186-205 — lastrun 파일은 run마다 덮어쓰고 health_check는 21:00에 한 번만 본다 → 2x 티어 11:00 run, hourly 24회 중 23회의 Traceback은 감지 불가. `[HEALTH-ALERT]`는 `botkit.run.alert`가 Discord로 직접 보내지만(`ingest_core.discord`, `DALWIKI_DISCORD_WEBHOOK_URL` User env 설정 확인) 크래시(Traceback)는 그 경로가 없다. — 크래시일 때만 `crash_<라벨>_<ts>.out`을 따로 남기거나 crash_check가 Discord 전송.
- [P2] [F4] register_tasks.ps1:108-125 — 11:00에 2x 티어(aesthervodbot run.bat ≈11:01)와 HOURLY 티어(aesthervodbot sync_hourly.bat 11:00)가 같은 DB로 겹친다. 스케줄 자체는 정상이고 봇의 락에 의존 — aesthervodbot 절 참조.
- [P3] run_tier_ewc.bat:25-33 — 옛 `findstr Traceback` 재시도(crash_check 미사용, lastrun 미기록, PYTHONIOENCODING 없음). 작업 `dalwiki bots - EWC seasonal`·`WorldCup off reminder`가 여전히 등록돼 있음(다음 실행 없음). — 은퇴 처리 시 unregister + legacy/.
- [P3] hidden_launch.vbs:5 — 작업 결과 코드가 항상 wscript의 0이라 `LastTaskResult`로는 실패를 못 본다(health_check가 대신). 문서화만.
- [P3] bot_scheduler 루트의 `*.bat.bak_*`·`.broken_bak` 5개 — legacy/로.

### health_check.py — MONITOR vs 티어 라벨
티어 4개의 `call :RUN` 62줄을 따라가 각 bat의 `track.py <라벨>`을 뽑아 MONITOR와 대조: **티어에 있는데 MONITOR에 없는 라벨 = koreafootballbot, overseas_football_bot 2개(의도적 제외 주석)**, MONITOR에 있는데 티어에 없는 라벨 0, track 줄 없는 티어 bat 0.
- [P2] [F3] health_check.py:119-121 — `koreafootballbot`·`overseas_football_bot`(koreansoccerguysbot/run_full.bat)은 DAILY 티어에서 매일 dal.wiki에 쓰는데 '시즌' 주석으로 미감시 — `#   koreafootballbot, overseas_football_bot (seasonal A-match)`. 티어에서 도는 동안은 감시해야 한다. — 티어에서 REM할 때 같이 빼는 규칙으로 MONITOR에 추가.
- [P2] [F3] aesthervodbot/run.bat·sync_hourly.bat 둘 다 `track.py aesthervodbot` — 2x 전체 run(VOD 추가+reconcile)이 멈춰도 hourly 미러가 매시간 같은 라벨을 갱신해 STOPPED가 뜨지 않는다(대시보드도 hourly run을 전체 run으로 셈). — sync_hourly 라벨을 `aesthervodbot-hourly`로 분리, MONITOR·BOT_LOG_GLOBS에 추가.
- [P2] [F3] health_check.py:246-247 — 기록 0건 라벨(untracked)은 `print`만 하고 Discord로 안 보낸다 → 새 bat의 라벨 오타·track 줄 누락이 영구 무감시. 지금은 0건이지만 실패 경로. — untracked도 경보에 포함.
- [P2] [F3] run_healthcheck.bat:4 + health_check.py:211 — health_check 자체가 죽으면(예: bot_runs.jsonl 잠김/없음 → `open(RUNS)` 예외) Traceback이 `health_check.log`에만 쌓이고 아무도 안 본다. dalwiki-agent health도 track 라벨(`dalwiki-agent-health`)은 남기지만 MONITOR에 없어 서로 교차 감시가 없다. — `dalwiki-agent-health`를 MONITOR에 넣고, agent health 쪽에서 health_check.log 최신 줄을 확인.
- [P3] [F2] `C:\Users\gamer\bot_runs.jsonl` 561KB 무한 증가, health_check·dashboard가 매번 전부 파싱.

### bot_dashboard (track.py, dashboard.py, dalkey.py)
- track.py OK: `O_CREAT|O_EXCL` 락 + 30s stale 회수 + fsync. [P3] track.py:47-52 — 10s 안에 락을 못 잡으면 락 없이 append(드묾, 손실보다 낫다는 선택으로 보임).
- dashboard.py BOT_LOG_GLOBS: 라벨 62개 경로 전부 존재 확인(VOD: `chzzk_vod_bot.log`·`stellive_bot.log`·`aesther_vod_bot.log`·`cluez_vod_bot.log`·`streamersyncbot/logs/streamersyncbot.log`). lastrun 파일명 규약 `lastrun_<폴더>__<bat stem>.out`도 `_lastrun()`과 일치.
  - [P3] dashboard.py:91-94 — `# HOURLY` 주석 아래 chzzkvodbot·stellivebot(실제 2x 티어). 
  - [P3] [F2] gamepassbot `logs/gamepass_*.log`·gamingschedulebot `logs/freegame_*.log` 일자 파일 무한 누적(4월부터 180+개) — `_glob`이 매번 전부 파싱.
- dalkey.py OK: 하드코딩 없음, env → stellivebot/.env → gachabot/.env.

### dalwiki-agent (run_health.py, agent/missions/health.py, topics.yaml)
- topics.yaml vs 실제 봇 토픽: 티어 봇 코드(legacy/state/data 제외)의 `topic` 줄에 있는 12자 ID 68개 중 레지스트리 미등록 18개는 전부 카테고리 ID 오탐(streamersync `SRC_CAT_MAP` 17, naehanbot Default 1) → **미등록 토픽 0, managed_by 불일치 0**(dcbot·mnetplusbot은 리센느 `also_bots`). VOD 레지스트리 39토픽(chzzk 17·stellive 13·aesther 5·cluez 3·streamersync 2) 전부 `kind: vod` → `snapshot_topic`이 seasonal 취급(no_future/drop 제외, stale만).
- [P2] [B1] agent/missions/health.py:62 + src/dalwiki_client/histories.py:67-68 — `list_histories`가 실패하면 `[]` → `create_history` last_at=None → **stale 판정이 조용히 빠진다**(errors에도 안 들어감). VOD·seasonal 토픽은 no_future/drop이 꺼져 있어 stale이 유일한 경보라 histories 장애 시 VOD 39토픽이 완전 무감시. — histories 실패(None/비200)를 예외로 올려 errors에 넣기(`list_histories`가 실패와 '이력 없음'을 구분하도록).
- [P2] [F3] topics.yaml:117-121·271-275·651-655 + config.yaml `realert_days: 7` — 은퇴 봇(asiangamesbot·ewcbot·worldcupbot) 토픽과 시즌오프 KBO 팀 토픽이 7일마다 같은 stale/no_future 경보를 재발송(agent.db: 9/20 이후 EWC `KCVJGVETQk0w` stale 2회, KBO 6팀 stale 각 2회 등 20건). 경보 피로로 진짜 경보가 묻힌다. — 은퇴 봇 토픽은 `managed_by: retired`(bot_topics 제외), 시즌오프는 `seasonal_until:` 같은 끄기.
- (알려진 이슈) 미하루 `aMKFbKplzI5Y` stale 주간 반복 = dalbots-24e. 신규 등록 안 함.
- [P3] run_health_report.bat — track 라벨 없음(주간 리포트 실행 여부를 대시보드에서 못 봄; run_health.py:23 한 줄 로그로 대체).
- 경보 임계(config.yaml:17-24): stale = max(7일, 생성주기 중앙값×3), drop = 7일 전 대비 50% 미만(직전 ≥4건). 조회 실패는 `topic_events`가 `ListFailed`를 올려 errors로 Discord에 나간다(OK). `eventCount>0`인데 0건이면 예외(OK).

## botkit.vod (공통 모듈, 참고)
- [P2] [G1] src/botkit/vod.py:90-94 — `safe_summary`가 strip 후 `t[:100]`로 잘라 100번째 글자가 공백이면 끝 공백이 남고, client `_guard_summary`(client.py:244)가 다시 strip해 99자로 저장 → 다음 run `ex_summary != new_summary`로 **영구 PATCH**(aesther `"갠방 - "+원제` 100자 초과 시 등 5봇 공통) — `return t[:SUMMARY_MAX]`. — 자른 뒤 `.rstrip()`(테스트 추가).
- [P3] [C1] src/botkit/vod.py:198-200 — `MissingGuard.__init__`가 dry_run이어도 `executescript(_SCHEMA)`+`commit`. `executescript`는 먼저 COMMIT을 발행하므로 호출자 conn의 미커밋 트랜잭션도 확정된다 — 테이블 생성뿐이라 데이터 누수는 없음.
- [P3] [A2] src/dalwiki_client/client.py:337-342 — `list_events` truncation 분할이 `_max_depth`(7)에 닿으면 잘린 결과를 조용히 반환 → `fetch_events_chunked`가 complete=True로 본다. 3일 청크×take 500이라 현실적 위험은 낮음.

## 공통 패턴

1. **'PATCH는 허용'의 틈 — 불완전 조회에서 새 매핑이 생긴다.** R-A는 불완전 run에서 기존 매핑 PATCH만 허용하는데, 시간 근사 매칭(chzzk 782-793, stellive 1415-1436)은 매핑을 *새로 만드는* PATCH라 같은 문을 통과한다. 고아정리·DAY_WIPE의 가드도 한쪽 플래그만 보는 곳이 있다(stellive 1934 `main_ok`만). 규칙을 "삭제·생성·**새 매핑** 보류, 완전 조회 플래그는 관련된 양쪽 모두"로 읽어야 한다.
2. **MissingGuard가 '사람 삭제'가 아닌 것을 사람 삭제로 기록하고, 기록은 조용하다.** 봇 자신의 고아삭제(stellive P0), 윈도우 경계 밖 매핑 상대(chzzk·aesther·cluez)가 MISSING이 되면 재생성이 영구히 막히는데 흔적은 info 로그 한 줄뿐. MissingGuard에 '새 키 기록 시 alert' 또는 '기록 전 `events/get` 404 확인'(stellive 328-338 방식)을 공통화할 것.
3. **404 → create 폴백(`update_or_create_event`)이 모든 안전장치를 우회한다.** 5봇 모두 메인/사본 PATCH 404에서 MissingGuard·R-A를 거치지 않고 생성하며, aesther·cluez는 새 id를 버려 매핑 없는 중복을 남긴다. 경합 때만이지만 공통 원인 하나 — 메인 토픽 쓰기는 404를 실패로 다루는 `update_event`를 쓸 것.
4. **매핑 정리 비대칭.** 이벤트를 지울 때 매핑 테이블의 한쪽 키로만 행을 지워 반대쪽 매핑이 유령으로 남는다(stellive 1164 vs 1280). 유령 매핑 + MissingGuard = 영구 누락.
5. **warning 로그는 아무도 안 본다.** 치지직 API 실패(aesther·cluez), LOCK_BUSY(aesther), 그룹 미러 fail(stellive), histories 실패(agent health), untracked 라벨(health_check), 재시도에 덮인 첫 크래시(티어 bat) — 전부 '쓰기 0이라 안전'하지만 기능이 무음으로 멈추는 경로다. 경보 채널은 `[HEALTH-ALERT]`(Discord 직송) 하나로 모을 것.
6. **락은 aesther만.** 티어는 순차라 서로 안 겹치지만 수동 bat(run.bat·quick_sync·sync_main·backfill)와 StartWhenAvailable 따라잡기가 겹치면 chzzk·cluez·stellive·streamersync는 중복 CREATE(streamersync는 state 밖 영구 중복). aesther의 `acquire_run_lock`(msvcrt OS 락)+`busy_timeout`이 검증된 패턴이니 그대로 이식.
7. **감시 체계의 자기 사각지대.** 라벨 공유(aesther 2x·hourly), 시즌 주석으로 빠진 활성 봇, 은퇴 토픽의 주간 반복 경보, health 도구끼리 교차 감시 없음 — 봇 결함보다 '결함이 보이는가'가 약하다.

## beads
P0~P2를 에픽 `dalbots-bmi` 자식 `[W7]` 이슈로 등록(라벨 `audit,w7`). 같은 원인은 대상 여러 개를 한 이슈로 묶었다(예: 치지직 무음 = aesther·cluez 1건, 수동 bat 락 = chzzk·cluez·streamersync 1건). P3는 이 보고서에만.

| 우선순위 | 이슈 |
|---|---|
| P0 | bmi.77 chzzk MAIN_REUSE 실패→공지 고아 삭제 · bmi.98 stellive 자기 고아삭제→MissingGuard→메인 영구 누락 |
| P1 | bmi.78 chzzk 시각 미반영 · bmi.79 chzzk 교차 VOD · bmi.80 aesther·cluez 치지직 무음 · bmi.99 stellive 메인 고아정리 indiv_ok · bmi.100 stellive dedup-main 전 카테고리 |
| P2 | bmi.81~97(chzzk 근사매칭·락 4봇·MissingGuard 경계·update_or_create·chzzk 비용·aesther LOCK_BUSY·aesther 빈본문 PATCH·cluez MAIN_STRIP·vod safe_summary·streamersync 삭제보호·미러 엣지·crash_check·lastrun 덮어쓰기·MONITOR 누락·health 자기감시·agent histories·은퇴 토픽 경보) · bmi.101~103(stellive desc_failed·2차 매칭·group fail) |
