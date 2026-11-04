"""LINEのグループIDを受け取るための、仮の受け口。

使い方（Pi）:
    python3 tools/capture_group_id.py
別の画面で cloudflared の仮URLを立て、LINE Developers の Webhook URL に
  https://<仮URL>/callback
を入れて「検証」する。そのあと公式アカウントをグループに招待すると、
グループIDが画面に出る。

外部ライブラリは使わない（標準ライブラリだけ）。
"""

import base64
import hashlib
import hmac
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = 8000
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env(path):
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


ENV = load_env(ENV_PATH)
SECRET = ENV.get("LINE_CHANNEL_SECRET", "")


def signature_ok(body, signature):
    digest = hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        signature = self.headers.get("X-Line-Signature", "")

        if not signature_ok(body, signature):
            print("署名が合いません（LINE以外からの送信か、シークレットの誤り）")
            self.send_response(403)
            self.end_headers()
            return

        self.send_response(200)
        self.end_headers()

        events = json.loads(body).get("events", [])
        if not events:
            print("LINEからの検証を受け取りました（OK）")
            return

        for event in events:
            source = event.get("source", {})
            kind = event.get("type")
            if source.get("type") == "group":
                print("")
                print("==============================")
                print(f"イベント: {kind}")
                print(f"LINE_GROUP_ID={source.get('groupId')}")
                print("==============================")
                print("上の1行を .env に書き写してください")
            elif source.get("type") == "user":
                print(f"イベント: {kind}（1対1のトーク）")
                print(f"開発用の自分のID: {source.get('userId')}")

    def log_message(self, format, *args):
        # アクセスのたびの細かい記録は出さない
        pass


def main():
    if not SECRET:
        print(f"{ENV_PATH} に LINE_CHANNEL_SECRET がありません")
        return
    print(f"待ち受け中: http://localhost:{PORT}/callback （止めるときは Ctrl+C）")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
