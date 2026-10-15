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
                return _Resp(item)

        self.messages = _Messages()


def test_llm_json_ok_passes_model_and_prompt():
    cl = FakeClient(['```json\n{"ok": true}\n```'])
    out = llm_json("질문", model=SONNET, max_tokens=99, client=cl, system="sys", cache_system=True)
    assert out == {"ok": True}
    kw = cl.calls[0]
    assert kw["model"] == SONNET and kw["max_tokens"] == 99
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
