"""botkit.run — --dry-run 표준 처리, 예외 → [HEALTH-ALERT] + exit 1, alert() 포맷, 로그 회전 파일."""
from __future__ import annotations

import logging
import os

import importlib

runmod = importlib.import_module("botkit.run")
from botkit.run import ALERT_TAG, alert, run


def test_dry_run_flag_sets_env_and_ctx(tmp_path, monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)
    got = {}

    def main(ctx):
        got["dry"] = ctx.dry_run
        got["args"] = ctx.args
        got["env"] = os.environ.get("DRY_RUN")

    code = run("tbot", main, argv=["--dry-run", "--since", "3"], log_dir=tmp_path, _exit=False)
    assert code == 0
    assert got == {"dry": True, "args": ["--since", "3"], "env": "true"}
    assert (tmp_path / "tbot.log").exists()


def test_exception_emits_health_alert_and_exit_1(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)

    def main():
        raise RuntimeError("소스 폭발")

    code = run("tbot", main, argv=[], log_dir=tmp_path, _exit=False)
    out = capsys.readouterr().out
    assert code == 1
    assert f"{ALERT_TAG} tbot: 예외 종료 — RuntimeError: 소스 폭발" in out
    assert "Traceback" in out        # 티어 bat RETRY 조건


def test_return_code_passthrough_and_no_arg_main(tmp_path, monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)
    assert run("tbot", lambda: 3, argv=[], log_dir=tmp_path, _exit=False) == 3
    assert run("tbot", lambda: None, argv=[], log_dir=tmp_path, _exit=False) == 0


def test_alert_format_one_line(capsys, monkeypatch):
    monkeypatch.setattr(runmod, "_current_bot", "kbobot")
    line = alert("KBO API 0건\n이번 run 스킵", discord=False)
    assert line == f"{ALERT_TAG} kbobot: KBO API 0건 이번 run 스킵"
    assert line in capsys.readouterr().out


def test_exit_on_alert_option(tmp_path, monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setattr(runmod, "_alerts", [])

    def main(ctx):
        ctx.alert("소스 0건")

    assert run("tbot", main, argv=[], log_dir=tmp_path, _exit=False) == 0
    monkeypatch.setattr(runmod, "_alerts", [])
    assert run("tbot", main, argv=[], log_dir=tmp_path, exit_on_alert=True, _exit=False) == 1


def test_handlers_cleaned_up(tmp_path, monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)
    before = len(logging.getLogger().handlers)
    run("tbot", lambda: None, argv=[], log_dir=tmp_path, _exit=False)
    assert len(logging.getLogger().handlers) == before


# W8-0 .71 — alert 의 Discord 경로는 dalwiki_client.discord_notify 웹훅
def test_alert_sends_via_dalwiki_webhook(monkeypatch):
    from dalwiki_client import discord_notify
    sent = []

    class R:
        status_code = 204

    monkeypatch.setattr(runmod, "_current_bot", "kbobot")
    monkeypatch.setattr(discord_notify.requests, "post", lambda url, json, timeout: sent.append((url, json)) or R())
    alert("소스 0건", discord=True)
    assert sent == []                                           # 웹훅 미설정 → 무동작
    monkeypatch.setenv("DALWIKI_DISCORD_WEBHOOK_URL", "https://discord.test/hook")
    alert("소스 0건", discord=True)
    assert len(sent) == 1 and sent[0][0] == "https://discord.test/hook"
    emb = sent[0][1]["embeds"][0]
    assert emb["title"] == f"{ALERT_TAG} kbobot" and emb["description"] == "소스 0건"
