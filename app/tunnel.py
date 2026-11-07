"""LINE の受け口の URL を、Cloudflare のトンネル（Quick Tunnel）に合わせて登録し直す。

11/7 変更：Tailscale Funnel は LINE からつながらなかった（LINE の試験で
「Session protocol negotiation failure」。こちらからの curl は TLS 1.2/1.3 とも通る）。
同じ時刻に Cloudflare Quick Tunnel を試すと LINE から 200 が返ったので、受け口をこちらに替えた。

Quick Tunnel は cloudflared を起動するたびに URL が変わる。そのため定期の仕事（30秒ごと）で
cloudflared の窓口（/quicktunnel）から今の URL を聞き、LINE に登録してある URL と違えば書き換える。
親は何もしなくてよい。cloudflared は deploy/koe-tunnel.service で動かす。
"""

import json
import logging
import urllib.error
import urllib.request

from app import config, line_client

log = logging.getLogger(__name__)

ENDPOINT_URL = "https://api.line.me/v2/bot/channel/webhook/endpoint"
CALLBACK_PATH = "/callback"

_registered = None  # この起動で、LINE に登録済みと確かめた URL


def current_hostname(metrics_url=None):
    """cloudflared が今使っている URL のホスト名。cloudflared が動いていなければ None。"""
    url = (metrics_url or config.TUNNEL_METRICS_URL).rstrip("/") + "/quicktunnel"
    try:
        with urllib.request.urlopen(url, timeout=3) as res:
            return json.loads(res.read()).get("hostname") or None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def _line_request(method, data=None):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(ENDPOINT_URL, data=body, method=method, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code == 429 or e.code >= 500:
            raise line_client.TemporaryError(f"LINE {e.code}: {detail}") from e
        raise line_client.PermanentError(f"LINE {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise line_client.TemporaryError(f"LINE につながりません: {e}") from e


def sync_webhook(metrics_url=None):
    """LINE の受け口を今のトンネルに合わせる。書き換えたら新しい URL、何もしなければ None。

    cloudflared が動いていないとき（Mac で試すときなど）は何もしない。
    """
    global _registered
    hostname = current_hostname(metrics_url)
    if not hostname:
        return None
    want = f"https://{hostname}{CALLBACK_PATH}"
    if want == _registered:
        return None  # この起動で確かめ済み。LINE には問い合わせない

    now = _line_request("GET").get("endpoint")
    if now != want:
        _line_request("PUT", {"endpoint": want})
        log.info("LINE の受け口を登録し直しました: %s（前は %s）", want, now)
        _registered = want
        return want
    _registered = want
    return None
