"""モニター表示（仕様書 3.4）。Chromium のキオスクモードで http://127.0.0.1:8001/ を全画面で開く。

画面の3つの状態
- new（新着あり）：「お便りが〇件あります（誰から）」「受話器を取ってください」だけを大きく。本文は出さない
- playing（再生中）：誰から・いつ・本文を大きく、写真があれば大きく
- idle（ふだん）：時計と、最後に再生したお便り、その前の数件を小さく

夜（22〜7時）で新着がないときは、画面を暗くする（U26 の仮決め。実機で焼き付きを見て決め直す）。
このサーバーは 127.0.0.1 だけで待つ。受け口のトンネル（8000番）とは別のポートにして、外に出さない。
"""

import json
import logging
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app import clock, config, db as koe_db, inbox

log = logging.getLogger(__name__)

PAGE = Path(__file__).resolve().parent / "static" / "display.html"


class State:
    """いま再生しているお便りと、ひとこと（「送りました」など）。DB に入らないものだけを持つ。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.playing_id = None
        self.notice = None
        self.notice_until = None

    def show_letter(self, letter_id):
        with self._lock:
            self.playing_id = letter_id

    def clear_letter(self):
        with self._lock:
            self.playing_id = None

    def show_notice(self, text, seconds=8):
        with self._lock:
            self.notice = text
            self.notice_until = datetime.now().timestamp() + seconds

    def snapshot(self):
        with self._lock:
            notice = self.notice if self.notice_until and datetime.now().timestamp() < self.notice_until else None
            return self.playing_id, notice


STATE = State()


def _letter_json(row):
    if row is None:
        return None
    received = datetime.fromisoformat(row["received_at"])
    return {
        "id": row["id"],
        "sender": row["sender_name"] or inbox.DEFAULT_SENDER,
        "when": f"{received.month}月{received.day}日 {clock.hm(received)}",
        "kind": row["kind"],
        "text": row["display"] or row["spoken"] or "",
        "photo": f"/photo/{row['id']}" if row["image_path"] else None,
    }


def build_state(conn, now=None):
    now = now or datetime.now()
    playing_id, notice = STATE.snapshot()
    waiting = inbox.unplayed(conn)
    senders = []
    for r in waiting:
        name = r["sender_name"] or inbox.DEFAULT_SENDER
        if name not in senders:
            senders.append(name)

    if playing_id is not None:
        mode = "playing"
    elif waiting:
        mode = "new"
    else:
        mode = "idle"

    recent = [_letter_json(r) for r in inbox.recent_played(conn, 4)]
    return {
        "mode": mode,
        "night": not clock.is_daytime(now) and mode == "idle",
        "clock": {"date": f"{now.month}月{now.day}日（{'月火水木金土日'[now.weekday()]}）", "time": clock.hm(now)},
        "waiting": {"count": len(waiting), "senders": senders},
        "playing": _letter_json(inbox.get(conn, playing_id)) if playing_id is not None else None,
        "recent": recent,
        "notice": notice,
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self._send(200, "text/html; charset=utf-8", PAGE.read_bytes())
        elif path == "/api/state":
            conn = koe_db.connect()
            try:
                body = json.dumps(build_state(conn), ensure_ascii=False).encode("utf-8")
            finally:
                conn.close()
            self._send(200, "application/json; charset=utf-8", body)
        elif path.startswith("/photo/"):
            self._photo(path.rsplit("/", 1)[-1])
        else:
            self._send(404, "text/plain", b"")

    def _photo(self, letter_id):
        if not letter_id.isdigit():
            self._send(404, "text/plain", b"")
            return
        conn = koe_db.connect()
        try:
            row = inbox.get(conn, int(letter_id))
        finally:
            conn.close()
        if not row or not row["image_path"] or not Path(row["image_path"]).exists():
            self._send(404, "text/plain", b"")
            return
        ctype = "image/png" if row["image_path"].endswith(".png") else "image/jpeg"
        self._send(200, ctype, Path(row["image_path"]).read_bytes())

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def start(port=None):
    port = config.DISPLAY_PORT if port is None else port  # 0 は「空いているポート」（試験用）
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, name="display", daemon=True).start()
    log.info("モニター表示: http://127.0.0.1:%d/", server.server_address[1])
    return server
