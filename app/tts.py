"""Open JTalk で読み上げの WAV を作る（Pi 上。仕様書 3.3）。

長い文は「。」で区切って、何本かの WAV にする。
受話器を置いたら、文の切れ目を待たずに止められるようにするため（audio.py が1本ずつ流す）。
"""

import re
import subprocess
import tempfile
from pathlib import Path

from app import config

MAX_CHARS = 80


def split_sentences(text):
    """読み上げやすい長さに区切る。改行も文の区切りとして扱う。"""
    text = re.sub(r"[ \t　]+", " ", text.strip())
    parts = [p.strip() for p in re.split(r"(?<=[。！？!?])|\n+", text) if p and p.strip()]
    chunks = []
    for p in parts:
        while len(p) > MAX_CHARS:  # 句点のない長い文は、読点のあたりで切る
            cut = p.rfind("、", 0, MAX_CHARS)
            cut = cut + 1 if cut > 0 else MAX_CHARS
            chunks.append(p[:cut])
            p = p[cut:]
        if p:
            chunks.append(p)
    return chunks


def synthesize(text, out_dir=None):
    """文を読み上げた WAV のパスの一覧を返す。"""
    out_dir = Path(out_dir or tempfile.mkdtemp(prefix="koe-tts-"))
    paths = []
    for i, chunk in enumerate(split_sentences(text)):
        path = out_dir / f"{i:03d}.wav"
        subprocess.run(
            ["open_jtalk", "-x", config.JTALK_DIC, "-m", config.JTALK_VOICE,
             "-r", config.JTALK_SPEED, "-ow", str(path)],
            input=chunk.encode("utf-8"), check=True, timeout=60,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        paths.append(path)
    return paths
