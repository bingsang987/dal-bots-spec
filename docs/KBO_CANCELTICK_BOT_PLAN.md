# kbocanceltickbot 설계 — KBO 취켓팅(취소표) 계산기 봇 (2026-10-26)

실사: `docs/KBO_CANCELTICK_SURVEY_2026-10.md`(규정·근거). 규칙: `W2_REBUILD_BRIEF.md`. 패키지: botkit(SyncEngine/run), dalwiki_client.
원리: **소스 스크래핑 없음.** dal.wiki 'KBO 리그' 토픽(KBObot이 관리)의 경기 일정 × 예매처 규정 → 취켓팅 시각 이벤트를 역산해 새 토픽에 등록한다.

## 1. 토픽·카테고리 (관제탑이 생성 완료)
- 토픽 `rtWGH1oRAKc4` 'KBO 취켓팅(취소표)'
- 카테고리: 취소마감 막차 `XviyCWmALrKl` / 자정 막차 `mIv9Xk5-bjDB` (Default `tgRhC5zODRe_`는 쓰지 않음)

## 2. 입력
- **경기**: `dalwiki_client.TopicCache` 또는 `list_events`로 KBO 리그 토픽 `vvSHkOrwL1Rh`의 [오늘-1일, 오늘+14일] 이벤트. 조회 실패(ListFailed)면 `source_complete=False`(absent 보류)·alert.
  - 라운드 = categoryId: 정규 `6x1e7y5tf6MP`(순위결정전 포함) / WC `-ildJOwqUcbY` / 준PO `uZ-nWqTfYKqe` / PO `oeAN7ILl-fdn` / KS `dIG6fEQL_yey`. 올스타 `n5YSPg6j5slj`·시범 `XQPmF6g4gBu2`는 제외.
  - summary 형식 `원정 vs 홈` 약칭(예: `삼성 vs KIA`) → 홈 팀 약칭. 약칭→홈 코드 표는 KBObot `TEAMS`(C:\Users\gamer\Documents\KBObot\bot.py)와 동일하게 config에 둔다.
  - 더블헤더: 같은 날 같은 카드 2건이면 start 순으로 DH1/DH2.
- **예매처 V**(홈 코드 기준, 정규시즌): 티켓링크 = LG·HH·SS·KT·HT / NOL = OB·WO / **제외(1차)** = SK·LT·NC(규정 미확인). 포스트시즌은 홈과 무관하게 NOL.
- **오픈 일정 `openings.yaml`**(사람이 공지 보고 적음; 봇은 읽기만):
  ```yaml
  # 포스트시즌: 라운드별 예매 오픈. 경기 날짜 목록 ↔ 오픈 일시(KST)
  - round: PO
    games: ["2026-10-26", "2026-10-27", "2026-11-01"]   # 대구 1·2·5차전
    open: "2026-10-21T14:00"
  - round: PO
    games: ["2026-10-29", "2026-10-30"]
    open: "2026-10-22T14:00"
  # KS: 공지 확인 후 추가
  ```
  정규시즌 오픈은 구단 규칙으로 계산: 티켓링크 5구단 '시리즈 첫 경기 D-7' (LG·HH·SS·HT 11:00, KT 16:00), NOL 두산 D-7 11:00, 키움 D-7 14:00. 3연전 묶음 판정 = 같은 홈·같은 상대 연속 경기.

## 3. 이벤트 규칙 (KST)
| 코드 | 카테고리 | 대상 | start~end | 요약(summary) |
|---|---|---|---|---|
| K1 | 취소마감 막차 | 정규 (TL·NOL) | **[T-5h, T-4h]** | `{홈} vs {원정} 취소마감 막차 (티켓링크\|NOL)` |
| K1p | 취소마감 막차 | 포스트시즌(NOL) | **[T-6h, T-5h]** | `{라운드} {n}차전 {홈} vs {원정} 취소마감 막차 (NOL)` — n차전은 같은 라운드 경기 start 순서 |
| K2 | 자정 막차 | 정규·PS, 오픈일 알 때만 | **오픈일 [23:00, 24:00)** | `{라운드/구단 시리즈} 예매 당일 자정 막차 (무료취소 마감{·미입금 취소})` |
| K3 | 자정 막차 | **NOL 두산 정규만** | **오픈일 [23:30, 23:45)** | K2와 같은 오픈일이면 **K2 하나에 합치고 본문에 23:30 미입금 자동취소 문구** (별도 이벤트 금지) |
- 본문(description): 규정 요약만. 예: PS "취소 마감 경기 시작 5시간 전 · 수수료 10%(예매 당일 자정 전 무료) · 무통장 불가 · 취소분 현장 판매 경기 2시간 전~8회말 · 1인 4매". 정규 TL: "취소 마감 4시간 전 · 10% · 취소표대기 07:00 안내(2시간 내 결제)". **출처·사이트명 표기 금지**, 링크는 `link` 필드만: PS → `https://nol.yanolja.com/ticket/genre/sports/postseason`, TL 구단 → `https://www.ticketlink.co.kr/sports/137/{id}`(LG59·HH63·SS57·KT62·HT58), NOL 두산 `.../sports/bears`, 키움 `.../sports/heroes`.
- 제외: 올스타·시범·SK/LT/NC 홈 정규경기. 경기 start가 이미 지난 것. 이벤트 end가 이미 지난 것은 생성하지 않음(오늘 11:00 run이 12:30 창을 만드는 건 OK).
- **K4(아침 09:00 대기열 방출)·K5(우천 순연 자정)는 2차** — 매진 신호 소스와 순연 감지가 없음. 코드에 자리만(주석) 두고 구현하지 않는다.

## 4. 키·엔진
- `SyncEngine(topic="rtWGH1oRAKc4", state="state/kbocanceltick.db", absent="delete")`, `sync(events, source_complete=kbo_fetch_ok, source_window=(오늘-1일, 오늘+14일))`.
- key: 경기 단위 `KBO|{YYYYMMDD}|{원정코드}{홈코드}|{DH}|K1` / 오픈일 묶음 `KBO|{V}|{오픈일 YYYYMMDD}|K2`. 우천 순연으로 KBObot 쪽 날짜가 바뀌면 옛 키는 absent 삭제, 새 키 생성(공연 취켓팅과 같은 정책).
- 하루 2회(2x 티어)라 run 간 변화는 KBObot 갱신 반영뿐. 2회째 전건 UNCHANGED여야 한다.

## 5. 운영
- 폴더 `C:\Users\gamer\Documents\kbocanceltickbot`(신규 private repo bingsang987/kbocanceltickbot), `bot.py`(botkit.run, bot 이름 `kbocanceltickbot`), `config.py`, `openings.yaml`, `run.bat`(ASCII, `track.py kbocanceltickbot`, pause). `.env`는 다른 봇 .env에서 DALWIKI_API_KEY만.
- 티어: **2x**(11:00/19:30) — 관제탑이 `run_tier_2x.bat`·health_check MONITOR·topics.yaml에 편입. 세션은 bat만 만든다.
- alert: KBO 토픽 조회 실패 / openings.yaml 파싱 실패 / 대상 PS 경기가 있는데 오픈 정보가 없을 때(info 로그, alert 아님).

## 6. 검증
1. dry-run: 오늘(10-26) 기준 예상 출력 = PO2 10-27 12:30~13:30, PO3 10-29 12:30~13:30, PO4 10-30 12:30~13:30, PO5 11-01 08:00~09:00 (K1p 4건) + K2는 오픈일이 지나서 0건. 실사 §5-4 표와 일치해야 함.
2. 실제 run → 토픽에 4건, 2회째 UNCHANGED 4.
3. 시뮬레이션: KBO 토픽 조회 ListFailed 주입 → 쓰기 0 + absent 보류 + alert 1줄.
4. 정규시즌 로직은 과거 경기로 dry-run: `--as-of 2026-08-14` 같은 플래그로 기준일을 바꿔 8월 3연전에서 K1·K2(·두산 K3 합침)가 규칙대로 나오는지 표로 보고(등록은 안 함).
5. 11-02/03 KS 1차전: 공지 확인되면 openings.yaml에 추가 → 다음 run에 K2 생성되는지(관제탑).

## 7. 하지 말 것
KBObot·다른 봇·패키지 수정, 티어 bat·health_check 수정, 예매처 스크래핑, 로그인/차단 우회, 본문 출처 표기, Default 카테고리 사용.
