"""LINE Messaging API で、家族のグループに送る。標準ライブラリだけで書く。"""

import json
import urllib.error
import urllib.request

from app import config

PUSH_URL = "https://api.line.me/v2/bot/message/push"


class TemporaryError(Exception):
    """通信切れ・LINE側の不調。あとで送り直す。"""


class PermanentError(Exception):
    """トークンの誤りなど。送り直しても直らない。"""


def push_text(text, retry_key, to=None):
    """グループにテキストを送る。

    retry_key（UUID）を同じにして送り直せば、LINE側で二重に届かない。
    """
    body = json.dumps({
        "to": to or config.LINE_GROUP_ID,
        "messages": [{"type": "text", "text": text[:5000]}],
    }).encode("utf-8")
    req = urllib.request.Request(PUSH_URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}",
        "X-Line-Retry-Key": retry_key,
    })
    try:
        with urllib.request.urlopen(req, timeout=15):
            return
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code == 409:
            return  # 同じ再送キーで、もう受け付けられている
        if e.code == 429 or e.code >= 500:
            raise TemporaryError(f"LINE {e.code}: {detail}") from e
        raise PermanentError(f"LINE {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TemporaryError(f"LINE につながりません: {e}") from e
