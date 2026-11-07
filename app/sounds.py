"""「ピー」（話してください）と「ポーン」（新着のチャイム）を作る。音のファイルを持ち歩かなくてよいよう、その場で作る。

チャイムは読み上げとは違う、短くやわらかい音にする（仕様書 3.3.2：驚かせない）。
"""

import math
import struct
import wave
from pathlib import Path

RATE = 16000
SOUND_DIR = Path("/tmp/koe-dayori-sounds")


def _write(path, samples):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32767)) for s in samples))
    return path


def beep(path=None, freq=1000, seconds=0.6, volume=0.5):
    """留守番電話の「ピー」。端の切れ目でプツッと鳴らないよう、前後を少しだけなめらかにする。"""
    n = int(RATE * seconds)
    fade = int(RATE * 0.01)
    samples = []
    for i in range(n):
        env = min(1.0, i / fade, (n - i) / fade)
        samples.append(volume * env * math.sin(2 * math.pi * freq * i / RATE))
    return _write(Path(path) if path else SOUND_DIR / "beep.wav", samples)


def chime(path=None, seconds=1.6, volume=0.25):
    """「ポーン」。高めの音が、ゆっくり消えていく。小さめの音量にする。"""
    n = int(RATE * seconds)
    attack = int(RATE * 0.005)
    samples = []
    for i in range(n):
        t = i / RATE
        env = min(1.0, i / attack) * math.exp(-3.0 * t)
        tone = math.sin(2 * math.pi * 880 * t) + 0.3 * math.sin(2 * math.pi * 1760 * t)
        samples.append(volume * env * tone / 1.3)
    return _write(Path(path) if path else SOUND_DIR / "chime.wav", samples)
