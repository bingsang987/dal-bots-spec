# 공통 관심사 (Cross-Cutting Concerns)

## 실행 모드

모든 봇은 다음 모드를 지원한다. `--dry-run` 플래그 또는 `.env`의 `DRY_RUN=true`로 활성화.

| 모드 | 설명 | dal.wiki API 호출 |
|------|------|-----------------|
| **Dry-Run** | 변경사항을 로그로만 출력 | 없음 (가짜 ID 반환: `"dry-{timestamp}"`) |
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

- POST 성공 → 반환된 UUID를 로컬 DB `dalwiki_id`에 저장
- PATCH 시 저장된 UUID 사용; 없으면 POST로 대체 (복구 경로)
- DELETE 시 `dalwiki_id`를 NULL로 초기화

---

## 환경변수 구성

### 공통 (전 봇 공유)

| 변수 | 설명 |
|------|------|
| `DALWIKI_API_BASE` | API 기본 URL (`https://api.dal.wiki`) |
| `DALWIKI_API_KEY` | Bearer 인증 토큰 (없으면 미인증 접근) |
| `DRY_RUN` | `true` 설정 시 드라이런 모드 |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` |
| `LOG_DIR` | 로그 파일 저장 경로 |

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

- 모든 봇은 `logging` 모듈 사용
- 포맷: `{timestamp} [{level}] {message}`
- 파일 로그 + 콘솔 출력 (기본)
- 중요 이벤트: POST/PATCH/DELETE 성공·실패 결과를 INFO 레벨로 기록
- API 오류: 상태 코드 + 응답 본문을 WARNING/ERROR로 기록

---

## HTTP 세션 관리

- 프로세스당 `requests.Session()` 싱글턴 하나를 공유하여 연결 재사용
- `Content-Type: application/json` 헤더를 세션 레벨에서 설정
- 봇별 `User-Agent` 문자열 설정 (소스 사이트 차단 방지)

---

## 시간대 처리

- 모든 이벤트는 `Asia/Seoul` (KST, UTC+9) 기준으로 처리
- 외부 소스가 UTC로 반환하는 경우 KST로 변환 후 저장
- ISO 8601 형식: `YYYY-MM-DDTHH:MM:SS+09:00`
- `naive datetime` 사용 금지; 항상 timezone-aware datetime 사용

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
