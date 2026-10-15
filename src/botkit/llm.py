"""llm_json — Claude 에게 JSON 을 받아오는 공용 헬퍼 (dalbots-bmi.20).

    from botkit.llm import llm_json, HAIKU, SONNET, LLMUnavailable, LLMParseError

    try:
        data = llm_json("…JSON 으로만 답해라…", model=HAIKU, max_tokens=800)
    except LLMUnavailable:
        alert("LLM 불가(크레딧/인증/과부하) — 커서 전진 중단");  raise
    except LLMParseError:
        ...  # 이 메시지만 실패. 재시도 큐로.

규칙:
  - 모델 ID 는 여기 상수만 쓴다: HAIKU="claude-haiku-5-5", SONNET="claude-sonnet-5-5".
  - 응답 파싱: 코드펜스(```json … ```) 제거 → 첫 `{`/`[` 부터 json.JSONDecoder.raw_decode
    (뒤에 잡설이 붙어도 'Extra data' 로 안 죽음. 탐욕 regex `\\{.*\\}` 금지).
  - 429/529(과부하)/5xx → 지수 백오프 재시도(기본 4회). 소진 시 LLMUnavailable.
  - 401/403, 크레딧 소진(400 "credit balance is too low") → 즉시 LLMUnavailable.
    호출자는 **커서·state 를 전진시키지 말고** alert 후 다음 run 에 재시도한다.
  - 이미지: bytes/경로/URL dict 모두 허용. 긴 변 7,900px 초과면 PIL 로 축소(Anthropic 상한 8,000),
    media_type 은 매직바이트로 감지.
  - `cache_system=True` 면 system 프롬프트에 cache_control(ephemeral) 부여(반복 호출 비용 절감).
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional, Sequence

logger = logging.getLogger("botkit.llm")

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
DEFAULT_MODEL = HAIKU

MAX_IMAGE_EDGE = 7900
_RETRY_STATUSES = {429, 500, 502, 503, 529}
_UNAVAILABLE_STATUSES = {401, 403}
_CREDIT_PATTERNS = ("credit balance", "credit_balance", "insufficient credit", "billing", "quota")


class LLMError(Exception):
    """botkit.llm 공통 베이스."""


class LLMUnavailable(LLMError):
    """LLM 을 지금 쓸 수 없다(인증/크레딧/과부하 재시도 소진). 커서 전진 금지, 다음 run 재시도."""

    def __init__(self, reason: str, status: Optional[int] = None):
        self.reason = reason
        self.status = status
        super().__init__(f"LLM unavailable ({status or '-'}): {reason}")


class LLMParseError(LLMError):
    """응답에서 JSON 을 못 뽑았다. 이 입력 한 건의 실패(재시도 큐 대상)."""

    def __init__(self, text: str, detail: str = ""):
        self.text = text
        super().__init__(f"JSON 파싱 실패: {detail} | {text[:200]!r}")


# ---------------------------------------------------------------------------
# JSON 추출
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?\s*```", re.DOTALL)


def strip_code_fences(text: str) -> str:
    """```json … ``` 펜스가 있으면 첫 펜스 내부만, 없으면 원문."""
    m = _FENCE_RE.search(text)
    return m.group(1) if m else text


def extract_json(text: str) -> Any:
    """모델 응답 텍스트에서 첫 JSON 값(object/array)을 뽑는다.

    1) 코드펜스 제거 2) 첫 `{` 또는 `[` 위치부터 raw_decode 3) 실패하면 다음 후보 위치로 진행.
    앞뒤 잡설·'Extra data' 에 강건. 없으면 LLMParseError.
    """
    if text is None:
        raise LLMParseError("", "빈 응답")
    body = strip_code_fences(text).strip()
    if not body:
        raise LLMParseError(text, "빈 응답")
    dec = json.JSONDecoder()
    starts = [i for i, ch in enumerate(body) if ch in "{["]
    last_err = ""
    for i in starts:
        try:
            value, _end = dec.raw_decode(body, i)
            return value
        except json.JSONDecodeError as e:
            last_err = str(e)
            continue
    raise LLMParseError(text, last_err or "JSON 시작 문자 없음")


# ---------------------------------------------------------------------------
# 이미지 전처리
# ---------------------------------------------------------------------------
def detect_media_type(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"  # 모르면 jpeg 로 보냄(Anthropic 이 실제 바이트로 판정)


def _resize_if_needed(data: bytes, max_edge: int = MAX_IMAGE_EDGE) -> tuple[bytes, Optional[str]]:
    """긴 변이 max_edge 초과면 축소. PIL 없으면 원본 그대로. (bytes, media_type|None)"""
    try:
        from PIL import Image
    except Exception:
        return data, None
    try:
        im = Image.open(io.BytesIO(data))
        w, h = im.size
        if max(w, h) <= max_edge:
            return data, None
        scale = max_edge / max(w, h)
        im = im.convert("RGB").resize((max(1, int(w * scale)), max(1, int(h * scale))))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=88)
        logger.info(f"이미지 축소 {w}x{h} → {im.size[0]}x{im.size[1]}")
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        logger.warning(f"이미지 축소 실패(원본 전송): {e}")
        return data, None


def image_block(image, *, max_edge: int = MAX_IMAGE_EDGE) -> dict:
    """bytes | 파일 경로(str/Path) | 이미 만들어진 content block(dict) → Anthropic image block."""
    if isinstance(image, dict):
        if image.get("type") == "image":
            return image
        if "url" in image:
            return {"type": "image", "source": {"type": "url", "url": image["url"]}}
        raise TypeError(f"알 수 없는 이미지 dict: {list(image)}")
    if isinstance(image, (str, Path)):
        s = str(image)
        if s.startswith(("http://", "https://")):
            return {"type": "image", "source": {"type": "url", "url": s}}
        data = Path(s).read_bytes()
    elif isinstance(image, (bytes, bytearray)):
        data = bytes(image)
    else:
        raise TypeError(f"이미지 타입 불가: {type(image)}")
    data, forced_type = _resize_if_needed(data, max_edge)
    media_type = forced_type or detect_media_type(data)
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": base64.b64encode(data).decode("ascii")},
    }


# ---------------------------------------------------------------------------
# 호출
# ---------------------------------------------------------------------------
_client_singleton = None


def _client(api_key: Optional[str] = None):
    global _client_singleton
    if api_key:
        import anthropic
        return anthropic.Anthropic(api_key=api_key)
    if _client_singleton is None:
        import anthropic
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise LLMUnavailable("ANTHROPIC_API_KEY 미설정", 401)
        _client_singleton = anthropic.Anthropic(api_key=key)
    return _client_singleton


def _status_of(exc: Exception) -> Optional[int]:
    sc = getattr(exc, "status_code", None)
    if isinstance(sc, int):
        return sc
    resp = getattr(exc, "response", None)
    sc = getattr(resp, "status_code", None)
    return sc if isinstance(sc, int) else None


def _is_credit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(p in msg for p in _CREDIT_PATTERNS)


def _text_of(resp) -> str:
    parts = []
    for block in getattr(resp, "content", None) or []:
        t = getattr(block, "text", None)
        if t is None and isinstance(block, dict):
            t = block.get("text")
        if t:
            parts.append(t)
    return "".join(parts)


def llm_call(
    prompt: str | Sequence[dict],
    *,
    system: Optional[str] = None,
    images: Optional[Sequence] = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1024,
    temperature: Optional[float] = None,
    retries: int = 4,
    backoff_base: float = 2.0,
    cache_system: bool = False,
    client=None,
    api_key: Optional[str] = None,
    sleep=time.sleep,
) -> str:
    """Claude 호출 → 응답 텍스트. 429/529/5xx 지수 백오프, 401/403/크레딧 → LLMUnavailable."""
    if isinstance(prompt, str):
        content: list[dict] = []
        for im in images or []:
            content.append(image_block(im))
        content.append({"type": "text", "text": prompt})
        messages = [{"role": "user", "content": content if len(content) > 1 else prompt}]
    else:
        messages = list(prompt)

    kwargs: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system:
        if cache_system:
            kwargs["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        else:
            kwargs["system"] = system
    if temperature is not None:
        kwargs["temperature"] = temperature

    cl = client or _client(api_key)
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            resp = cl.messages.create(**kwargs)
            return _text_of(resp)
        except LLMError:
            raise
        except Exception as e:  # anthropic.APIStatusError 계열 + 네트워크
            sc = _status_of(e)
            last_exc = e
            if sc in _UNAVAILABLE_STATUSES:
                raise LLMUnavailable(f"인증/권한 오류: {e}", sc) from e
            if sc == 400 and _is_credit_error(e):
                raise LLMUnavailable(f"크레딧 소진: {e}", sc) from e
            if sc in _RETRY_STATUSES or sc is None and _looks_transient(e):
                wait = backoff_base ** (attempt - 1)
                logger.warning(f"LLM {sc or 'net'} (attempt {attempt}/{retries}) → {wait:.0f}s 대기: {e}")
                if attempt < retries:
                    sleep(wait)
                continue
            # 그 외 4xx(프롬프트 문제 등)는 재시도 의미 없음 → 그대로 올림
            raise
    raise LLMUnavailable(f"재시도 {retries}회 소진: {last_exc}", _status_of(last_exc) if last_exc else None) \
        from last_exc


def _looks_transient(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return any(k in name for k in ("connection", "timeout", "overloaded", "ratelimit", "internalserver"))


def llm_json(
    prompt: str | Sequence[dict],
    *,
    system: Optional[str] = None,
    images: Optional[Sequence] = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1024,
    temperature: Optional[float] = None,
    retries: int = 4,
    cache_system: bool = False,
    client=None,
    api_key: Optional[str] = None,
    sleep=time.sleep,
) -> Any:
    """Claude 에게 JSON 을 받아 파싱해 돌려준다(dict/list).

    Raises:
        LLMUnavailable: 인증/크레딧/과부하 재시도 소진 — 커서 전진 금지.
        LLMParseError: 응답은 왔지만 JSON 이 아님 — 이 입력만 실패.
    """
    text = llm_call(
        prompt, system=system, images=images, model=model, max_tokens=max_tokens,
        temperature=temperature, retries=retries, cache_system=cache_system,
        client=client, api_key=api_key, sleep=sleep,
    )
    return extract_json(text)
