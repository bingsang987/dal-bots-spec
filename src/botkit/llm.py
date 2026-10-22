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
  - thinking (dalbots-bmi.48): Haiku 5.5 / Sonnet 5.5 (및 5.x 세대 전부)는 `thinking` 파라미터를
    안 보내도 adaptive thinking 이 기본으로 켜지고, thinking 토큰도 `max_tokens` 에 포함된다.
    그래서 max_tokens 가 작으면 thinking 만 쓰다 stop_reason=max_tokens 로 끝나 text 블록이 없다.
      · 응답 텍스트는 content 중 type=="text" 블록만 이어붙인다(thinking/redacted_thinking/tool_use 등 무시).
      · 5.x thinking 모델이면 max_tokens 하한 MIN_THINKING_MAX_TOKENS(=1024) 적용 — 더 작게 줘도
        1024 로 올려 보낸다(Anthropic 마이그레이션 가이드의 short-output 예시 값. thinking 은 과금되지만
        출력이 짧으면 실제 사용 토큰은 작다). 하한 이상 값은 그대로.
      · 텍스트가 비었는데 stop_reason=max_tokens 면 경고 로그 + LLMParseError 메시지에 사유
        ("thinking 이 max_tokens 소진")를 적는다. JSON 이 잘려 깨진 경우도 stop_reason 을 덧붙인다.
  - thinking 이 max_tokens 를 소진하는 큰 배치(dalbots-bmi.214):
      · llm_json 은 stop_reason=max_tokens 로 잘리면(텍스트 없음 또는 최상위 JSON 미완결) max_tokens 를
        truncation_retry_max_tokens(기본 16000)로 올려 **1회 재시도**한다. 그래도 잘리면 LLMTruncated
        (LLMParseError 하위 — 기존 except LLMParseError 그대로 잡힘). None 이면 재시도 안 함.
        잘린 응답에서 뒤쪽 부분 객체만 파싱해 돌려주던 구 동작(배열이 잘리면 첫 원소만 반환)도 없앴다.
      · thinking 깊이 조절(opt-in): effort="low"|"medium"|"high"|"xhigh"|"max" → output_config.effort
        (Haiku 5.5 기본 medium). thinking=False → {"type":"disabled"} — Haiku 5.5·Opus 5 는 effort high
        이하에서만 받고(xhigh/max 와 같이 쓰면 400), Opus 5.5·Sonnet 5.5·Fable 은 400 이라 여기서 ValueError.
        thinking 예산(budget_tokens)은 5.x 전부 400 이라 제공하지 않는다.
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

# 5.x 세대(Haiku/Sonnet/Opus/Fable/Mythos 5.x)는 thinking 기본 on → max_tokens 하한 적용 대상.
_THINKING_DEFAULT_RE = re.compile(r"^(?:anthropic\.)?claude-(?:haiku|sonnet|opus|fable|mythos)-5(?:-|$)")
MIN_THINKING_MAX_TOKENS = 1024
# stop_reason=max_tokens 로 잘린 llm_json 응답을 1회 재시도할 때의 max_tokens(비스트리밍 HTTP 타임아웃 안전선).
TRUNCATION_RETRY_MAX_TOKENS = 16000
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# thinking={"type":"disabled"} 를 400 으로 거절하는 모델(effort 무관).
_NO_DISABLE_RE = re.compile(r"^(?:anthropic\.)?claude-(?:opus-5-5|sonnet-5-5|fable|mythos)(?:-|$)")

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
    """응답에서 JSON 을 못 뽑았다. 이 입력 한 건의 실패(재시도 큐 대상).

    stop_reason: 알 수 있으면 응답의 stop_reason("max_tokens" 면 thinking/출력이 상한에 걸린 것).
    """

    def __init__(self, text: str, detail: str = "", stop_reason: Optional[str] = None):
        self.text = text
        self.detail = detail
        self.stop_reason = stop_reason
        super().__init__(f"JSON 파싱 실패: {detail} | {(text or '')[:200]!r}")


class LLMTruncated(LLMParseError):
    """stop_reason=max_tokens 로 출력이 잘렸다(thinking 이 상한을 소진했거나 JSON 이 끝나기 전에 끊김).

    재시도(truncation_retry_max_tokens)까지 잘린 경우. 호출자는 배치를 줄이거나 effort="low" /
    thinking=False 를 쓰거나 max_tokens 를 올린다. max_tokens: 마지막 시도의 max_tokens.
    """

    def __init__(self, text: str, detail: str = "", stop_reason: Optional[str] = "max_tokens",
                 max_tokens: Optional[int] = None):
        super().__init__(text, detail, stop_reason=stop_reason)
        self.max_tokens = max_tokens


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


def _first_json_complete(text: str) -> tuple[bool, Any]:
    """첫 `{`/`[` 에서 시작하는 최상위 JSON 이 완결됐는가. 잘린 출력에서 뒤쪽 부분 객체를 집지 않기 위함."""
    body = strip_code_fences(text or "").strip()
    starts = [i for i, ch in enumerate(body) if ch in "{["]
    if not starts:
        return False, None
    try:
        value, _ = json.JSONDecoder().raw_decode(body, starts[0])
        return True, value
    except json.JSONDecodeError:
        return False, None


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


def _field(block, name):
    v = getattr(block, name, None)
    if v is None and isinstance(block, dict):
        v = block.get(name)
    return v


def _text_of(resp) -> str:
    """content 중 type=="text" 블록의 text 만 이어붙인다. thinking/redacted_thinking/tool_use 등은 무시.

    type 이 없는 블록(구형/가짜 응답)은 text 가 있으면 텍스트로 본다.
    """
    parts = []
    for block in getattr(resp, "content", None) or []:
        btype = _field(block, "type")
        if btype is not None and btype != "text":
            continue
        t = _field(block, "text")
        if isinstance(t, str) and t:
            parts.append(t)
    return "".join(parts)


def _block_types(resp) -> list:
    return [_field(b, "type") or "?" for b in getattr(resp, "content", None) or []]


def thinks_by_default(model: str) -> bool:
    """thinking 파라미터 없이도 thinking 이 켜지는(=thinking 토큰이 max_tokens 를 먹는) 모델인가."""
    return bool(_THINKING_DEFAULT_RE.match(model or ""))


def effective_max_tokens(model: str, max_tokens: int) -> int:
    """thinking 기본 모델이면 MIN_THINKING_MAX_TOKENS 하한 적용."""
    if thinks_by_default(model) and max_tokens < MIN_THINKING_MAX_TOKENS:
        return MIN_THINKING_MAX_TOKENS
    return max_tokens


def _empty_reason(resp, max_tokens: int) -> Optional[str]:
    """텍스트가 빈 응답의 사유 문자열(로그/에러용). stop_reason=max_tokens 일 때만."""
    if _field(resp, "stop_reason") != "max_tokens":
        return None
    return (f"stop_reason=max_tokens — thinking 이 max_tokens={max_tokens} 를 소진, text 블록 없음 "
            f"(content={_block_types(resp)})")


def _thinking_params(model: str, effort: Optional[str], thinking: Optional[bool]) -> dict:
    """effort/thinking 인자 → messages.create kwargs. API 가 400 낼 조합은 여기서 ValueError."""
    out: dict[str, Any] = {}
    if effort is not None:
        if effort not in EFFORTS:
            raise ValueError(f"effort 는 {EFFORTS} 중 하나: {effort!r}")
        out["output_config"] = {"effort": effort}
    if thinking is False:
        if _NO_DISABLE_RE.match(model or ""):
            raise ValueError(f"{model} 은 thinking 끄기(disabled)를 받지 않는다(400) — effort='low' 를 쓸 것")
        if effort in ("xhigh", "max"):
            raise ValueError(f"thinking=False 는 effort high 이하에서만 가능(400): effort={effort!r}")
        out["thinking"] = {"type": "disabled"}
    elif thinking is True:
        out["thinking"] = {"type": "adaptive"}
    return out


def _request(
    prompt, *, system, images, model, max_tokens, temperature, retries, backoff_base,
    cache_system, client, api_key, sleep, effort=None, thinking=None,
):
    """Claude 호출 → 원본 응답 객체. (text, stop_reason, 실제 max_tokens) 해석은 호출자가."""
    if isinstance(prompt, str):
        content: list[dict] = []
        for im in images or []:
            content.append(image_block(im))
        content.append({"type": "text", "text": prompt})
        messages = [{"role": "user", "content": content if len(content) > 1 else prompt}]
    else:
        messages = list(prompt)

    eff = effective_max_tokens(model, max_tokens)
    if eff != max_tokens:
        logger.debug(f"max_tokens {max_tokens} → {eff} (thinking 모델 {model} 하한)")
    kwargs: dict[str, Any] = {"model": model, "max_tokens": eff, "messages": messages}
    kwargs.update(_thinking_params(model, effort, thinking))
    if system:
        if cache_system:
            kwargs["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        else:
            kwargs["system"] = system
    if temperature is not None:
        if thinks_by_default(model) and temperature != 1:
            # thinking 기본 모델은 temperature=1 만 허용(그 외 400, 비재시도 → 봇 크래시). 무시하고 경고(.72).
            logger.warning(f"temperature={temperature} 무시 — thinking 기본 모델 {model} 은 temperature 1 만 허용")
        else:
            kwargs["temperature"] = temperature

    cl = client or _client(api_key)
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            return cl.messages.create(**kwargs)
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
    effort: Optional[str] = None,
    thinking: Optional[bool] = None,
) -> str:
    """Claude 호출 → 응답 텍스트(type=="text" 블록만). 429/529/5xx 지수 백오프, 401/403/크레딧 → LLMUnavailable.

    thinking 기본 모델은 max_tokens 하한(MIN_THINKING_MAX_TOKENS) 적용. 텍스트가 비고
    stop_reason=max_tokens 면 경고 로그만 남기고 "" 반환(판단은 호출자).
    effort / thinking: llm_json 과 같음(기본 None = 보내지 않음 → 모델 기본).
    """
    resp = _request(
        prompt, system=system, images=images, model=model, max_tokens=max_tokens,
        temperature=temperature, retries=retries, backoff_base=backoff_base,
        cache_system=cache_system, client=client, api_key=api_key, sleep=sleep,
        effort=effort, thinking=thinking,
    )
    text = _text_of(resp)
    if not text.strip():
        reason = _empty_reason(resp, effective_max_tokens(model, max_tokens))
        if reason:
            logger.warning(f"LLM 빈 응답 ({model}): {reason}")
    return text


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
    effort: Optional[str] = None,
    thinking: Optional[bool] = None,
    truncation_retry_max_tokens: Optional[int] = TRUNCATION_RETRY_MAX_TOKENS,
) -> Any:
    """Claude 에게 JSON 을 받아 파싱해 돌려준다(dict/list).

    응답 content 중 type=="text" 블록만 파싱한다(thinking/redacted_thinking 무시).
    model 이 thinking 기본 모델(HAIKU/SONNET 등 5.x)이면 max_tokens 는 최소 MIN_THINKING_MAX_TOKENS(1024)
    로 올려 보낸다 — thinking 토큰도 max_tokens 에 포함되기 때문.

    effort: "low"|"medium"|"high"|"xhigh"|"max" → output_config.effort. thinking 을 덜 하게 하는 기본 수단
        (Haiku 5.5 기본 medium). None 이면 보내지 않는다.
    thinking: False → thinking 끄기({"type":"disabled"}, Haiku 5.5 는 effort high 이하에서만). True → adaptive
        명시. None(기본) → 보내지 않음(5.x 는 adaptive 가 기본).
    truncation_retry_max_tokens: 응답이 stop_reason=max_tokens 로 잘렸으면(텍스트 없음 또는 최상위 JSON 미완결)
        max_tokens 를 이 값으로 올려 1회 재시도(기본 16000, 현재 값 이하이거나 None 이면 재시도 안 함).

    Raises:
        LLMUnavailable: 인증/크레딧/과부하 재시도 소진 — 커서 전진 금지.
        LLMTruncated: (재시도 후에도) stop_reason=max_tokens 로 잘림. LLMParseError 하위라 기존 except 로 잡힌다.
            detail 에 사유("thinking 이 max_tokens 소진" / "출력 잘림") + 경고 로그.
        LLMParseError: 응답은 왔지만 JSON 이 아님 — 이 입력만 실패. 예외의 .stop_reason 에 응답 stop_reason.
    """
    def call(mt: int):
        return _request(
            prompt, system=system, images=images, model=model, max_tokens=mt,
            temperature=temperature, retries=retries, backoff_base=2.0,
            cache_system=cache_system, client=client, api_key=api_key, sleep=sleep,
            effort=effort, thinking=thinking,
        )

    eff = effective_max_tokens(model, max_tokens)
    resp = call(max_tokens)
    text = _text_of(resp)
    if _field(resp, "stop_reason") == "max_tokens" and not _first_json_complete(text)[0] \
            and truncation_retry_max_tokens and truncation_retry_max_tokens > eff:
        logger.warning(f"LLM 출력 잘림({model}, stop_reason=max_tokens, max_tokens={eff}) → "
                       f"max_tokens={truncation_retry_max_tokens} 로 1회 재시도")
        eff = truncation_retry_max_tokens
        resp = call(eff)
        text = _text_of(resp)
    stop_reason = _field(resp, "stop_reason")

    if stop_reason == "max_tokens":
        if not text.strip():
            reason = _empty_reason(resp, eff)
            logger.warning(f"LLM 빈 응답 ({model}): {reason}")
            raise LLMTruncated(text, f"빈 응답 — {reason}", max_tokens=eff)
        complete, value = _first_json_complete(text)
        if complete:
            return value
        logger.warning(f"LLM 출력 잘림 ({model}): stop_reason=max_tokens, max_tokens={eff}")
        raise LLMTruncated(text, f"JSON 미완결 (stop_reason=max_tokens — 출력 잘림, max_tokens={eff})",
                           max_tokens=eff)
    try:
        return extract_json(text)
    except LLMParseError as e:
        e.stop_reason = stop_reason
        raise
