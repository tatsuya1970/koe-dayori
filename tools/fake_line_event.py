"""LINE から返信が来たことにして、受け口（127.0.0.1:8000/callback）に署名つきで送る。

家族に LINE で書いてもらわなくても、お便りの流れ（受け取り → 言い換え → チャイム → 画面 → 受話器で再生）を試せる。
app.main が動いている Pi の上で使う。

    .venv/bin/python -m tools.fake_line_event "週末にそっちへ行くね"
    .venv/bin/python -m tools.fake_line_event "元気？" --name ゆうた

.env の LINE_CHANNEL_SECRET と LINE_GROUP_ID を使う。送り主の名前は LINE に問い合わせず --name を使う。
"""

import argparse
import base64
import hashlib
import hmac
import json
import time
import urllib.request
import uuid

from app import config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("text")
    parser.add_argument("--name", default="たつや", help="送り主の名前（LINE には問い合わせない）")
    parser.add_argument("--url", default=f"http://127.0.0.1:{config.WEBHOOK_PORT}/callback")
    args = parser.parse_args()

    event_id = f"dev-{uuid.uuid4()}"
    payload = {"events": [{
        "type": "message",
        "webhookEventId": event_id,
        "timestamp": int(time.time() * 1000),
        "koeSenderName": args.name,
        "source": {"type": "group", "groupId": config.LINE_GROUP_ID},
        "message": {"type": "text", "id": event_id, "text": args.text,
                    "mention": {"mentionees": [{"index": 0, "length": 0, "isSelf": True}]}},
    }]}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sig = base64.b64encode(hmac.new(config.LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()).decode()
    req = urllib.request.Request(args.url, data=body, method="POST",
                                 headers={"X-Line-Signature": sig, "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as res:
        print(f"送りました: {res.status}")


if __name__ == "__main__":
    main()
