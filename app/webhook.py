"""LINE の返信を受け取る（Cloudflare Quick Tunnel の先。11/7 に Funnel から替えた。仕様書 3.3）。

- 署名が合わないものは 403 で捨てる
- 決めた家族グループのメッセージだけを受ける
- U24 仮決め：文は「公式アカウントへのメンションつき」だけを読む（REPLY_FILTER=all で全部）。写真はいつも受ける
- すぐ 200 を返し、言い換えなどは別のスレッド（inbox.prepare）で行う

Funnel は 127.0.0.1:8000 をまるごと外に出すので、ここには /callback 以外を置かない。
"""

import base64
import hashlib
import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import config, db as koe_db, inbox

log = logging.getLogger(__name__)


def signature_ok(body, signature, secret=None):
    secret = config.LINE_CHANNEL_SECRET if secret is None else secret
    if not secret or not signature:
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode("utf-8"), signature)


def strip_mentions(text, mentionees):
    """メンション（@声だより など）を本文から取り除く。位置は UTF-16 の単位で来る。"""
    data = text.encode("utf-16-le")
    for m in sorted(mentionees, key=lambda m: m.get("index", 0), reverse=True):
        start, length = m.get("index", 0) * 2, m.get("length", 0) * 2
        data = data[:start] + data[start + length:]
    return data.decode("utf-16-le").strip()


def to_letter(event, group_id=None, reply_filter=None):
    """LINE のイベントを、お便りとして貯める形（dict）にする。受けないものは None。"""
    group_id = config.LINE_GROUP_ID if group_id is None else group_id
    reply_filter = reply_filter or config.REPLY_FILTER

    source = event.get("source", {})
    if event.get("type") != "message" or source.get("type") != "group":
        return None
    if not group_id or source.get("groupId") != group_id:
        return None

    message = event.get("message", {})
    base = {
        "event_id": event.get("webhookEventId") or message.get("id"),
        "sender_id": source.get("userId"),
        # 試験用（tools/fake_line_event.py）。LINE からは来ない項目。署名が合ったものだけがここに来る
        "sender_name": event.get("koeSenderName"),
    }
    if message.get("type") == "image":
        return {**base, "kind": "image", "line_message_id": message.get("id")}
    if message.get("type") != "text":
        return None  # スタンプ・動画・音声などは、今は受けない

    text = message.get("text", "")
    mentionees = (message.get("mention") or {}).get("mentionees", [])
    to_me = any(m.get("isSelf") for m in mentionees)
    if reply_filter == "mention" and not to_me:
        return None
    text = strip_mentions(text, mentionees) if mentionees else text.strip()
    if not text:
        return None
    return {**base, "kind": "text", "text": text}


class Handler(BaseHTTPRequestHandler):
    on_received = None  # 新しいお便りを貯めたら呼ぶ（準備のスレッドを起こす）

    def do_POST(self):
        if self.path.split("?")[0] != "/callback":
            self._reply(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if not signature_ok(body, self.headers.get("X-Line-Signature", "")):
            log.warning("署名が合いません（LINE以外からの送信か、シークレットの誤り）")
            self._reply(403)
            return

        try:
            events = json.loads(body).get("events", [])
        except json.JSONDecodeError:
            self._reply(400)
            return

        added = 0
        conn = koe_db.connect()
        try:
            for event in events:
                letter = to_letter(event)
                if letter and inbox.add_event(conn, **letter):
                    added += 1
        finally:
            conn.close()
        self._reply(200)

        if added:
            log.info("お便りを %d 件受け取りました", added)
            if Handler.on_received:
                Handler.on_received()

    def do_GET(self):
        self._reply(404)

    def _reply(self, code):
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format, *args):
        pass


def start(on_received=None, port=None):
    Handler.on_received = on_received
    port = config.WEBHOOK_PORT if port is None else port  # 0 は「空いているポート」（試験用）
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, name="webhook", daemon=True).start()
    log.info("LINE の受け口: http://127.0.0.1:%d/callback", server.server_address[1])
    return server
