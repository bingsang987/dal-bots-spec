# dal.wiki 봇 생태계 명세서

dal.wiki 캘린더에 이벤트를 자동 등록하는 봇들의 구조와 동작 방식을 역공학(reverse-engineering)하여 정리한 문서입니다.

이 명세서를 기반으로 새로운 봇을 처음부터 구현할 수 있도록 작성되었습니다.

## 공통 클라이언트 패키지 `dalwiki_client`

`src/dalwiki_client/`에 dal.wiki API v2 공통 클라이언트가 패키지 형태로 들어있다. 봇별 자체 구현(`dalwiki.py` / `dalwiki_client.py`) 대신 이것을 쓰면 다음 기능을 자동으로 얻는다:

- `/events/list` 1000 cap 자동 분할 (truncation 감지 → 시간 윈도우 반으로 재호출). from/to 둘 중 하나가 없으면 분할이 꺼진다.
- **`list_events` 실패(400/5xx/재시도 소진) → `ListFailed` 예외.** 조용히 `[]`를 돌려주던 구 동작은 "없음"과 "실패"를 구분하지 못해 미러봇 5종의 prune 오삭제·CREATE 폭주의 공통 뿌리였다. 구 동작이 꼭 필요하면 `raise_on_fail=False`(삭제 판단에 쓰는 호출에서는 금지).
- `update_event` 404 시 자동 fallback (`update_or_create_event` 헬퍼), `TopicCache`로 (summary, start) 자연 키 복구. `TopicCache.load()`는 기본 strict — 청크 하나라도 실패하면 `ListFailed`를 그대로 던진다(불완전 캐시로 복구/삭제 판단 금지).
- `categoryIds`, `perDateLimit`, `tz` 신 API 파라미터 지원
- **ISO Z 정규화** `_iso_z`: aware datetime / Unix ms / ISO 문자열(`Z`, `+09:00`, 밀리초 유무 무관) 전부 서버 포맷 `YYYY-MM-DDTHH:MM:SS.000Z`로. 문자열을 그대로 통과시키던 구 동작은 figurebot/onlinegamebot의 `T00:00:00Z`가 TopicCache 키와 어긋나 404 복구를 무력화시켰다.
- 필드 가드 일괄: summary 2자 미만 보정(빈 값 `(제목 없음)`, 1자 `X (일정)`)·100자 클립, description 2자 미만 생략, location 64자 절단
- DRY_RUN: 쓰기(`create/update/delete`)만 가짜 id, **조회 POST(`events/list·get`, `topics/get·histories`, `categories/*`)는 실제 호출** — 드라이런에서 "중복 생성·삭제 없음"을 검증할 수 있다. `DRY_RUN` 환경변수는 매 호출 시점에 읽는다(`--dry-run`을 import 이후에 처리해도 됨).
- `X-Api-Key` 헤더 자동 첨부(Bearer 아님), 429/5xx 지수 백오프 재시도

설치 (각 봇 venv에서):

```bash
pip install -e C:/Users/gamer/Documents/GitHub/dal-bots-spec            # dalwiki_client + botkit(sync/run)
pip install -e "C:/Users/gamer/Documents/GitHub/dal-bots-spec[llm,discord]"   # + botkit.llm / botkit.discord
```

사용 예:

```python
from dalwiki_client import list_events, create_event, update_or_create_event, ListFailed

try:
    events = list_events(topic_id="...", from_=start_dt, to=end_dt, category_ids=["..."])
except ListFailed:
    alert("dal.wiki 조회 실패 — 이번 run 삭제/정리 스킵"); events = None
new_id = create_event(topic_id="...", summary="...", start=dt, end=dt, all_day=True, category_id="...")
final_id = update_or_create_event(maybe_legacy_id, topic_id="...", summary="...", start=dt, end=dt)
```

각 봇 마이그레이션은 점진적으로 진행. `migration/dalwiki_client_v2.py`는 패키지의 origin 템플릿.

## 공통 실행 기반 `botkit` (2026-10-15, 정비 계획 §2)

`src/botkit/` — 봇이 **소스 긁기 + Event 목록 만들기**만 하면 나머지(동기화·상태·로그·경보·LLM·Discord)는 공통으로 처리한다. 신규 봇과 W2/W3 재구축 봇은 이 위에 쓴다. 자체 `dalwiki.py` 래퍼·자체 state 테이블·자체 catchup 루프 금지.

| 모듈 | 쓰는 것 | 핵심 규칙 |
|------|---------|----------|
| `botkit.sync` | `SyncEngine(topic_id, state_path, absent="delete")`, `Event(key, summary, start, end, all_day, category_id, description, link, location)` | TopicCache 로드 실패 → `SyncAborted`(쓰기 0건). NEW→create(같은 summary+start가 있으면 입양), CHANGED→update(404면 캐시 복구→create), UNCHANGED→skip. **쓰기 성공 후에만 state 저장.** absent(소스에서 사라진 key)는 `keep|delete`, 단 소스 0건·`source_complete=False`면 전체 스킵. `source_window=(from,to)`로 absent 범위 한정(과거 보호). dry-run이면 state 미저장. 공유 토픽에서 카테고리마다 같은 (summary,start)가 흔하면 `adopt_scope="category"`(입양·404 복구를 같은 카테고리·미점유 id로 한정), 같은 토픽 엔진 여러 개는 `cache=`로 미리 로드한 TopicCache 공유. |
| `botkit.run` | `run(bot_name, main_fn)`, `alert(msg)` | 로그 `logs/<bot>.log` 5MB×3 회전 + 콘솔. `--dry-run`을 떼어 `DRY_RUN=true`. 예외 → `[HEALTH-ALERT] <bot>: 예외 종료 …` + Traceback + exit 1. `alert()`는 stdout 한 줄(+Discord webhook 있으면 전송). track.py는 그대로 bat에서 호출(래퍼가 대체하지 않음). |
| `botkit.llm` | `llm_json(prompt, images=None, model=HAIKU, max_tokens=…)`, 상수 `HAIKU="claude-haiku-5-5"`, `SONNET="claude-sonnet-5-5"` | 코드펜스 제거 + `raw_decode`(잡설/Extra data 무시). 429/529/5xx 지수 백오프. 401/403/크레딧 소진/재시도 소진 → **`LLMUnavailable`** — 호출자는 커서·state 전진 금지. 파싱 실패 → `LLMParseError`(그 입력만 실패). 이미지 긴 변 7,900px 축소·media_type 감지. |
| `botkit.discord` | `run_catchup_once(token, channel_ids, CursorStore, handler, retry_queue=RetryQueue(...))` | 메시지별 handler **성공 뒤에만** 커서 전진. `LLMUnavailable`이면 그 자리에서 멈춤(커서 유지, alert, 다음 run 재시도). 그 외 예외는 재시도 큐 파일(`max_attempts` 초과 시 폐기+alert)에 넣고 커서 전진. `on_ready`는 `try/finally: client.close()`, catchup 중 `on_message` 미등록. |

최소 봇 골격:

```python
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from botkit import run, alert, SyncEngine, Event

KST = ZoneInfo("Asia/Seoul")
TOPIC, CAT = "topicId…", "categoryId…"

def main(ctx):
    rows, all_ok = fetch_source()                 # (목록, 소스 전부 성공 여부)
    if not rows:
        alert("소스 0건 — 동기화·absent 스킵"); return
    events = [
        Event(key=f"{r.date}_{r.home}_{r.away}", summary=f"{r.home} vs {r.away}",
              start=datetime.combine(r.date, r.time, KST), end=datetime.combine(r.date, r.time, KST) + timedelta(hours=3),
              category_id=CAT, link=r.url)
        for r in rows
    ]
    eng = SyncEngine(TOPIC, "state.db", absent="delete")
    stats = eng.sync(events, source_complete=all_ok, source_window=(window_from, window_to))
    if not stats.ok():
        alert(f"쓰기 실패 {stats.failed}건: {stats.failures[:3]}")

if __name__ == "__main__":
    run("mybot", main)        # python bot.py --dry-run 으로 드라이런
```

기존 봇 state 이관: `SyncState(path, topic_id).import_legacy([(key, event_id), …])` — hash는 비워 두어 첫 run에 1회 PATCH로 서버와 맞춘다. Discord 커서는 `CursorStore.import_legacy({channel_id: last_message_id})`.

테스트: `python -m pytest tests` (가짜 dal.wiki 위에서 SyncEngine 시나리오·llm_json 파싱·`_iso_z`·catchup 커서 정책).

## 문서 구조

| 파일 | 내용 |
|------|------|
| [00-overview.md](spec/00-overview.md) | 시스템 전체 목적, 액터, 용어 |
| [01-features/](spec/01-features/) | 기능별 명세 |
| [02-data-model.md](spec/02-data-model.md) | 엔티티, 관계, 생명주기 |
| [03-integrations.md](spec/03-integrations.md) | 외부 API 연동 계약 |
| [04-cross-cutting.md](spec/04-cross-cutting.md) | 인증, 재시도, 운영 규칙 |
| [05-edge-cases.md](spec/05-edge-cases.md) | 경계 조건 및 예외 동작 |
| [06-traceability.md](spec/06-traceability.md) | 요구사항 ↔ 코드 추적 매트릭스 |

## 현재 운영 중인 봇 목록

2026-10-22(W5) 기준, 코드·티어 bat·`dalwiki-agent/topics.yaml`에서 확인한 값. 봇 폴더는 `C:\Users\gamer\Documents\<폴더>`, 상태 경로는 그 폴더 기준. 티어 시각·실행 규약은 `C:\Users\gamer\bot_scheduler\README.txt`. 라벨 = bat의 `track.py <라벨>`(대시보드·health_check 키).
기반: **SE** = `botkit.sync.SyncEngine`, **vod** = `botkit.vod` 헬퍼 + 봇 자체 reconciliation, **dc** = `botkit.discord` catchup, **llm** = `botkit.llm`, **client** = `dalwiki_client`만, **자체** = 봇 폴더의 자체 래퍼(내부는 dalwiki_client)·카테고리 API 직접 호출.

### 2x 티어 (매일 11:00·19:30) + HOURLY

| 폴더 (bat) | 라벨 | 소스 | 토픽 | 기반 | 상태 경로 |
|---|---|---|---|---|---|
| chzzkvodbot (run_sched) | chzzkvodbot | 치지직 API(VOD 매칭), 개별 캘린더 → 메인 미러 | 메인 `v1c2BaT1zEvn` + 스트리머 개별 16 | vod | `chzzkvodbot.db` |
| stellivebot (run · 주간 run_reconcile) | stellivebot · stellivebot-reconcile | stellight.fans API + 치지직 VOD | 멤버 개별 11 + 메인(멤버 카테고리) + 그룹 `WnIxykk2aL9f` | vod | `stellivebot.db` |
| aesthervodbot (run · HOURLY sync_hourly) | aesthervodbot | 메인 토픽 미러 + 치지직 VOD | 그룹 `TdjOJNFqzsmf` + 개별 4 | vod | `aesthervodbot.db` |
| cluezvodbot (run) | cluezvodbot | 메인 토픽 미러 + 치지직 VOD | 그룹 `3ZsfB6vOXpKx` + 개별 2 | vod | `cluezvodbot.db` |
| streamersyncbot (HOURLY run) | streamersyncbot | 메인 토픽 HONEYZ·ACAXIA 카테고리 미러 | `WhPckbBXTTsv`, `X6gXMTQFuVIr` | SE(adopt_scope=category) | `state/streamersync.db` |
| gachabot (run_catchup) | gachabot | Discord 게임 채널(TweetShift) + Haiku | 게임 12 + 통합 `IbM473NkLrAE` | SE·dc·llm | `state/gachabot.db` |
| qwerbot (run_catchup · run_redvelvet · run_rescene · 주간 run_namu) | qwerbot · redvelvet · rescene · namu-supplement | Discord(TweetShift) + Haiku, 보조 나무위키 | `aShgvAZWcd7B` QWER, `yTOM32lVDKJ5` 레드벨벳, `4k-F0Htz1dl6` 리센느 | SE·dc·llm | `state/{group}.db` |
| berriz-bots (run_all) | berriz-ive · berriz-kiiikiii | Berriz 공개 캘린더 API | `ahGUO5dJTIOH` IVE, `ovRE7nQSAlX2` KiiiKiii | SE | `state/{artist}.db` |
| timetree-bots (run_seventeen) | seventeen | TimeTree 공개 캘린더 + Haiku 분류 | `rM65HqLLyJc1` 세븐틴 | SE·llm | `state/seventeen.db` |
| dcbot (run_rescene) | dcbot-rescene | 디시 마이너갤 일정표 이미지 + Sonnet 비전 | `4k-F0Htz1dl6` 리센느 | SE·llm(검수 후 `--apply`) | `state/rescene1_sync.db`, 대기열 `state/rescene1_pending.jsonl` |
| lckteambot (run_once) | lckteambot | Leaguepedia Cargo + Discord 팀 SNS + LLM | 팀 10 + 통합 `BzGUT0wOWsgx` | SE·dc·llm | `state/lckteambot.db` |
| msibot (run_msi_bot · run_worlds_bot) | msibot · worldsbot | 네이버 e스포츠 API | `nTCdDehWCBKq` MSI, `U20Zns8YfaAc` 롤드컵 | 자체 | `msi_state.json`, `*_events_db.json` |
| ticketbot (run) | ticketbot | 인터파크·YES24·멜론 티켓 오픈 공지 | `46z005GBfOYB` 콘서트, `MTG8oh05P0iv` 뮤지컬·연극 | SE | `state/ticketbot.db` |
| canceltickbot (run) | canceltickbot | ticketbot 스크래퍼 + 인터파크 잔여석 + Haiku | `eDQQHdy1bN4d` 취켓팅 | SE·llm | `state/canceltick.db` |
| daisobot (run) | daisobot | 다이소몰 사전예약 기획전 API | `L8fI7uBj-6jZ` 다이소 오픈런 | client | `state.json` |
| theatergoodsbot (run) | theatergoodsbot | CGV·롯데시네마·메가박스 특전 페이지 | `VBMiAMPv37Oy` 극장 특전 | client | `state.json` |
| PLBOT (run_once) | PLBOT | football-data.org | 빅6 팀 6 | SE | `state.db` |

### DAILY 티어 (매일 11:15)

| 폴더 (bat) | 라벨 | 소스 | 토픽 | 기반 | 상태 경로 |
|---|---|---|---|---|---|
| KBObot (run_kbo_bot) | KBObot | koreabaseball.com 내부 API | 리그 `vvSHkOrwL1Rh` + 구단 10 | SE | `kbo_state.db` |
| kboticketbot (run_sched) | kboticketbot | KBObot 경기 목록 역산 | `VvaQUi6Q48Ab` KBO 티켓 오픈 | client | `kboticket.db` — ⚠ 입력 `KBObot/kbo_db.json`이 W2 후 없음(후속 이슈) |
| lckbot (run_lck_bot) | lckbot | Leaguepedia Cargo | `EZuS4T1OGjb2` LCK | SE | `state/lckbot.db` |
| onlinegamebot (run) | onlinegamebot | 게임 9종 공지 크롤·넥슨 Open API + Haiku | 게임 9 + 통합 `eznOF4IYU9ox` | SE·llm | `state/state.db` |
| album_bot (run) | album_bot | 멜론·iTunes·Deezer·MusicBrainz·Metacritic·나무위키 | `9LVm3HmCUSnN` 앨범 발매 | 자체 | `releases.db` |
| netflixbox (run_netflixbot) | netflixbot | uNoGS + JustWatch + TMDB | `VOOirIQJ3hhQ` 넷플릭스 | SE | `state/netflixbot.db` |
| dramabot (run_drama_bot) | dramabot | TMDB discover/tv | `excH0WFng4WM` 드라마·예능 신작 | 자체 | `drama_state.json`, `drama_db.json` |
| kdramabot (run_daily) | kdramabot | DAUM EPG 스냅샷 + programs.yaml + OTT | `K4V9xKVLkSxS` 한국 드라마 + 예능 3 | 자체 | `state.json`, `kdrama_db.json` |
| animebot (run) | animebot | 나무위키 분기 애니 분류·문서 | `Fhumn3q1yXWA` 애니메이션 | client | `namu_anime.db` |
| stockmarketBOT (run_kr · run_us) | stockmarket-kr · stockmarket-us | OpenDART·KIND / Alpha Vantage·FMP·Nasdaq | `Z8pPQhuIE6HW` 한국 증시, `mu7Xb68e2UrP` 미국 증시 | SE | `state/stockmarket.db` |
| econcalbot (run_econ_bot) | econcalbot | Nasdaq 경제캘린더(High) | `R6_y96_olqnS` 경제지표 | SE | `state/econcalbot.db` |
| tumblbugbot (run_apply) | tumblbugbot | 텀블벅 API | `3sEDrwwRTo75` 텀블벅 마감 | SE | `state/tumblbug.db` |
| housingbot (run_scheduled) | housingbot | 청약Home Open API | `KiHFceW2e9N8` 청약 | SE | `state/housingbot.db` |
| convinibot (run) | convinibot | BGF 보도자료·뉴스와이어·Google News + Haiku | `WZ8xOfyIJxLz` 편의점 신상 | SE·llm | `state.db` |
| moviebot (run) | moviebot | 메가박스 개봉예정 API | `EsGKyUpMrKVm` 영화 개봉 | client | `movie_bot.db` |
| gamepassbot (run) | gamepassbot | news.xbox.com 게임패스 글 + LLM | `yBU8HbSdzAGR` XBOX 게임패스 | 자체 | `data/state.db` |
| collabcafebot (run) | collabcafebot | Discord(TweetShift) + 팝가·데이포유 + Haiku | `wyJcQePq9IFq` 서브컬쳐 팝업 | SE·dc·llm | `state.db`, `state/cursors.db` |
| naehanbot (run) | naehanbot | 나무위키 '내한 공연/YYYY년' | `8yjCWNnLFgVj` 내한 공연 | 자체 | `naehanbot.db` |
| mnetplusbot (run_apply) | mnetplusbot | Mnet Plus 아티스트 캘린더 | `4k-F0Htz1dl6` 리센느 | SE | `state/mnetplus.db` |
| laftelbot (run_new · 주간 run_expire) | laftelbot-new · laftelbot-expire | 라프텔 API | `6X7pfGtRIOHw` 라프텔 | SE | `state/laftel.db` |
| ipobot (run_apply) | ipobot | 38커뮤니케이션 공모주 | `O2gLoYrjMM7e` 공모주 | SE | `state/ipobot.db` |
| youthpolicybot (run_apply) | youthpolicybot | 온통청년 | `LvzVxU_W6tDi` 청년 정책 | SE | `state/youth.db` |
| campingcalbot (run_sched) | campingcalbot | 숲나들e·KNPS 반복 규칙 | `yPNcS1cyLfj0` 휴양림·야영장 예약 | SE | `state/campingcalbot.db` |
| couponcalbot (run_sched) | couponcalbot | 정책브리핑 보도자료 + Haiku | `33wyyta_Fqdd` 소비쿠폰·할인권 | SE·llm | `state/couponcalbot.db` |
| figurebot (run_scrape · 주간 run_translate·run_delays) | figurebot-scrape · figurebot-translate · figurebot-delay | 굿스마일·알터·카이요도 등 공홈 + LLM 번역 | `N55OnVjoGg7N` 피규어 | SE·llm | `state/figurebot.db` |
| koreafootballbot (run_recent) [시즌] | koreafootballbot | KFA 월별 일정 | `IPCj1xcOK2AL` 국가대표 | SE | `state/koreafootballbot.db` |
| koreansoccerguysbot (run_full) [시즌] | overseas_football_bot | football-data.org + ESPN | `ibVjqypMr9mn` 해외파 선수 | 자체 | `state/state.json` |

### WEEKLY 티어 (월 13:00)

| 폴더 (bat) | 라벨 | 소스 | 토픽 | 기반 | 상태 경로 |
|---|---|---|---|---|---|
| festivalbot (run) | festivalbot | TourAPI + Haiku | `IZdp3T7oB48s` 한국 축제 | SE·llm | `state.db` |
| museum_bot (run_update) | museumbot | KCISA·SeMA·PKM | `ZDF49ygIhO15` 전시회 | SE | `state.db` |
| doujin_bot (run) | doujin_bot | comicw.net 행사 게시판 | `akEt1KVzmve7` 동인 행사 | SE | `state.db` |
| runbot (run) | runbot | 마라톤고·고러닝·로드런 | `MrblvLt48lWt` 마라톤 | 자체 | `races.db` |
| megasale_bot (run) | megasale_bot | `seed_events.json` 시드 + Haiku | `doMQDPF5gu5l` 직구 메가세일 | SE·llm | `state/megasale.db` |
| fgcbot (run_apply) | fgcbot | start.gg + BNE·CPT·STL | `VBxX9AHcxhiO` 철권, `aG892YPuMZXD` 스파6 | 자체 | `data/state.json` |
| gamingschedulebot (run_freegame) | gamingschedulebot | 에픽·PS Plus·GamerPower(GOG)·STOVE | `kdWPdGk04_gH` 무료게임 | 자체 | `data/seen.db` |
| taxcalbot (run_sched) | taxcalbot | `seed.py` 정적 규칙 | `YXJVydUEl9m6` 세금 신고·납부 | SE | `state/taxcalbot.db` |

(stellivebot reconcile·qwerbot namu·figurebot translate/delays·laftelbot expire도 WEEKLY — 위 표의 해당 행 참조.)

`topics.yaml`에 아직 없는 토픽: `WnIxykk2aL9f`(stellive 그룹), `4k-F0Htz1dl6`(리센느, qwer·mnetplus·dcbot 공유), `L8fI7uBj-6jZ`, `R6_y96_olqnS`, `LvzVxU_W6tDi`, `33wyyta_Fqdd`, `kdWPdGk04_gH`.

### 아카이브 (`C:\Users\gamer\Documents\_archive_bots`, 티어에서 빠짐)

| 폴더 | 무엇이었나 |
|---|---|
| asiangamesbot | 2026 아이치·나고야 아시안게임(본체 + e스포츠, 반자동). 대회 종료 10-04 → 2026-10-15 은퇴 |
| ewcbot | E스포츠 월드컵 2026 종목별 Liquipedia 봇. 시즌 작업 2026-08-24 만료 |
| holidaytrafficbot | 설·추석 승차권/버스 예매 일정(반자동, 연 2회) |
| musicshowbot | 음악방송 3사 출연진 게시물 → 라인업 캘린더 |
| pointfarm_sports_bot | dal.wiki 봇 아님 — pointfarm 예측 마켓 생성·정산(Supabase) |
| worldcupbot | 2026 FIFA 월드컵 104경기 단일 토픽. 대회 종료 07-19 → 은퇴 |

이전 목록에 있던 pokemonTCGbot·OTTbot은 현재 폴더·티어 모두 없음.
