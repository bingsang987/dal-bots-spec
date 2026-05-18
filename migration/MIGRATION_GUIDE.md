# API v2 마이그레이션 가이드

> 프로덕션 API 전환 시 이 문서를 참고하여 실행한다.

## 준비물 확인

- [ ] `dalwiki_client_v2.py` — 이 폴더에 있음
- [ ] 새 `DALWIKI_API_BASE` 값 (운영자에게 확인)
- [ ] 각 봇의 `.env` 업데이트 권한

---

## 실행 순서

### 1단계 — 환경변수 업데이트 (각 봇 .env)

```
# 기존
DALWIKI_API_BASE=https://api.dal.wiki

# 변경 후 (실제 값은 운영자 확인)
DALWIKI_API_BASE=https://api.dal.wiki  ← 새 프로덕션 URL로 변경
```

### 2단계 — dalwiki_client.py 교체

Claude에게 다음과 같이 요청:

```
dal-bots-spec/migration/dalwiki_client_v2.py 를 레퍼런스로,
모든 봇 디렉토리의 dalwiki.py / dalwiki_client.py 를 새 API 기준으로 교체해줘.
봇 목록: C:\Users\gamer\Documents\ 아래 27개 봇
병렬 서브에이전트로 처리해줘.
```

### 3단계 — 검증

각 봇을 dry-run으로 실행하여 새 API 호출이 정상인지 확인:

```powershell
cd C:\Users\gamer\Documents\runbot
python main.py --dry-run
```

---

## 변경 매핑 요약

| 구 코드 패턴 | 신 코드 패턴 |
|-------------|-------------|
| `session.get(f"{BASE}/events", params={"topic_id":...,"from":ms})` | `_post("/events/list", {"topicId":..., "from": iso_str})` |
| `session.post(f"{BASE}/events", json=payload)` | `_post("/events/create", payload)` |
| `session.patch(f"{BASE}/events/{id}", json=payload)` | `_post("/events/update", {"id": id, ...변경필드만})` |
| `session.delete(f"{BASE}/events/{id}")` | `_post("/events/delete", {"id": id})` |
| `session.get(f"{BASE}/topics?name={name}")` | `_post("/legacy/resolveTopicByName", {"topicName": name})` |
| `session.get(f"{BASE}/topics/{name}")` | `_post("/legacy/resolveTopicByName", {"topicName": name})` |
| `session.post(f"{BASE}/topics", json={name, desc})` | `_post("/topics/create", {name, description})` |

## 주의사항

1. **`startTimezone` 기본값 변경** — 새 API 서버 기본값이 `"UTC"`. `dalwiki_client_v2.py`는 기본값을 `"Asia/Seoul"`로 설정해두었으나, 기존 봇에서 timezone을 명시 안 하는 경우 확인 필요.

2. **`from`/`to` 타입 변경** — `dalwiki_client_v2.py`의 `list_events()`는 기존 봇과의 호환성을 위해 ms 정수를 받아 내부에서 ISO 변환. 봇 main 코드 수정 불필요.

3. **`/events/update`는 이제 partial update** — 변경할 필드만 보내면 됨. 전체 필드 전송도 여전히 동작.

4. **응답 구조 미정** — 신 API의 응답 스키마가 OpenAPI 문서에 정의되어 있지 않음(`anyOf: [{}]`). 실제 응답을 보고 `_extract_id()` 조정 필요할 수 있음.
