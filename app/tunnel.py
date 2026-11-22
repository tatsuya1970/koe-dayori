"""LINE の受け口の URL を、Cloudflare のトンネル（Quick Tunnel）に合わせて登録し直す。

11/7 変更：Tailscale Funnel は LINE からつながらなかった（LINE の試験で
「Session protocol negotiation failure」。こちらからの curl は TLS 1.2/1.3 とも通る）。
同じ時刻に Cloudflare Quick Tunnel を試すと LINE から 200 が返ったので、受け口をこちらに替えた。

Quick Tunnel は cloudflared を起動するたびに URL が変わる。そのため定期の仕事（30秒ごと）で
cloudflared の窓口（/quicktunnel）から今の URL を聞き、LINE に登録してある URL と違えば書き換える。
親は何もしなくてよい。cloudflared は deploy/koe-tunnel.service で動かす。

11/22 追加：Cloudflare 側でトンネルが消されても cloudflared は落ちないので、/ready を見張って
数分つながらなければ作り直す（下の Watchdog）。
"""

import json
import logging
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timedelta

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


# ---- 見張り：トンネルが消えたら作り直す（11/22 追加） ----
#
# 11/22 12:23、Cloudflare 側でトンネルが消された（ログに "Unauthorized: Tunnel not found"）。
# cloudflared は消えたトンネルにつなぎ直そうとし続けるだけでプロセスは落ちず、Restart=always も効かない。
# URL も変わらないので sync_webhook も動かず、13:38 に手で再起動するまで LINE のお便りが届かなかった。
#
# そこで cloudflared の窓口の /ready を定期の仕事（30秒ごと）で見る。
#   200（readyConnections が1以上）… つながっている
#   503（readyConnections が0）     … つながっていない。11/22 は 12:23:17 から1本も登録されていなかった
#   返事がない                       … cloudflared が動いていない（Mac で試すとき、429 で落ちて systemd が
#                                       起こし直している途中など）。ここでは数えない。起こすのは systemd の仕事
# つながらない状態が READY_GRACE 続いたら `systemctl --user restart koe-tunnel` で作り直す。
# 数秒の切断（12:23:15 の "edge closed" のあとすぐ戻るような場合）では作り直さない。
#
# trycloudflare は作り直しが続くと 429 で断る（11/7 夜）。作り直したら RESTART_MIN_GAP は次をしない。
# 作り直しても直らなければ、間を倍ずつ空ける（上限 RESTART_MAX_GAP）。つながったら元に戻す。

READY_GRACE = timedelta(minutes=5)
RESTART_MIN_GAP = timedelta(minutes=10)
RESTART_MAX_GAP = timedelta(minutes=60)
RESTART_COMMAND = ["systemctl", "--user", "restart", "koe-tunnel"]


def ready(metrics_url=None):
    """True＝つながっている、False＝つながっていない、None＝cloudflared が返事をしない。"""
    url = (metrics_url or config.TUNNEL_METRICS_URL).rstrip("/") + "/ready"
    try:
        with urllib.request.urlopen(url, timeout=3) as res:
            body = json.loads(res.read() or b"{}")
            return int(body.get("readyConnections", 1)) > 0
    except urllib.error.HTTPError:
        return False  # 503：接続が1本もない
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def _restart():
    subprocess.run(RESTART_COMMAND, check=True, timeout=30,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


class Watchdog:
    def __init__(self, restart=_restart):
        self.restart = restart
        self.bad_since = None     # つながらなくなった時刻
        self.last_restart = None  # 最後に作り直した時刻
        self.gap = RESTART_MIN_GAP

    def check(self, now, state):
        """state は ready() の返り値。作り直したら True。"""
        if state is None:
            return False  # cloudflared が動いていない。数えない（続きは動き出してから）
        if state:
            if self.bad_since is not None:
                log.info("LINE の受け口がつながりました（%s から切れていました）",
                         self.bad_since.strftime("%H:%M"))
            self.bad_since = None
            self.gap = RESTART_MIN_GAP
            return False

        if self.bad_since is None:
            self.bad_since = now
            log.warning("LINE の受け口がつながっていません。%d分続いたら作り直します",
                        READY_GRACE.total_seconds() // 60)
            return False
        if now - self.bad_since < READY_GRACE:
            return False
        if self.last_restart is not None and now - self.last_restart < self.gap:
            return False  # 作り直したばかり（429 を避ける）

        minutes = int((now - self.bad_since).total_seconds() // 60)
        if self.last_restart is not None and self.last_restart >= self.bad_since:
            # 前に作り直してもつながらなかった。次はもっと間を空ける
            self.gap = min(self.gap * 2, RESTART_MAX_GAP)
        log.warning("LINE の受け口が%d分つながらないので作り直します（次に作り直せるのは%d分あと）",
                    minutes, self.gap.total_seconds() // 60)
        self.last_restart = now
        try:
            self.restart()
        except (subprocess.SubprocessError, OSError) as e:
            log.warning("LINE の受け口を作り直せません: %s", e)
        return True


_watchdog = Watchdog()


def watch(now=None, metrics_url=None):
    """定期の仕事から呼ぶ。作り直したら True。"""
    return _watchdog.check(now or datetime.now(), ready(metrics_url))
