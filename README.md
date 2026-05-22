# dal.wiki 봇 생태계 명세서

dal.wiki 캘린더에 이벤트를 자동 등록하는 봇들의 구조와 동작 방식을 역공학(reverse-engineering)하여 정리한 문서입니다.

이 명세서를 기반으로 새로운 봇을 처음부터 구현할 수 있도록 작성되었습니다.

## 공통 클라이언트 패키지 `dalwiki_client`

`src/dalwiki_client/`에 dal.wiki API v2 공통 클라이언트가 패키지 형태로 들어있다. 봇별 자체 구현(`dalwiki.py` / `dalwiki_client.py`) 대신 이것을 쓰면 다음 기능을 자동으로 얻는다:

- `/events/list` 1000 cap 자동 분할 (truncation 감지 → 시간 윈도우 반으로 재호출)
- `update_event` 404 시 자동 fallback (`update_or_create_event` 헬퍼)
- `categoryIds`, `perDateLimit`, `tz` 신 API 파라미터 지원
- ISO Z 포맷 변환 헬퍼 (datetime aware / Unix ms / 이미 ISO 문자열 모두 입력 가능)
- DRY_RUN 환경변수 분기, 100자 summary 자동 클립, 429/5xx 지수 백오프 재시도

설치 (각 봇 venv에서):

```bash
pip install -e C:/Users/gamer/Documents/GitHub/dal-bots-spec
```

사용 예:

```python
from dalwiki_client import list_events, create_event, update_or_create_event

events = list_events(topic_id="...", from_=start_dt, to=end_dt, category_ids=["..."])
new_id = create_event(topic_id="...", summary="...", start=dt, end=dt, all_day=True, category_id="...")
final_id = update_or_create_event(maybe_legacy_id, topic_id="...", summary="...", start=dt, end=dt)
```

각 봇 마이그레이션은 점진적으로 진행. `migration/dalwiki_client_v2.py`는 패키지의 origin 템플릿.

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
