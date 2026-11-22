"""クラウド（Cloudflare Workers、relay/）に貯めてあるお便りを、Pi から取りに行く（11/22 D29）。

LINE の Webhook は Workers が受けて署名を確かめ、決めたグループのお便りだけを貯めている。
Pi は外へ出ていく通信だけで済む。Pi が止まっていても、お便りはクラウドで待っている。

取りに行き方（U31）
- WebSocket（/ws）でつなぎっぱなしにし、届いたときだけ「new」と知らせてもらう。中身は HTTPS（/events）で取る
  （Workers 側は Hibernation API なので、待っているあいだ料金がかからない。ping は通信を保つためだけ）
- WebSocket が切れているあいだは、定期の仕事（30秒ごと）で /events を聞く（取りこぼしを防ぐ）
- つながっていても、念のため SAFETY_POLL ごとに1回は聞く

順番の約束：SQLite に入れ終えてから /ack で「受け取った」と返す。Workers はそのとき初めて消す。
入れたあと ack の前に落ちても、次に同じものを受け取るだけで、inbox.add_event() が event_id で二重を防ぐ。
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.request

from app import config, inbox, webhook

log = logging.getLogger(__name__)

SAFETY_POLL = 30 * 60   # WebSocket がつながっていても、この秒数ごとに1回は聞く
RECONNECT_MIN = 5       # WebSocket がつながらないとき、つなぎ直す間隔（倍ずつ、上限 RECONNECT_MAX）
RECONNECT_MAX = 300


class RelayError(Exception):
    """クラウドにつながらない・返事がおかしい。次の回にやり直す。"""


def _request(path, method="GET", data=None, timeout=20):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(config.RELAY_URL + path, data=body, method=method, headers={
        "Authorization": f"Bearer {config.RELAY_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "koe-dayori",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.read(), res.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        raise RelayError(f"relay {e.code} {path}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RelayError(f"relay につながりません: {e}") from e


def _save_image(item):
    """Workers が LINE から取り出しておいた写真を取ってきて置く。置き場所を返す。"""
    data, content_type = _request(f"/image?id={item['id']}", timeout=60)
    config.PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    ext = ".png" if "png" in content_type else ".jpg"
    path = config.PHOTO_DIR / f"relay-{item['id']}{ext}"
    path.write_bytes(data)
    return path


def pull(conn, now=None, request=None, save_image=None):
    """クラウドに貯まっているお便りを受け取って SQLite に入れ、受け取ったものを消してもらう。

    新しく入れたお便りの数を返す。通信が切れていたら RelayError（入れ終えた分は ack してから）。
    """
    request = request or _request
    save_image = save_image or _save_image
    total = 0
    while True:
        body, _ = request("/events")
        items = json.loads(body or b"{}").get("events", [])
        if not items:
            return total
        done, added, error = [], 0, None
        for item in items:
            try:
                letter = webhook.to_letter(item["event"])
                if letter:
                    image_path = None
                    if letter["kind"] == "image" and item.get("image"):
                        image_path = save_image(item)  # 先に置く。落ちたら ack しないので、次にやり直す
                    letter_id = inbox.add_event(conn, now=now, **letter)
                    if letter_id:
                        added += 1
                        if image_path:
                            conn.execute("UPDATE letters SET image_path = ? WHERE id = ?",
                                         (str(image_path), letter_id))
                            conn.commit()
                done.append(item["id"])  # 入れ終えた（二重・受けないものも、もう要らない）
            except RelayError as e:
                error = e
                break
        if done:
            request("/ack", method="POST", data={"ids": done})
        total += added
        if added:
            log.info("お便りを %d 件受け取りました（クラウドから）", added)
        if error:
            raise error
        if len(done) < len(items):
            return total


class Relay:
    """定期の仕事から呼ぶ。WebSocket の見張りは別のスレッドで動かす。"""

    def __init__(self, on_received=None):
        self.on_received = on_received  # 「届いた」の知らせが来たら呼ぶ（定期の仕事を起こす）
        self.connected = False
        self._due = threading.Event()   # 取りに行く用がある
        self._due.set()                 # 起動したら、まず1回取りに行く
        self._last_pull = 0.0
        self._stop = False
        self._ws = None

    # ---- 定期の仕事（Housekeeper のスレッド）から ----
    def due(self, now_mono=None):
        now_mono = time.monotonic() if now_mono is None else now_mono
        if self._due.is_set() or not self.connected:
            return True
        return now_mono - self._last_pull >= SAFETY_POLL

    def run_once(self, conn, now=None, now_mono=None):
        """用があれば取りに行く。新しく入れた数を返す。"""
        if not self.due(now_mono):
            return 0
        self._due.clear()
        try:
            added = pull(conn, now=now)
        except RelayError as e:
            self._due.set()  # 次の回にやり直す
            log.warning("クラウドからお便りを受け取れません（次の回に再試行）: %s", e)
            return 0
        self._last_pull = time.monotonic() if now_mono is None else now_mono
        return added

    def notify(self):
        """「届いた」の知らせ。どのスレッドから呼んでもよい。"""
        self._due.set()
        if self.on_received:
            self.on_received()

    # ---- WebSocket（別のスレッド） ----
    def start(self):
        threading.Thread(target=self._ws_loop, name="relay-ws", daemon=True).start()

    def _ws_loop(self):
        import websocket  # websocket-client

        url = config.RELAY_URL.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/ws"
        wait = RECONNECT_MIN
        while not self._stop:
            opened = time.monotonic()
            self._ws = websocket.WebSocketApp(
                url,
                header=[f"Authorization: Bearer {config.RELAY_TOKEN}"],
                on_open=self._on_open,
                on_message=self._on_message,
                on_close=self._on_close,
                on_error=lambda ws, e: log.warning("クラウドとの WebSocket: %s", e),
            )
            try:
                # protocol の ping（Workers 側は眠ったまま応える。料金はかからない）
                self._ws.run_forever(ping_interval=config.RELAY_PING_SECONDS,
                                     ping_timeout=min(20, config.RELAY_PING_SECONDS - 1))
            except Exception:
                log.exception("クラウドとの WebSocket で止まりました")
            self.connected = False
            if self._stop:
                return
            if time.monotonic() - opened > 60:
                wait = RECONNECT_MIN  # しばらくつながっていたなら、すぐつなぎ直す
            time.sleep(wait)
            wait = min(wait * 2, RECONNECT_MAX)

    def _on_open(self, ws):
        was = self.connected
        self.connected = True
        if not was:
            log.info("クラウドにつながりました（届いたら知らせが来ます）")
        self.notify()  # つながっていないあいだに届いた分を取りに行く

    def _on_message(self, ws, message):
        if message == "new":
            self.notify()

    def _on_close(self, ws, code, reason):
        if self.connected:
            log.warning("クラウドとの WebSocket が切れました（%s）。つなぎ直すまで30秒ごとに聞きます", code)
        self.connected = False

    def stop(self):
        self._stop = True
        if self._ws:
            self._ws.close()
