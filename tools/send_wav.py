"""WAV ファイルを1つ渡して、「話して送る」流れを最後まで試す。

使い方（Pi）:
    .venv/bin/python -m tools.send_wav 録音.wav            # LINE に送る
    .venv/bin/python -m tools.send_wav 録音.wav --dry-run  # 送らずに文面だけ見る

渡した WAV は消さない（本番の流れでは、文字にしたら消す）。
"""

import argparse
import logging
import tempfile
import time
from pathlib import Path

from app import outbox, pipeline


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wav")
    parser.add_argument("--dry-run", action="store_true", help="LINE に送らない")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.dry_run:
        # 試しのときは本番の貯め置きを汚さない
        db = outbox.connect(Path(tempfile.mkdtemp()) / "outbox.sqlite3")
    else:
        db = outbox.connect()

    started = time.monotonic()
    result = pipeline.handle_recording(args.wav, db, keep_audio=True, dry_run=args.dry_run)
    print(f"結果: {result.status}（全体 {time.monotonic() - started:.1f}秒、未送信 {result.remaining}件）")


if __name__ == "__main__":
    main()
