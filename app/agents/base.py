"""Claude を呼んで、決まった形（JSON）で答えを受け取る共通部分。"""

import json
import logging

import anthropic

from app import config

log = logging.getLogger(__name__)

_client = None


class TemporaryError(Exception):
    """通信切れ・混雑など。貯めておいて、あとでやり直す。"""


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY or None)
    return _client


def ask_json(*, model, effort, system, user_text, schema, max_tokens=4000):
    """JSON を返す。答えが使えないとき（断られた・形が違う）は None を返す。

    None のときは、呼び出し側が安全側の値を使う（仕様書 4章）。
    """
    try:
        response = _get_client().beta.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_text}],
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            # 安全のための自動判定で断られたとき、サーバー側で別のモデルにやり直させる
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError) as e:
        raise TemporaryError(str(e)) from e
    except anthropic.APIStatusError as e:
        if e.status_code >= 500 or e.status_code in (408, 409, 529):
            raise TemporaryError(str(e)) from e
        log.error("Claude API エラー %s: %s", e.status_code, e.message)
        return None

    if response.stop_reason == "refusal":
        log.warning("Claude に断られました: %s", response.stop_details)
        return None
    if response.stop_reason == "max_tokens":
        log.warning("答えが途中で切れました（%s）", model)
        return None

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("JSON として読めませんでした: %r", text[:200])
        return None
