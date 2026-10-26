# W9 — VOD 4봇: 분할 VOD(17h+) 오매칭 수정, liveOpenDate 기반 (beads dalbots-bmi.233)

작성 2026-10-26. 대상 `chzzkvodbot`(기준 구현) → `aesthervodbot` → `cluezvodbot` → `stellivebot`. 규칙·금지는 `W2_REBUILD_BRIEF.md`, VOD 규칙 `W4_PLAN_vod.md` §2. **데이터 복구는 하지 않는다**(기존 오매칭은 사용자가 수동으로 고쳐 둠 — 이미 붙은 VOD·이미 있는 이벤트는 건드리지 않는다).

## 1. 진단(실측)
- 네 봇 모두 VOD 시작을 `publishDateAt − duration`으로 추정한다.
- 치지직은 17h 초과 방송을 자동 분할한다: 제목 `YYYY년 MM월 DD일 N부 …`, 1부 duration = 정확히 61200초. 분할편의 `publishDateAt`은 **그 부의 방송 종료가 아니라 인코딩 완료·공개 시각**이라 실측 3.7h~9h 늦다(윤가놈 09-21: 1부 공개 19:23, 2부 공개 익일 20:27·17h → est_start가 8h 뒤로 밀림). 짧은 2부가 1부보다 먼저 공개되는 경우도 같은 경로로 틀어진다.
- 그 결과 series gap(60m)·tolerance(±6h)가 깨져 2부 이상이 다음 날 이벤트에 붙거나(윤가놈 WDY6/eBwJ3 교차, 독케익 10-04 드래플린) CREATE_NEW로 '2부' 고아 이벤트가 생긴다.
- `GET https://api.chzzk.naver.com/service/v2/videos/{videoNo}` 응답 `content.liveOpenDate`("YYYY-MM-DD HH:MM:SS", KST)가 **같은 라이브의 모든 분할편에서 동일**(15307272·15319391 = "2026-09-21 02:06:52", 15802003·15802356 = "2026-10-21 20:59:41"). 목록 API(v1 channels/{id}/videos)에는 없고 v2/v3 목록은 404. `prevVideo/nextVideo`의 liveOpenDate는 null이라 쓸 수 없다.

## 2. 공통 설계 (`botkit.vod`에 추가, 4봇이 공유)
```
vod.fetch_live_open(video_no) -> Optional[int]        # v2/videos 상세 → liveOpenDate KST → epoch ms. 실패/None → None(재시도는 다음 run)
vod.VodMeta(conn)                                    # 테이블 vod_meta(video_no PK, live_open_ms, title, duration, fetched_at). 캐시: 있으면 상세 호출 없음
vod.resolve_starts(vods, meta, fetch=fetch_live_open) -> int   # 각 vod dict에 v["live_open_ms"](있으면)와 v["start_ms"] 세팅:
                                                     #   start_ms = live_open_ms if live_open_ms else est_start_ms(기존 추정)
                                                     #   v["_start_source"] = "live_open" | "estimated". 신규 videoNo만 상세 호출(캐시 미스), 실패 건수 반환
vod.group_by_live(vods) -> int                        # live_open_ms 같은 VOD = 한 series. 정렬은 (live_open_ms, publishDateAt).
                                                     #   series head = publishDateAt 가장 이른 것이 아니라 **제목의 'N부' 최솟값 → 없으면 publishDateAt 최솟값**.
                                                     #   각 분할편에 _series_part/_series_head/_orig_est_start_ms 세팅(기존 메타와 호환).
                                                     #   live_open_ms 없는 VOD끼리만 기존 gap 휴리스틱(apply_series_grouping)을 폴백으로 적용.
```
테스트(tests/test_vod_live.py): liveOpenDate 파싱(KST→UTC ms), 캐시 히트 시 호출 0, 2부가 먼저 와도(1부 미공개) 같은 live_open_ms로 head 지정, 분할편 publish가 9h 늦어도 같은 series, 상세 실패 시 estimated 폴백 + 반환 실패 수.

## 3. 봇별 적용
### chzzkvodbot (기준, 먼저)
- `fetch_vods` 결과에 `resolve_starts` → `est_start_ms` 대신 `start_ms`를 매칭 키로(기존 변수명은 유지해도 되나 값은 start_ms). `apply_series_grouping` 호출을 `group_by_live`로 교체(폴백 포함).
- 매칭(±tolerance)·배정·CREATE_NEW 로직은 그대로. 단 **CREATE_NEW는 live_open_ms 기준**: `series_created_in_run`을 `live_open_ms` 키로 바꾸고, 새 테이블 `live_events(member_name, live_open_ms PK, event_id)`에 저장해 **다음 run에 오는 후속 부(1부가 늦게 공개된 경우 포함)가 같은 이벤트에 append**되게 한다. 이미 `vod_map`에 있는 videoNo는 지금처럼 건드리지 않는다(사용자 수동 수정 보호).
- CREATE_NEW 제목: 분할편 제목의 `YYYY년 MM월 DD일 N부 ` 접두는 떼고 본제만(`"2026년 10월 21일 2부 글클메 …"` → `"글클메 …"`). 메인 미러 제목도 동일.
- `_start_source="estimated"`(상세 실패)인 VOD는 이번 run **매칭·CREATE_NEW 보류**(추정값으로 붙이면 오매칭 재발) — 로그 1줄, 다음 run 재시도. 3 run 연속 실패면 alert.
- 로그: `series 분할편 묶음` 줄에 `live_open=N estimated=M` 추가.
### aesthervodbot · cluezvodbot
- `ChzzkClient.fetch_vods`의 `estimatedStartMs`를 `resolve_starts`의 `start_ms`로. `_process_vod`의 `_effective_kst_day`·근접 매칭은 start_ms 기준. series 병합(같은 방송 2부 past_events 편입)은 `group_by_live`로. 나머지 동일.
### stellivebot
- `fetch_chzzk_vods`(684~)에서 `pub − dur` 대신 `resolve_starts`. `find_vod_for_event`(804~)·VOD append 규칙은 그대로. stellive는 stellight 일정이 기준이라 CREATE_NEW가 없다 — 매칭 시각만 바뀐다.

## 4. 검증(봇마다)
1. dry-run → 실제 1회 → 2회째 전건 unchanged(VOD +0). **기존 vod_map에 있는 VOD는 재배치되지 않아야 한다**(사용자 수동 수정 보호) — 로그로 '재배치 0' 확인.
2. 실측 사례 dry-run 대조(쓰기 없이): 명예훈장 10-21(15802003/15802356), 달콤레나 10-17, 독케익 10-04(15528236/15532749)가 같은 live_open_ms로 묶이고 같은 이벤트 후보로 가는지 로그 제시. 윤가놈 09-19/09-21 4부작도 live별로 묶이는지.
3. 상세 API 실패 시뮬레이션(fetch=None 주입) → estimated 폴백·보류·alert 확인.
4. 치지직 상세 호출 수: run당 신규 VOD 수와 같아야 함(캐시 적중 로그).
5. 라이브 실행 금지: 11:00/19:30 ±10분(2x), 매 정각 ±5분(aesther hourly), 월 13:00(stellive reconcile).

## 5. 하지 말 것
- 기존 이벤트의 VOD 링크 제거·이동, vod_map 행 삭제, 메인 토픽 범위 확장.
- per-day reconciliation·분류·카페 공지 보존 로직 변경.
- 패키지 기존 함수 시그니처 변경(추가만).
