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


# W8-0 시크릿 마스킹(.117/.172/.175) — alert · run() 예외 · 로그 포맷터
def test_mask_secrets_patterns():
    from botkit.run import mask_secrets
    s = mask_secrets("GET http://x/api?serviceKey=AbC%2B1==&pageNo=1&key=G00gle token=t0k "
                     "api_key=k-1 apiKeyNm=nm secret=s3 sync fail: create 실패 key=k1 'X'")
    for raw in ("AbC%2B1", "G00gle", "t0k", "k-1", "=nm", "s3"):
        assert raw not in s
    assert "serviceKey=***&pageNo=1&key=***" in s
    assert "실패 key=k1" in s                                  # 봇 키 로그는 보존


def test_alert_masks_secrets(capsys, monkeypatch):
    monkeypatch.setattr(runmod, "_current_bot", "housingbot")
    line = alert("HTTPError for url: https://apis.data.go.kr/x?serviceKey=REALKEY123&numOfRows=10", discord=False)
    assert "REALKEY123" not in line and "serviceKey=***" in line
    assert "REALKEY123" not in capsys.readouterr().out


def test_run_exception_and_traceback_masked(tmp_path, capfd, monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)

    def main():
        raise RuntimeError("Max retries exceeded with url: /api?api_key=SUPERSECRET&q=1")

    code = run("tbot", main, argv=[], log_dir=tmp_path, _exit=False)
    out, err = capfd.readouterr()
    assert code == 1
    assert "SUPERSECRET" not in out and "SUPERSECRET" not in err
    assert f"{ALERT_TAG} tbot: 예외 종료 — RuntimeError:" in out and "api_key=***" in out
    assert "Traceback" in out + err
    assert "SUPERSECRET" not in (tmp_path / "tbot.log").read_text(encoding="utf-8")


def test_log_formatter_masks_secrets(tmp_path, monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)

    def main(ctx):
        ctx.log.warning("fetch 실패 https://x/y?token=TKN999&page=2 (key=bot-key-1)")

    run("tbot", main, argv=[], log_dir=tmp_path, _exit=False)
    text = (tmp_path / "tbot.log").read_text(encoding="utf-8")
    assert "TKN999" not in text and "token=***&page=2" in text and "key=bot-key-1" in text


def test_mask_secrets_covers_crtfc_and_auth_keys():
    from botkit.run import mask_secrets
    s = "https://opendart.fss.or.kr/api/list.json?crtfc_key=abc123&corp=1 authKey=zz access_key=q password=p1"
    out = mask_secrets(s)
    assert "abc123" not in out and "zz" not in out and "=q" not in out and "p1" not in out
    assert "crtfc_key=***" in out and "corp=1" in out
