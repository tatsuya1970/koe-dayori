"""文字起こしだけを試し、速さを測る（U14：small か base か）。

使い方:
    .venv/bin/python -m tools.try_stt a.wav b.wav ...
    KOE_WHISPER_MODEL=base .venv/bin/python -m tools.try_stt a.wav
"""

import sys
import time

from app import config, stt


def main():
    started = time.monotonic()
    stt._get_model()
    print(f"モデル {config.WHISPER_MODEL} の読み込み: {time.monotonic() - started:.1f}秒")
    for path in sys.argv[1:]:
        t = stt.transcribe(path)
        print(f"\n[{path}] 録音 {t.duration:.1f}秒 → 文字起こし {t.elapsed:.1f}秒"
              f"（話した {t.speech_seconds:.1f}秒、聞き取りにくい={t.unclear}）")
        print(t.text)


if __name__ == "__main__":
    main()
