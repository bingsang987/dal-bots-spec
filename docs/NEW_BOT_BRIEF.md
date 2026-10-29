# 신규 캘린더/봇 실사·구축 브리프 (2026-10-29)

핸드오프 문서(`C:\Users\gamer\Downloads\handoffs\0N_*.md`)는 개념·범위·결정 질문만 담는다. 실행 세션은 아래 순서로 간다. **규칙 원문은 `W2_REBUILD_BRIEF.md`(요약·본문·카테고리·시각·금지), 패키지는 dal-bots-spec `botkit`(SyncEngine/run/llm/discord/vod) — 기존 봇 중 가장 작은 예시 `C:\Users\gamer\Documents\PLBOT\bot.py`, 한 이벤트를 토픽 2개에 넣는 예시 `gachabot/bot.py`(게임 토픽 + 통합 토픽에 같은 key로 엔진 2개), 다중 토픽 라우팅 예시 `lckteambot`.**

## 1. 실사(fact-finding) 먼저 — 쓰기 없이
- 핸드오프의 "실사 (Claude Code)" 항목 전부를 실제 소스·코드·데이터로 확인하고 수치로 답한다(건수, 구조, 키 필요 여부, 이용 조건).
- 외부 API 키가 필요하면 먼저 기존 봇 `.env`에서 찾는다(`grep -l NAVER_CLIENT_ID C:\Users\gamer\Documents\*\.env`). 없으면 "사용자 발급 필요"로 보고하고, 키 없이 가능한 만큼만 진행한다. 키 값은 절대 출력·커밋하지 않는다.
- 외부 사이트는 robots/이용약관을 존중하고 차단 우회를 하지 않는다. 레이트리밋 준수.

## 2. 결정 질문은 기본안으로 진행하되, 아래는 반드시 멈추고 관제탑(pointfarm 세션)에 묻는다
- **dal.wiki 토픽(캘린더) 생성** — 세션이 만들지 않는다. 만들 토픽 목록(이름·설명 한 줄·썸네일 후보·카테고리 안)을 보고서에 적고 끝낸다. 관제탑이 사용자 확인 후 생성해 topic id를 준다.
- 공개 데이터에 영향이 큰 선택(수록 범위 축소/확대, 기존 토픽 이벤트 이동).

## 3. 구축(토픽 id가 없어도 여기까지는 간다)
- 새 봇 폴더 `C:\Users\gamer\Documents\<봇이름>`(기존 봇 수정이면 그 레포). 구조: `bot.py`(botkit.run + SyncEngine), `sources*.py`, `config.py`(토픽/카테고리 id는 .env 또는 config, 비면 dry-run만), `state/`(sqlite), `logs/`, `run.bat`(ASCII only, `python C:\Users\gamer\bot_dashboard\track.py <label> 2>nul` 한 줄 + `python bot.py` + `pause`), `README.md`, `.gitignore`(.env, state, logs, *.db).
- Event.key = 소스의 안정 식별자(날짜·제목 아님). absent=delete + source_window + source_complete, 조각 실패 시 complete=False. 카테고리 id 항상 명시. 본문에 출처 표기 금지(link 칸), 링크는 target=_blank rel=noopener noreferrer. 종일 = 00:00~23:59:59 KST. summary 2~100자, '[카테고리]' 프리픽스 금지.
- 실패 규약: 소스 0건/전부 실패/dal.wiki 장애 → `alert()`([HEALTH-ALERT]) + 쓰기 보류.
- 토픽 id 없는 동안: `--dry-run`으로 "등록될 이벤트 목록(N건, 샘플 10)"을 뽑아 보고서에. 기존 토픽에 라우팅을 추가하는 경우(애니·선수)는 **기존 토픽 쪽 동작이 1비트도 변하지 않음**을 2회째 unchanged로 증명.
- git init + private 레포(`gh repo create bingsang987/<봇> --private --source=. --push`), .env 제외 확인.

## 4. 보고서
`C:\Users\gamer\Documents\<봇>\HANDOFF_REPORT.md`에: 실사 답(수치), 채택한 기본안/바꾼 점, 토픽 생성 요청 목록, 사용자 결정이 필요한 질문(최대 4개, 각각 기본안 명시), 다음 단계. 마지막 보고 메시지에 이 파일 경로와 요약.

## 5. 하지 말 것
dal.wiki 토픽/카테고리 생성, 기존 토픽 이벤트 삭제·이동, 티어 bat·health_check 수정(관제탑이 함), 패키지 시그니처 변경, 다른 봇 폴더 수정(명시된 것 외), 하위 에이전트 대량 병렬(세션 하나가 봇 하나를 직접 한다).
