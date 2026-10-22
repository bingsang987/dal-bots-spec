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

| 봇 이름 | 대상 소스 | 캘린더 |
|---------|----------|--------|
| animebot | AniList GraphQL | 애니 방영 일정 |
| KBObot | koreabaseball.com hidden API | KBO 야구 |
| chzzkvodbot | Naver Chzzk VOD API | 스트리머별 VOD |
| aesthervodbot | Chzzk + 메인 캘린더 | AESTHER 4명 |
| netflixbox | 내부 데이터 | Netflix KR |
| figurebot | 6개 제조사 웹사이트 스크래핑 | 피규어 발매 |
| doujin_bot | comicw.net | 동인 행사 |
| album_bot | MusicBrainz/Melon/iTunes/Deezer | 앨범 발매 |
| festivalbot | 한국관광공사 TourAPI | 문화 축제 |
| laftelbot | api.laftel.net | 라프텔 스트리밍 |
| runbot | gorunning/marathongo/roadrun | 마라톤/달리기 |
| ticketbot | 인터파크/YES24/멜론 | 티켓 오픈 |
| gamingschedulebot | Epic/PS Plus/GOG | 무료 게임 |
| convinibot | CU/GS25 보도자료 + 뉴스와이어 | 편의점 신상품 |
| megasale_bot | Amazon/AliExpress/iHerb 등 | 해외직구 세일 |
| pokemonTCGbot | 공식 홈페이지 스크래핑 | 포켓몬 TCG |
| ewcbot | Liquipedia + start.gg | e스포츠 월드컵 |
| worldcupbot | football-data.org | FIFA 월드컵 |
| koreafootballbot | KFA + football-data.org | 한국 축구 |
| koreansoccerguysbot | football-data.org + ESPN | 해외파 축구 |
| onlinegamebot | 11개 PC 게임 공지 API/크롤링 | 온라인 게임 |
| housingbot | 청약Home | 청약 일정 |
| museumbot | KCISA + SeMA + PKM | 박물관·미술관 |
| collabcafebot | 서브컬쳐 팝업 소스 | 콜라보 카페 |
| OTTbot | JustWatch API | OTT 콘텐츠 |
| lckteambot | Liquipedia | LCK 팀별 일정 |
| stockmarketBOT | 증권 API | 주식 이벤트 |
