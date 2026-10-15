"""run(bot_name, main_fn) — 봇 실행 래퍼 + [HEALTH-ALERT] 규약.

    from botkit import run, alert

    def main(ctx):
        items = fetch()
        if not items:
            alert("소스 0건")          # → stdout 에 '[HEALTH-ALERT] <bot>: 소스 0건'
            return
        ...

    if __name__ == "__main__":
        run("kbobot", main)

제공:
  - 로깅: 콘솔(stdout, utf-8) + RotatingFileHandler(`<log_dir>/<bot_name>.log`, 5MB × 3)
  - `--dry-run` 표준 처리: argv 에서 떼어내고 os.environ["DRY_RUN"]="true" (dalwiki_client 가 매 호출 읽음)
  - 처리되지 않은 예외 → 로그 + `[HEALTH-ALERT] <bot>: 예외 종료 …` + exit 1
  - `alert(msg)`: `[HEALTH-ALERT] <bot>: msg` 를 stdout 에 flush 출력 + logger.error (+ Discord webhook 있으면 전송)
  - main_fn 이 int 를 돌려주면 그걸 exit code 로 쓴다

규약(health_check 가 보는 것은 `[HEALTH-ALERT]` 한 줄):
  주 소스 전부 실패 / 소스 0건 / dal.wiki 장애(ListFailed, SyncAborted) / LLM 불가(LLMUnavailable)
  → alert() 호출. 그리고 그 run 에서는 커서·state 를 전진시키지 않는다.

track.py(대시보드)는 그대로 bat 에서 호출한다 — 이 래퍼가 대체하지 않는다(사용자 결정 §5-8).
"""
from __future__ import annotations

import inspect
import logging
import os
import sys
import traceback
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Optional

ALERT_TAG = "[HEALTH-ALERT]"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

_current_bot: Optional[str] = None
_alerts: list[str] = []
logger = logging.getLogger("botkit.run")


@dataclass
class RunContext:
    bot_name: str
    dry_run: bool
    args: list[str]
    log: logging.Logger
    log_dir: Path
    alerts: list[str] = field(default_factory=list)

    def alert(self, msg: str) -> None:
        alert(msg, bot=self.bot_name)
        self.alerts.append(msg)


def alert(msg: str, *, bot: Optional[str] = None, discord: bool = True) -> str:
    """[HEALTH-ALERT] 한 줄을 stdout(flush) + logger.error 로 남긴다. 예외를 던지지 않는다.

    health_check 는 lastrun .out 에서 이 태그만 본다. 메시지는 한 줄(개행 제거)로.
    """
    bot = bot or _current_bot or "bot"
    one_line = " ".join(str(msg).split())
    line = f"{ALERT_TAG} {bot}: {one_line}"
    _alerts.append(line)
    try:
        print(line, flush=True)
    except Exception:
        pass
    logger.error(line)
    if discord:
        try:
            from ingest_core import discord as _dc
            if _dc.is_enabled():
                _dc.alert(one_line, title=f"{ALERT_TAG} {bot}")
        except Exception:
            pass
    return line


def alerts() -> list[str]:
    """이 프로세스에서 지금까지 발생한 alert 라인들."""
    return list(_alerts)


def _setup_logging(bot_name: str, log_dir: Path, level: str) -> tuple[logging.Logger, list[logging.Handler]]:
    """루트 로거에 콘솔 + 회전 파일 핸들러를 붙인다. 이 호출이 새로 붙인 핸들러 목록도 돌려준다(run 종료 시 제거)."""
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    added: list[logging.Handler] = []

    # 콘솔(stdout) — Windows cp949 콘솔에서 이모지/한글 깨짐 방지
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    has_console = any(type(h) is logging.StreamHandler for h in root.handlers)
    if not has_console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        root.addHandler(ch)
        added.append(ch)

    log_path = log_dir / f"{bot_name}.log"
    has_file = any(isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == str(log_path)
                   for h in root.handlers)
    if not has_file:
        fh = RotatingFileHandler(log_path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT,
                                 encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
        added.append(fh)
    return logging.getLogger(bot_name), added


def _strip_flag(argv: list[str], flag: str) -> tuple[bool, list[str]]:
    present = flag in argv
    return present, [a for a in argv if a != flag]


def run(
    bot_name: str,
    main_fn: Callable,
    *,
    argv: Optional[list[str]] = None,
    log_dir: Optional[str | Path] = None,
    exit_on_alert: bool = False,
    _exit: bool = True,
) -> int:
    """봇 main 을 표준 래퍼 안에서 실행한다.

    Args:
        bot_name: 로그 파일명·alert prefix. 대시보드 라벨과 맞추는 것을 권장.
        main_fn: `main(ctx: RunContext)` 또는 인자 없는 `main()`. int 반환 시 exit code.
        argv: 기본 sys.argv[1:]. `--dry-run` 은 여기서 떼어낸다.
        log_dir: 기본 `LOG_DIR` 환경변수 → 없으면 실행 스크립트 폴더의 `logs/`.
        exit_on_alert: True 면 alert 가 하나라도 있으면 exit 1(기본 False — alert 는 "처리된 저하",
            예외만 exit 1. 티어 bat 의 RETRY 는 Traceback 유무로 판단).
        _exit: False 면 sys.exit 하지 않고 exit code 를 반환(테스트용).
    """
    global _current_bot
    _current_bot = bot_name
    argv = list(sys.argv[1:] if argv is None else argv)
    dry, rest = _strip_flag(argv, "--dry-run")
    if dry:
        os.environ["DRY_RUN"] = "true"
    dry = dry or os.environ.get("DRY_RUN", "false").lower() == "true"

    if log_dir is None:
        env_dir = os.environ.get("LOG_DIR")
        if env_dir:
            log_dir = Path(env_dir)
        else:
            script = Path(sys.argv[0]).resolve().parent if sys.argv and sys.argv[0] else Path.cwd()
            log_dir = script / "logs"
    log_dir = Path(log_dir)
    log, added_handlers = _setup_logging(bot_name, log_dir, os.environ.get("LOG_LEVEL", "INFO"))
    ctx = RunContext(bot_name=bot_name, dry_run=dry, args=rest, log=log, log_dir=log_dir)

    log.info(f"▶ {bot_name} start" + (" [DRY-RUN]" if dry else "") + (f" args={rest}" if rest else ""))
    code = 0
    try:
        nparams = len([
            p for p in inspect.signature(main_fn).parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ])
        result = main_fn(ctx) if nparams >= 1 else main_fn()
        if isinstance(result, bool):
            code = 0 if result else 1
        elif isinstance(result, int):
            code = result
    except KeyboardInterrupt:
        log.warning("KeyboardInterrupt")
        code = 130
    except SystemExit as e:
        code = int(e.code or 0) if isinstance(e.code, (int, type(None))) else 1
        if code != 0:
            alert(f"SystemExit({e.code})", bot=bot_name)
    except Exception as e:
        # 스택은 로그 파일에, stdout 엔 Traceback 도 남겨 티어 bat RETRY 조건(마지막 20줄 Traceback)에 걸리게
        log.exception(f"{bot_name} 처리되지 않은 예외")
        traceback.print_exc()
        alert(f"예외 종료 — {type(e).__name__}: {' '.join(str(e).split())[:300]}", bot=bot_name)
        code = 1

    if code == 0 and exit_on_alert and _alerts:
        code = 1
    log.info(f"■ {bot_name} end exit={code}" + (f" alerts={len(_alerts)}" if _alerts else ""))
    root = logging.getLogger()
    for h in added_handlers:
        try:
            h.flush()
            h.close()
        except Exception:
            pass
        root.removeHandler(h)
    if _exit:
        logging.shutdown()
        sys.exit(code)
    return code
