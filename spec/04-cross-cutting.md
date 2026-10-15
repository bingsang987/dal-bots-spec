# 공통 관심사 (Cross-Cutting Concerns)

## 실행 모드

모든 봇은 다음 모드를 지원한다. `--dry-run` 플래그 또는 `.env`의 `DRY_RUN=true`로 활성화.

| 모드 | 설명 | dal.wiki API 호출 |
|------|------|-----------------|
| **Dry-Run** | 변경사항을 로그로만 출력. **state·커서 미저장** | 쓰기(create/update/delete) 없음 — 가짜 ID `"dry-{timestamp}-{n}"`. **조회 POST(`events/list·get`, `topics/get·histories`, `categories/*`)는 실제 호출**(pointfarm-cb4) |
| **Live** | 실제 POST/PATCH/DELETE 수행 | 있음 |
| **Backfill** | 과거 날짜 범위 소급 등록 | 있음 |
| **Resync** | content_hash 무관하게 강제 PATCH | 있음 |
| **Cleanup** | 오래된 이벤트 삭제 | DELETE 있음 |

---

## 중복 제거 (Deduplication)

### 레이어 1 — 소스 정규화

외부 소스에서 데이터를 수집한 직후, 봇 내부에서 canonical key를 생성한다.

```
canonical_key = f"{date}_{region}_{distance_bucket}"   # runbot 예시
canonical_key = f"{manufacturer}_{jan_code}"            # figurebot 예시
canonical_key = f"{game_id}_{notice_id}"               # onlinegamebot 예시
```

### 레이어 2 — Content Hash 비교

```python
current_hash = hash(title + date + url + description)
stored_hash  = db.get(canonical_key).content_hash

if stored_hash is None:
    action = "NEW"       # → POST
elif current_hash != stored_hash:
    action = "CHANGED"   # → PATCH
else:
    action = "UNCHANGED" # → 건너뜀
```

### 레이어 3 — dal.wiki ID 추적

- POST 성공 → 반환된 ID를 로컬 DB에 저장. **쓰기가 성공한 뒤에만 저장**(실패했는데 저장하면 다음 run이 404만 받는다)
- PATCH 시 저장된 ID 사용; 404면 `TopicCache`로 (summary, start) 자연 키 복구 → 그래도 없으면 POST (복구 경로)
- DELETE 성공(또는 이미 404) 시 행 제거

위 3레이어는 `botkit.sync.SyncEngine`이 구현한다. 신규/재구축 봇은 직접 짜지 않고 이것을 쓴다(§ botkit 참조).

### 레이어 4 — 조회 실패와 "없음"의 구분 (dalbots-bmi.12)

`dalwiki_client.list_events`는 400/5xx/네트워크 재시도 소진 시 **`ListFailed` 예외**를 던진다(`[]`가 아님). 호출자 규칙:

- 조회 결과로 **삭제·prune·DAY_WIPE·재생성**을 판단하는 코드는 `ListFailed`를 잡아 **그 범위의 삭제/생성을 전부 스킵**하고 `[HEALTH-ALERT]`를 낸다.
- `TopicCache.load()`는 기본 strict: 청크 하나라도 실패하면 예외 → `SyncEngine`은 `SyncAborted`로 이번 run 쓰기를 전면 중단한다.
- `raise_on_fail=False`(구 동작)는 "표시용 조회"에만. from/to 둘 중 하나가 없으면 truncation 자동 분할도 꺼진다는 점을 기억할 것.

---

## 환경변수 구성

### 공통 (전 봇 공유)

| 변수 | 설명 |
|------|------|
| `DALWIKI_API_BASE` | API 기본 URL (기본값 `https://api.dal.wiki/api-reference`) |
| `DALWIKI_API_KEY` | **`X-Api-Key` 헤더**로 전송되는 API 키 (Bearer 아님). 봇 코드에 하드코딩 금지, `.env`에만 |
| `DRY_RUN` | `true` 설정 시 드라이런 모드. 클라이언트가 매 호출 시점에 읽음 |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` |
| `LOG_DIR` | 로그 파일 저장 경로 (`botkit.run` 기본: 실행 스크립트 폴더의 `logs/`) |
| `ANTHROPIC_API_KEY` | `botkit.llm` 사용 봇. 봇별로 별도 키 발급 권장(1키 N봇 공유 → 동시 소진) |
| `DALWIKI_DISCORD_WEBHOOK_URL` | 있으면 `alert()`가 Discord로도 전송 |

### 봇별

| 변수 패턴 | 예시 |
|-----------|------|
| `{NAME}_TOPIC_ID` | `MARATHON_TOPIC_ID`, `KBO_TOPIC_ID` |
| `{GAME}_TOPIC_ID` + `UNIFIED_TOPIC_ID` | dual-posting 봇 |
| 소스별 API 키 | `TOURAPI_KEY`, `FOOTBALL_DATA_API_KEY` |

---

## 실행 스케줄

Windows 작업 스케줄러가 `.bat` 파일을 주기 실행한다.

| 파일명 패턴 | 주기 예시 |
|------------|----------|
| `run.bat` | 매일 1회 (새벽 시간대) |
| `매일_run.bat` | 매일 |
| `주1회_*.bat` | 매주 1회 |
| `주2회_*.bat` | 매주 2회 |
| `dry_run.bat` | 수동 테스트용 |
| `first_run.bat` | 초기 설치 시 1회 |

---

## 로깅

- 모든 봇은 `logging` 모듈 사용 (`botkit.run`이 설정을 대신한다)
- 포맷: `{timestamp} [{level}] {logger}: {message}`
- 파일 로그(`logs/<bot>.log`, **RotatingFileHandler 5MB × 3**) + 콘솔(stdout, utf-8)
- 중요 이벤트: POST/PATCH/DELETE 성공·실패 결과를 INFO 레벨로 기록
- API 오류: 상태 코드 + 응답 본문을 WARNING/ERROR로 기록
- 로그 파일을 무한히 키우지 않는다(runbot 3.3MB, namu_cache 69MB 사례)

---

## `[HEALTH-ALERT]` 규약 (dalbots-bmi.21)

health_check는 각 봇 run의 stdout(`lastrun_*.out`)에서 **`[HEALTH-ALERT]` 한 줄**만 본다. "오류를 처리하고 정상 종료"한 run은 이 줄이 없으면 무감지다(10-01 크레딧 소진 때 6봇 무감지).

### 반드시 alert 해야 하는 상황

| 상황 | 예 | 동반 조치 |
|------|----|----------|
| 주 소스 전부 실패 | 나무위키 차단, API 403/5xx 연속 | 동기화·absent 처리 스킵 |
| 소스 0건 | KBO API 월간 0경기, TourAPI 0건 | absent(소멸 판정) 스킵 — `SyncEngine`이 자동 |
| dal.wiki 장애 | `ListFailed`, `SyncAborted`, 쓰기 실패 다수 | 그 범위 삭제/생성 스킵, state 미저장 |
| LLM 불가 | `LLMUnavailable`(크레딧 소진·401·과부하 재시도 소진) | **커서·state 전진 금지**, 다음 run 재시도 |
| 처리되지 않은 예외 | 어떤 Traceback | `botkit.run`이 자동으로 alert + exit 1 |

### 형식

```
[HEALTH-ALERT] <bot_name>: <한 줄 사유>
```

- 태그는 정확히 `[HEALTH-ALERT]`. `[ALERT]`·`[WARN]` 등 변형 금지(daisobot·theatergoodsbot·musicshowbot 사례 — 정규식에 안 걸림).
- stdout에 flush 출력(파일 로그에만 쓰면 health_check가 못 본다). `botkit.run.alert(msg)`가 이 형식을 보장한다.
- 한 run에 여러 번 가능. alert만 있고 예외가 없으면 exit 0(처리된 저하). 티어 bat의 RETRY는 "마지막 20줄에 Traceback"으로만 판단한다.
- 시즌 종료 등 **예상된 0건**은 alert 대상이 아니다 — 비시즌 가드로 run 자체를 스킵하거나 `topics.yaml seasonal`로 제외.

---

## botkit 사용 규약 (정비 계획 §2, 2026-10-15)

`src/botkit/` 모듈 요약과 골격은 [README](../README.md#공통-실행-기반-botkit-2026-10-15-정비-계획-2) 참조. 규칙만 적는다.

1. **쓰기는 전부 `dalwiki_client` 경유.** 봇 폴더에 `dalwiki.py`/`dalwiki_client.py` 래퍼를 두지 않는다(5벌 복제·깨진 버전 사고).
2. **동기화는 `SyncEngine.sync(events, source_complete=…, source_window=…)` 한 번.** 봇은 `Event` 목록만 만든다. key는 run이 바뀌어도 같은 안정적 자연 키.
3. **소멸(absent) 정책**: 기본 `delete`. 단 소스 0건·일부 실패 run은 엔진이 자동으로 absent를 스킵한다. "앞으로 N일"만 긁는 봇은 `source_window`를 꼭 준다.
4. **state는 쓰기 성공 후에만.** dry-run은 state·커서를 저장하지 않는다.
5. **LLM**: 모델 ID는 `botkit.llm.HAIKU/SONNET`만. 응답 파싱은 `llm_json`(탐욕 regex 금지). `LLMUnavailable`은 잡아서 커서 정지 + alert, `LLMParseError`는 그 입력만 재시도 큐.
6. **Discord**: `botkit.discord.catchup`. 커서는 메시지 처리 성공 뒤에만 전진. 데몬이 아니라 `run_catchup_once`로 티어에서 1회 실행.
7. **실행**: `run(bot_name, main_fn)`으로 감싼다. `--dry-run` 표준. `track.py` 호출은 기존처럼 bat에 둔다(래퍼가 대체하지 않음).
8. 이벤트 필드: `categoryId` 항상 명시, summary에 `[카테고리]` prefix 금지, description 링크는 `target="_blank" rel="noopener noreferrer"`, **본문에 출처 표기 금지**(원본은 `link` 칸), 종일 이벤트는 00:00~23:59:59 KST(start=end 금지).

---

## HTTP 세션 관리

- 프로세스당 `requests.Session()` 싱글턴 하나를 공유하여 연결 재사용
- `Content-Type: application/json` 헤더를 세션 레벨에서 설정
- 봇별 `User-Agent` 문자열 설정 (소스 사이트 차단 방지)

---

## 시간대 처리

- 봇 내부 계산은 `Asia/Seoul` (KST, UTC+9) 기준. `startTimezone`/`endTimezone`은 `Asia/Seoul`.
- **dal.wiki API로 보내는 start/end는 UTC `YYYY-MM-DDTHH:MM:SS.000Z`** 하나뿐이다. `+09:00` 오프셋 포맷을 보내면 400. 서버가 돌려주는 start도 이 포맷이므로 (summary, start) 자연 키 비교는 이 포맷으로만 성립한다.
- 변환은 `dalwiki_client._iso_z`가 담당: aware datetime / Unix ms / ISO 문자열(Z, +09:00, 밀리초 유무 무관)을 모두 `.000Z`로 정규화. 봇이 직접 `strftime("…Z")`로 만들지 않는다(figurebot `T00:00:00Z` → 캐시 키 불일치 → 중복 66그룹 사례).
- `naive datetime` 사용 금지(클라이언트가 `ValueError`); 항상 timezone-aware datetime 사용. UTC↔KST 변환 후에는 기존 저장값 샘플과 대조해 9시간 어긋남이 없는지 확인.
- 종일(allDay) 이벤트: 그날 00:00~23:59:59 KST. start=end면 월간 뷰에서 빠진다.

---

## HTML 이스케이프 규칙

description에 외부 데이터를 포함할 때:

```python
import html
safe_text = html.escape(user_text)          # 일반 텍스트
safe_url  = html.escape(url, quote=True)   # URL
```

---

## 봇 대시보드 추적

모든 봇 실행은 `track.py`를 통해 중앙 대시보드에 기록된다.

- 신규 봇을 추가할 때 대시보드 레이블도 함께 등록해야 한다
- `dashboard.py`로 현황 조회 가능
- `botkit.run`은 track.py를 대체하지 않는다 — bat에서 기존처럼 호출(사용자 결정 2026-10-15)

---

## ⚠️ 카테고리 이중표시 (`[청약] [청약]`) — 재발 1순위 함정

### 증상
이벤트가 캘린더에 `[청약] [청약] 스트라드비젼` 처럼 **카테고리 태그가 두 번** 찍힌다.

### 원인 (왜 자꾸 반복되는가)
1. **레거시 메커니즘의 잔상.** 구 dal.wiki는 `summary` 맨 앞 대괄호를 **자동으로 카테고리로 추출**했다. 그래서 옛 봇들은 의도적으로 `summary = "[카테고리] 제목"` 으로 만들었다.
2. **마이그레이션 후 의미가 뒤집힘.** API v2 + UI 개편으로 카테고리는 **`categoryId`로 명시 지정**하고, **UI가 그 카테고리명을 `[이름]` 칩으로 자동 prepend**한다. 이제 summary의 대괄호 prefix는 불필요 + 중복.
3. **재발 메커니즘.** 신규 봇을 짤 때 (a) 옛 봇 코드를 참고/복붙하며 `f"[{label}] {title}"` 패턴이 따라오고, (b) `categoryId`도 올바르게 지정 → **옛 방식 + 새 방식이 동시에 적용**되어 칩이 두 번 뜬다. 한쪽만 틀린 게 아니라 "둘 다 맞게 한" 게 함정.

### 규칙 (확정)
- **`summary`에는 카테고리 대괄호 prefix를 절대 넣지 않는다.** 순수 콘텐츠(제목·부가정보)만.
- 카테고리는 **오직 `categoryId`로만** 지정한다 (항상 명시 — `categoryId` 자동추출에 의존 금지).
- 콘솔/로그에서 카테고리를 보고 싶으면 **로그 문자열에만** `[etype]`을 붙이고, **API에 보내는 `summary`에는 넣지 않는다.**

```python
# ❌ 금지 — UI 칩과 중복되어 [청약] [청약] 이중표시
summary = f"[{label}] {corp}"
create_event(summary=summary, category_id=cat_id)

# ✅ 올바름 — summary는 콘텐츠만, 카테고리는 categoryId로만
summary = corp                       # 예: "스트라드비젼 (공모 14,000원)"
create_event(summary=summary, category_id=cat_id)
```

### 빌드 전 체크 / 탐지
- 신규 봇 `build_*_summary` 작성 시: **summary 문자열이 `[` 로 시작하면 거의 버그.**
- 사후 점검: `list_events` 결과의 `summary`가 `[`로 시작하는지 grep. 시작하면 이중표시 의심.
- 적발 이력: ipobot (2026-06-10) — 메모리에 규칙이 있었으나 코드 작성 시 미적용. 그래서 이 함정을 **스펙 문서(빌드 전 필독)** 에 명시. 관련 메모리: `dalwiki-summary-prefix-deprecated`, `dalwiki-category-ui-behavior`, `feedback_dalwiki_categoryid_explicit`.
