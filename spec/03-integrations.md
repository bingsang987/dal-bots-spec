# dal.wiki API 연동 계약

## 기본 정보

| 항목 | 값 |
|------|----|
| Base URL | `https://api.dal.wiki` |
| 프로토콜 | REST, HTTPS |
| 응답 형식 | JSON |
| 인증 | `Authorization: Bearer {DALWIKI_API_KEY}` (선택적) |
| 타임아웃 | 요청당 15–30초 |

---

## 엔드포인트 레퍼런스

### GET /events

주어진 토픽의 이벤트 목록을 조회한다.

**Query Parameters**

| 파라미터 | 타입 | 설명 |
|---------|------|------|
| `topic_id` | UUID string | 조회할 캘린더 토픽 |
| `from` | integer (ms) | 시작 시각 (Unix timestamp, 밀리초) |
| `to` | integer (ms) | 종료 시각 (Unix timestamp, 밀리초) |

**Response**: 200 OK, 이벤트 객체 배열

---

### POST /events

새 이벤트를 생성한다.

**Request Body**: 이벤트 페이로드 ([페이로드 스키마](#이벤트-페이로드-스키마) 참조)

**Response**

```json
{ "id": "event-uuid" }
```

또는

```json
{ "eventId": "event-uuid" }
```

> 클라이언트는 두 키 모두를 시도해야 한다: `data.get("id") or data.get("eventId")`

**재시도 대상 상태 코드**: 429, 502, 503, 504

---

### PATCH /events/{eventId}

기존 이벤트를 전체 교체(full replacement)한다.

> **CRITICAL**: PATCH는 부분 업데이트가 아닌 전체 교체다. 원래 설정된 필드라도 생략하면 해당 필드가 지워진다. 모든 필드를 항상 포함해야 한다.

**Request Body**: 이벤트 페이로드 (전체 필드 포함)

**Response**: 200 또는 204 (빈 바디 또는 `{}`)

**재시도 대상 상태 코드**: 429, 502, 503, 504

---

### DELETE /events/{eventId}

이벤트를 삭제한다.

**Response**: 204 No Content

**재시도**: 502 발생 시만 재시도 (1.5초 × 시도 횟수 대기)

---

## 이벤트 페이로드 스키마

### 필수 필드

| 필드 | 타입 | 설명 |
|------|------|------|
| `topicId` | string (UUID) | 대상 캘린더 토픽 ID |
| `summary` | string | 이벤트 제목 (최대 100자; 초과 시 97자 + "...") |
| `start` | string (ISO 8601) | 시작 시각 (예: `"2026-05-18T00:00:00+09:00"`) |
| `end` | string (ISO 8601) | 종료 시각 |
| `allDay` | boolean | 종일 이벤트 여부 |
| `startTimezone` | string | 항상 `"Asia/Seoul"` |
| `endTimezone` | string | 항상 `"Asia/Seoul"` |

### 선택 필드

| 필드 | 타입 | 규칙 |
|------|------|------|
| `description` | string (HTML) | HTML 포맷 사용; 비어 있으면 생략 |
| `link` | string (URL) | None 또는 빈 문자열이면 **키 자체를 생략** |
| `location` | string | None 또는 빈 문자열이면 **키 자체를 생략** |

### 페이로드 예시

```json
{
  "topicId": "3606a7ac-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "summary": "서울 국제 마라톤 2026",
  "start": "2026-03-15T09:00:00+09:00",
  "end": "2026-03-15T18:00:00+09:00",
  "allDay": false,
  "startTimezone": "Asia/Seoul",
  "endTimezone": "Asia/Seoul",
  "description": "<p class=\"editor-paragraph\">42.195km 풀코스</p>",
  "link": "https://example.com/race"
}
```

---

## allDay 이벤트 규칙

- `allDay: true`일 때 `start` = 해당일 `00:00:00+09:00`, `end` = 해당일 `23:59:59+09:00`
- `start == end`이면 달력 UI에 이벤트가 표시되지 않으므로 반드시 end에 `23:59:59`를 설정
- 기간이 24시간 이상이거나, 시각 정보가 불명확한 경우 `allDay: true` 사용

---

## description HTML 포맷

```html
<!-- 기본 단락 -->
<p class="editor-paragraph">텍스트 내용</p>

<!-- 빈 줄 (줄바꿈) -->
<p class="editor-paragraph"><br /></p>

<!-- 링크 -->
<a href="https://..." class="editor-link" target="_blank" rel="noopener noreferrer">
  <span>https://...</span>
</a>

<!-- 소제목 -->
<h3 class="editor-heading-h3">소제목</h3>
```

> 사용자 입력값은 반드시 `html.escape()`로 이스케이프한다. URL은 `html.escape(url, quote=True)`.

---

## 재시도 정책

### 표준 재시도 (POST / PATCH)

```
시도 1 실패 → 2초 대기 → 시도 2
시도 2 실패 → 4초 대기 → 시도 3
시도 3 실패 → 오류 기록, 해당 이벤트 건너뜀
```

- 재시도 대상: 429, 502, 503, 504
- 4xx (429 제외): 즉시 실패, 재시도 없음
- 그 외 5xx: 경고 기록 후 재시도

### DELETE 재시도

```
시도 1 실패 (502) → 1.5초 대기 → 시도 2
시도 2 실패 (502) → 3.0초 대기 → 시도 3
기타 오류: 즉시 실패
```

---

## Topics 조회

```
GET /topics?name={토픽이름}
```

- 토픽 ID는 환경변수에서 직접 설정하는 것이 기본 패턴
- 이름 조회는 초기 설정 시 사용; 봇 실행 중에는 캐시된 ID 사용

---

## 외부 소스별 연동 정보

| 봇 | 소스 | API 유형 | 주의사항 |
|----|------|---------|----------|
| runbot | gorunning.kr | REST | |
| runbot | marathongo.co.kr | REST | |
| runbot | roadrun.co.kr | HTML 스크래핑 | |
| animebot | graphql.anilist.co | GraphQL | |
| KBObot | koreabaseball.com | 비공개 SOAP-like | User-Agent 필수 |
| chzzkvodbot | api.chzzk.naver.com | REST | OAuth 불필요 |
| laftelbot | api.laftel.net | REST | |
| figurebot | 6개 제조사 사이트 | HTML 스크래핑 | 사이트별 파서 |
| ewcbot | liquipedia.net | MediaWiki API | 30초/요청 엄격 제한 |
| ewcbot | start.gg | GraphQL | |
| worldcupbot | api.football-data.org | REST | API 키 필요 |
| koreafootballbot | api.football-data.org + kfa.or.kr | REST + 스크래핑 | |
| festivalbot | api.visitkorea.or.kr | REST | TourAPI 키 필요 |
| convinibot | 보도자료 HTML | 스크래핑 + Claude API | |
| megasale_bot | 쇼핑몰 8개 사이트 | HTML 스크래핑 + Claude API | |
