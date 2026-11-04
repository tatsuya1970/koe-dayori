"""設定。秘密情報は .env から、それ以外はここの既定値か環境変数から読む。"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
DATA_DIR = ROOT / "data"


def load_env(path=ENV_PATH):
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


_ENV = load_env()


def get(key, default=""):
    """環境変数 → .env → 既定値 の順で探す。"""
    return os.environ.get(key) or _ENV.get(key) or default


# 秘密情報
ANTHROPIC_API_KEY = get("ANTHROPIC_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = get("LINE_CHANNEL_SECRET")
LINE_GROUP_ID = get("LINE_GROUP_ID")

# 呼び名（LINEの文面に使う）
PARENT_NAME = get("KOE_PARENT_NAME", "お母さん")

# 文字起こし（U14：small か base かは実測で決める）
WHISPER_MODEL = get("KOE_WHISPER_MODEL", "small")
MIN_SPEECH_SECONDS = 1.0  # これより短い録音は送らない（仕様書 3.1）

# Claude（U10b：役割ごとのモデルは料金と速さを実測して決める）
SUMMARY_MODEL = get("KOE_SUMMARY_MODEL", "claude-opus-5-5")
SUMMARY_EFFORT = get("KOE_SUMMARY_EFFORT", "medium")
URGENCY_MODEL = get("KOE_URGENCY_MODEL", "claude-opus-5-5")
URGENCY_EFFORT = get("KOE_URGENCY_EFFORT", "medium")

# 貯め置き
OUTBOX_PATH = DATA_DIR / "outbox.sqlite3"
