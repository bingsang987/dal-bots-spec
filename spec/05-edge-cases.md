# 엣지 케이스 및 경계 조건

## API 응답 관련

### EC-01 — response body의 ID 키 불일치

**조건**: POST/PATCH 응답의 최상위 키가 `"id"` 또는 `"eventId"` 둘 중 하나.

**동작**: 클라이언트는 두 키 모두 시도해야 한다.

```python
event_id = data.get("id") or data.get("eventId")
```

**관련 요구사항**: INT-R1

---

### EC-02 — DELETE 시 502 응답

**조건**: DELETE 요청에 502가 반환되는 경우가 있음.

**동작**: 1.5초 × 시도 횟수 대기 후 최대 3회 재시도. 그 외 오류는 즉시 실패.

**관련 요구사항**: INT-R4

---

### EC-03 — PATCH 후 빈 응답 바디

**조건**: PATCH 성공 시 서버가 `{}` 또는 빈 바디 + 204 반환.

**동작**: 상태 코드만으로 성공 여부 판단. 바디 파싱 오류 무시.

**관련 요구사항**: INT-R2

---

## 페이로드 필드 관련

### EC-04 — link/location 빈 문자열 금지

**조건**: `link` 또는 `location` 필드가 None, `""`, 또는 미설정.

**동작**: 해당 키를 페이로드 dict에서 **완전히 제거**. 빈 문자열로 설정하면 API 오류 또는 부정확한 표시 발생.

```python
payload = {...}
if not link:
    payload.pop("link", None)
```

**관련 요구사항**: INT-R3

---

### EC-05 — summary 100자 초과

**조건**: 이벤트 제목이 100자를 넘는 경우.

**동작**: 97자로 자르고 `"..."` 추가. API 측에서도 처리하지만, 클라이언트에서 사전 처리 권장.

**관련 요구사항**: INT-R5

---

### EC-06 — allDay 이벤트에서 start == end

**조건**: `allDay: true`이고 start 날짜 = end 날짜를 00:00으로 설정한 경우.

**동작**: dal.wiki UI에 이벤트가 표시되지 않음. `end`는 반드시 해당일 `23:59:59+09:00`으로 설정해야 한다.

**관련 요구사항**: INT-R6

---

### EC-07 — PATCH 시 필드 생략

**조건**: PATCH 요청에서 원래 설정했던 필드를 생략.

**동작**: 해당 필드가 서버에서 지워짐. PATCH는 partial update가 아닌 full replacement.

**관련 요구사항**: INT-R2

---

## 중복·멱등성 관련

### EC-08 — dry-run ID가 DB에 저장되는 경우

**조건**: dry-run 모드에서 반환된 가짜 ID(`"dry-{timestamp}"`)가 실수로 DB에 저장됨.

**동작**: 다음 live 실행 시 PATCH 대상 UUID가 없어 404 → POST로 폴백하여 이벤트 중복 등록.

**방지**: dry-run 시 DB write를 완전히 건너뜀.

**관련 요구사항**: DEDUP-R3

---

### EC-09 — dalwiki_id 누락 (복구 경로)

**조건**: `dalwiki_id`가 NULL인데 content_hash가 이미 저장된 경우 (이전 POST 실패 또는 DB 유실).

**동작**: PATCH 대신 POST 시도. 성공 시 새 UUID 저장. (dal.wiki 측에서 중복 이벤트 생성 가능성 있음)

**관련 요구사항**: DEDUP-R2

---

## 소스 데이터 관련

### EC-10 — release_date가 월(月)만 있는 경우

**조건**: 피규어 발매월이 `"2026-08"` 형태 (일자 미정).

**동작**: 해당 월의 1일(`2026-08-01`)을 start로 사용. `allDay: true`, 제목에 "(발매월 미정)" 등의 표기 추가.

**관련 요구사항**: F-FIG-R1

---

### EC-11 — 외부 API rate limit 초과

**조건**: Liquipedia API 등 엄격한 제한이 있는 소스에서 429 반환.

**동작**: 지수 백오프 후 재시도. Liquipedia는 parse API 30초/요청 제한 — 위반 시 IP 차단.

**관련 요구사항**: INT-R7

---

### EC-12 — 이벤트 소스에서 사라진 항목

**조건**: 이전에 등록했던 이벤트가 외부 소스 응답에서 더 이상 나오지 않음.

**동작**: 봇마다 다름:
- 일부 봇: 자동으로 DELETE 실행
- 일부 봇: 운영자에게 로그로 알리고 수동 처리

**관련 요구사항**: OPSOPS-R1

---

## 시간대 관련

### EC-13 — UTC 원본 데이터의 날짜 경계

**조건**: 외부 소스가 UTC 타임스탬프 반환. UTC 기준으로는 5월 17일이지만 KST로는 5월 18일.

**동작**: 변환 시 반드시 KST 기준 날짜를 기준으로 이벤트 날짜 결정. 기존 DB의 저장값 샘플을 먼저 확인하여 저장 형식을 파악한 후 변환.

**관련 요구사항**: CROSS-TZ-R1

---

### EC-14 — naive datetime 혼용

**조건**: `datetime.now()` 등 timezone-aware 없이 생성된 datetime 객체가 코드 내 혼용.

**동작**: 비교 연산 시 TypeError 발생 또는 잘못된 시간 계산.

**방지**: 모든 datetime은 `datetime.now(tz=ZoneInfo("Asia/Seoul"))` 등으로 timezone-aware하게 생성.

---

## dual-posting 관련

### EC-15 — 두 캘린더 중 하나만 성공

**조건**: 같은 이벤트를 두 토픽에 POST할 때 첫 번째는 성공, 두 번째는 실패.

**동작**: 첫 번째 UUID는 저장. 두 번째 `dalwiki_id_2`는 NULL 유지. 다음 실행 시 첫 번째는 건너뛰고 두 번째만 재시도.

**관련 요구사항**: F-DUAL-R1

---

## AI 추출 관련 (convinibot, megasale_bot)

### EC-16 — Claude API 추출 실패

**조건**: Claude API가 필드 추출에 실패하거나 confidence가 낮은 경우.

**동작**: 해당 이벤트 등록 건너뜀. 로그에 기록. 이후 실행에서 재시도하지 않음 (사전 필터로 같은 항목이 다시 들어오지 않도록 처리).

**관련 요구사항**: F-AI-R1
