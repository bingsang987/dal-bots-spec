"""botkit.llm — JSON 추출, 백오프, LLMUnavailable 분기, 이미지 전처리."""
from __future__ import annotations

import base64
import io

import pytest

from botkit.llm import (
    HAIKU, SONNET, LLMParseError, LLMUnavailable, detect_media_type, extract_json,
    image_block, llm_json, strip_code_fences,
)


def test_model_constants():
    assert HAIKU == "claude-haiku-5-5" and SONNET == "claude-sonnet-5-5"


# ── extract_json ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("text, expected", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1, "b": [1,2]}\n```', {"a": 1, "b": [1, 2]}),
    ('```\n{"a": 1}\n```', {"a": 1}),
    ('다음은 결과입니다:\n{"title": "철권", "date": "2026-01-01"}\n도움이 되셨길!', {"title": "철권", "date": "2026-01-01"}),
    ('{"a": 1} 그리고 추가 설명 {"b": 2}', {"a": 1}),                 # Extra data 로 안 죽음
    ('[I need to filter…] 결과: [{"k": "v"}]', [{"k": "v"}]),       # 앞의 가짜 대괄호 건너뜀
    ('[{"k": 1}, {"k": 2}]', [{"k": 1}, {"k": 2}]),
    ('{"nested": {"x": [1, {"y": "z}"}]}}', {"nested": {"x": [1, {"y": "z}"}]}}),
])
def test_extract_json_cases(text, expected):
    assert extract_json(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "JSON 없음", "{broken", None])
def test_extract_json_failures(text):
    with pytest.raises(LLMParseError):
        extract_json(text)


def test_strip_code_fences_keeps_plain():
    assert strip_code_fences("plain") == "plain"
    assert strip_code_fences("```json\n{}\n```") == "{}"


# ── llm_json with fake client ─────────────────────────────────────────────
class _Err(Exception):
    def __init__(self, status, msg="err"):
        super().__init__(msg)
        self.status_code = status


class _Block:
    def __init__(self, text):
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class _TBlock:
    """type 이 있는 SDK 스타일 블록."""
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class _FullResp:
    def __init__(self, blocks, stop_reason="end_turn"):
        self.content = blocks
        self.stop_reason = stop_reason


class FakeClient:
    def __init__(self, script):
        """script: 순서대로 반환할 항목. str → 응답 텍스트, Exception → raise."""
        self.script = list(script)
        self.calls = []

        outer = self

        class _Messages:
            def create(self, **kw):
                outer.calls.append(kw)
                item = outer.script.pop(0)
                if isinstance(item, Exception):
                    raise item
                if isinstance(item, _FullResp):
                    return item
                return _Resp(item)

        self.messages = _Messages()


def test_llm_json_ok_passes_model_and_prompt():
    cl = FakeClient(['```json\n{"ok": true}\n```'])
    out = llm_json("질문", model=SONNET, max_tokens=2000, client=cl, system="sys", cache_system=True)
    assert out == {"ok": True}
    kw = cl.calls[0]
    assert kw["model"] == SONNET and kw["max_tokens"] == 2000
    assert kw["messages"][0]["content"] == "질문"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_llm_json_backs_off_on_429_and_529_then_succeeds():
    sleeps = []
    cl = FakeClient([_Err(429), _Err(529), '{"a": 1}'])
    out = llm_json("q", client=cl, sleep=sleeps.append)
    assert out == {"a": 1}
    assert sleeps == [1.0, 2.0]          # 지수 백오프 2^(n-1)


def test_llm_json_exhausted_retries_raise_unavailable():
    cl = FakeClient([_Err(529)] * 3)
    with pytest.raises(LLMUnavailable) as ei:
        llm_json("q", client=cl, retries=3, sleep=lambda s: None)
    assert ei.value.status == 529 and not cl.script


def test_llm_json_401_is_unavailable_immediately():
    cl = FakeClient([_Err(401, "invalid x-api-key"), '{"never": 1}'])
    with pytest.raises(LLMUnavailable) as ei:
        llm_json("q", client=cl, sleep=lambda s: None)
    assert ei.value.status == 401 and len(cl.calls) == 1


def test_llm_json_credit_exhausted_is_unavailable():
    cl = FakeClient([_Err(400, "Your credit balance is too low to access the Anthropic API.")])
    with pytest.raises(LLMUnavailable) as ei:
        llm_json("q", client=cl)
    assert "크레딧" in str(ei.value)


def test_llm_json_other_400_propagates_raw():
    cl = FakeClient([_Err(400, "max_tokens too large")])
    with pytest.raises(_Err):
        llm_json("q", client=cl)


def test_llm_json_parse_error_is_not_unavailable():
    cl = FakeClient(["죄송합니다, JSON 을 만들 수 없어요."])
    with pytest.raises(LLMParseError):
        llm_json("q", client=cl)


# ── thinking 블록 / max_tokens 하한 (dalbots-bmi.48) ─────────────────────
def test_llm_json_ignores_thinking_blocks_and_uses_text_only():
    resp = _FullResp([
        _TBlock("thinking", thinking="", signature="sig"),
        _TBlock("redacted_thinking", data="xxx"),
        _TBlock("text", text='{"ok": 1}'),
    ])
    cl = FakeClient([resp])
    assert llm_json("q", model=HAIKU, client=cl) == {"ok": 1}


def test_llm_json_thinking_text_with_braces_is_not_parsed():
    # thinking 요약에 { 가 있어도 text 블록만 본다
    resp = _FullResp([
        _TBlock("thinking", thinking='후보 {"bad": 0}', signature="s"),
        _TBlock("text", text='[{"k": "v"}]'),
    ])
    assert llm_json("q", model=HAIKU, client=FakeClient([resp])) == [{"k": "v"}]


def test_llm_json_dict_blocks_with_type_are_filtered():
    resp = _FullResp([{"type": "thinking", "thinking": "{x}"}, {"type": "text", "text": '{"d": 2}'}])
    assert llm_json("q", model=HAIKU, client=FakeClient([resp])) == {"d": 2}


def test_llm_json_thinking_only_max_tokens_raises_with_reason(caplog):
    resp = _FullResp([_TBlock("thinking", thinking="", signature="s")], stop_reason="max_tokens")
    cl = FakeClient([resp])
    with caplog.at_level("WARNING", logger="botkit.llm"):
        with pytest.raises(LLMParseError) as ei:
            llm_json("q", model=HAIKU, max_tokens=120, client=cl)
    msg = str(ei.value)
    assert "max_tokens" in msg and "thinking" in msg and "1024" in msg
    assert ei.value.stop_reason == "max_tokens"
    assert any("빈 응답" in r.message and r.levelname == "WARNING" for r in caplog.records)
    assert len(cl.calls) == 1            # 파싱 실패는 재시도하지 않음


def test_llm_json_truncated_json_mentions_max_tokens():
    resp = _FullResp([_TBlock("thinking", thinking=""), _TBlock("text", text='{"a": [1, 2')],
                     stop_reason="max_tokens")
    with pytest.raises(LLMParseError) as ei:
        llm_json("q", model=HAIKU, client=FakeClient([resp]))
    assert "stop_reason=max_tokens" in str(ei.value)


def test_llm_json_empty_end_turn_is_plain_parse_error():
    resp = _FullResp([_TBlock("text", text="")], stop_reason="end_turn")
    with pytest.raises(LLMParseError) as ei:
        llm_json("q", model=HAIKU, client=FakeClient([resp]))
    assert "max_tokens" not in str(ei.value) and ei.value.stop_reason == "end_turn"


@pytest.mark.parametrize("model, asked, sent", [
    (HAIKU, 120, 1024),
    (SONNET, 99, 1024),
    (HAIKU, 1024, 1024),
    (HAIKU, 4000, 4000),
    ("claude-opus-5-5", 50, 1024),
    ("anthropic.claude-haiku-5-5", 50, 1024),
    ("claude-haiku-4-5", 120, 120),      # 4.x 는 thinking 기본 off → 그대로
])
def test_max_tokens_floor_for_thinking_models(model, asked, sent):
    cl = FakeClient(['{"ok": 1}'])
    llm_json("q", model=model, max_tokens=asked, client=cl)
    assert cl.calls[0]["max_tokens"] == sent


def test_llm_call_returns_text_only_and_warns_on_empty_max_tokens(caplog):
    from botkit.llm import llm_call
    ok = _FullResp([_TBlock("thinking", thinking="생각"), _TBlock("text", text="안녕")])
    assert llm_call("q", client=FakeClient([ok])) == "안녕"
    empty = _FullResp([_TBlock("thinking", thinking="")], stop_reason="max_tokens")
    with caplog.at_level("WARNING", logger="botkit.llm"):
        assert llm_call("q", client=FakeClient([empty])) == ""
    assert any("max_tokens" in r.message for r in caplog.records)


# ── 이미지 ───────────────────────────────────────────────────────────────
def _png(w, h) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def test_detect_media_type():
    assert detect_media_type(_png(2, 2)) == "image/png"
    assert detect_media_type(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert detect_media_type(b"GIF89a....") == "image/gif"
    assert detect_media_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"


def test_image_block_small_png_passthrough():
    data = _png(10, 10)
    blk = image_block(data)
    assert blk["source"]["media_type"] == "image/png"
    assert base64.b64decode(blk["source"]["data"]) == data


def test_image_block_resizes_long_edge_over_7900():
    from PIL import Image
    blk = image_block(_png(8200, 20))
    assert blk["source"]["media_type"] == "image/jpeg"
    im = Image.open(io.BytesIO(base64.b64decode(blk["source"]["data"])))
    assert max(im.size) <= 7900


def test_llm_json_with_images_builds_content_blocks():
    cl = FakeClient(['{"seen": 1}'])
    llm_json("뭐가 보여?", images=[_png(4, 4), {"url": "https://example.com/a.png"}], client=cl)
    content = cl.calls[0]["messages"][0]["content"]
    assert [c["type"] for c in content] == ["image", "image", "text"]
    assert content[1]["source"] == {"type": "url", "url": "https://example.com/a.png"}


# W8-0 .72 — thinking 기본 모델에 temperature → 무시 + 경고(400 크래시 방지)
def test_temperature_dropped_for_thinking_model(caplog):
    cl = FakeClient(['{"a": 1}', '{"a": 2}', '{"a": 3}'])
    with caplog.at_level("WARNING", logger="botkit.llm"):
        assert llm_json("q", model=SONNET, temperature=0.2, client=cl) == {"a": 1}
    assert "temperature" not in cl.calls[0]
    assert any("temperature=0.2 무시" in r.getMessage() for r in caplog.records)
    llm_json("q", model=SONNET, temperature=1, client=cl)                  # 1 은 허용
    assert cl.calls[1]["temperature"] == 1
    llm_json("q", model="claude-3-5-haiku-20241022", temperature=0.2, client=cl)   # 비-thinking 모델은 그대로
    assert cl.calls[2]["temperature"] == 0.2
