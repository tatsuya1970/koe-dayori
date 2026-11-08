"""モニター表示を、仮のお便りで確かめる（Mac でも Pi でも動く。本番のデータは使わない）。

    python3 -m tools.dev_display --mode new       # 新着あり
    python3 -m tools.dev_display --mode playing   # 再生中
    python3 -m tools.dev_display --mode idle      # ふだん
    python3 -m tools.dev_display --mode sent      # 話して送った直後（LINE に送った要約）
ブラウザで http://127.0.0.1:8001/ を開く。Ctrl+C で終わる。
"""

import argparse
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from app import config, db as koe_db, display, inbox

SAMPLES = [
    ("たつや", "週末の土曜日に、\nそっちへ行くね。\nお昼ごはんは一緒に食べよう。"),
    ("ゆうた", "おばあちゃん、\nサッカーの試合で\nゴールを決めたよ！⚽"),
    ("みき", "寒くなってきたから、\n暖かくして寝てね。"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["new", "playing", "idle", "sent"], default="new")
    parser.add_argument("--port", type=int, default=config.DISPLAY_PORT)
    args = parser.parse_args()

    config.KOE_DB_PATH = Path(tempfile.mkdtemp(prefix="koe-display-")) / "koe.sqlite3"
    conn = koe_db.connect()
    now = datetime.now()
    ids = []
    for i, (name, text) in enumerate(SAMPLES):
        t = now - timedelta(hours=len(SAMPLES) - i)
        letter_id = inbox.add_event(conn, event_id=f"dev{i}", kind="text", sender_name=name, text=text, now=t)
        conn.execute("UPDATE letters SET spoken = ?, display = ?, status = 'ready' WHERE id = ?", (text, text, letter_id))
        ids.append(letter_id)
    conn.commit()

    if args.mode in ("idle", "playing", "sent"):
        for letter_id in ids[:-1]:
            inbox.mark_played(conn, letter_id)
    if args.mode in ("idle", "sent"):
        inbox.mark_played(conn, ids[-1])
    if args.mode == "sent":
        display.STATE.show_sent("膝が少し痛いそうです。来週、病院の予約をお願いしたいとのことです。", seconds=3600)
    if args.mode == "playing":
        display.STATE.show_letter(ids[-1])

    display.start(args.port)
    print(f"http://127.0.0.1:{args.port}/ を開いてください（{args.mode}）。Ctrl+C で終わる")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
