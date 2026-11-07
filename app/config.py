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

# ---- ここから 11/7 追加（Macで作成。Piで実機確認する） ----

def _flag(key, default):
    return get(key, default) in ("1", "true", "yes", "on")


def _names(raw):
    """「LINEの表示名=呼び名,表示名=呼び名」を辞書にする。"""
    names = {}
    for pair in raw.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            names[k.strip()] = v.strip()
    return names


# 試しのとき（1）は LINE に送らず、画面に出すだけ
DRY_RUN = _flag("KOE_DRY_RUN", "0")

# 新しく足した記録（お便り・知らせ・人感センサー）はひとつのファイルに置く
KOE_DB_PATH = DATA_DIR / "koe.sqlite3"
PHOTO_DIR = DATA_DIR / "photos"
RETENTION_DAYS = 30  # 仕様書 10章

# 家族からのお便り（仕様書 3.3）
WEBHOOK_PORT = int(get("KOE_WEBHOOK_PORT", "8000"))  # Cloudflare Quick Tunnel の先（11/7 に Funnel から替えた）
DISPLAY_PORT = int(get("KOE_DISPLAY_PORT", "8001"))  # モニター用。127.0.0.1 だけで待つ（外に出さない）
# 11/7：受け口は Cloudflare Quick Tunnel。cloudflared の窓口から今の URL を聞いて LINE に登録する（app/tunnel.py）
TUNNEL_METRICS_URL = get("KOE_TUNNEL_METRICS_URL", "http://127.0.0.1:20241")
# U24 仮決め：mention＝公式アカウントにメンションした文だけ読む / all＝グループの文を全部読む。写真はどちらでも受ける
REPLY_FILTER = get("KOE_REPLY_FILTER", "mention")
SENDER_NAMES = _names(get("KOE_SENDER_NAMES", ""))  # 例：Tatsuya T=たつや
REWRITE_MODEL = get("KOE_REWRITE_MODEL", "claude-opus-5-5")
REWRITE_EFFORT = get("KOE_REWRITE_EFFORT", "medium")
PHOTO_MODEL = get("KOE_PHOTO_MODEL", "claude-opus-5-5")
PHOTO_EFFORT = get("KOE_PHOTO_EFFORT", "low")
DAILY_MODEL = get("KOE_DAILY_MODEL", "claude-opus-5-5")
DAILY_EFFORT = get("KOE_DAILY_EFFORT", "medium")

# 昼と夜（仕様書 3.3.2・3.5）
DAY_START_HOUR = int(get("KOE_DAY_START_HOUR", "7"))
DAY_END_HOUR = int(get("KOE_DAY_END_HOUR", "22"))
UNPLAYED_REMIND_HOURS = float(get("KOE_UNPLAYED_REMIND_HOURS", "3"))
NOTIFY_PLAYED = _flag("KOE_NOTIFY_PLAYED", "1")      # 「聞きました」を送るか（U28：通数が多ければ 0）
NOTIFY_UNPLAYED = _flag("KOE_NOTIFY_UNPLAYED", "1")  # 「まだ聞いていません」を送るか

# 見守り（仕様書 3.5・3.6）
WATCH_ENABLED = _flag("KOE_WATCH_ENABLED", "1")
NO_MOTION_HOURS = float(get("KOE_NO_MOTION_HOURS", "4"))
MORNING_DEADLINE_HOUR = int(get("KOE_MORNING_DEADLINE_HOUR", "10"))
MORNING_FROM_HOUR = int(get("KOE_MORNING_FROM_HOUR", "5"))  # これより後の動きを「朝の動き」とする
DAILY_REPORT_HOUR = int(get("KOE_DAILY_REPORT_HOUR", "20"))

# 受話器（仕様書 3.1・3.3.1）
RECORD_LIMIT_SECONDS = 60
RECORD_WARN_SECONDS = 50
HOOK_GPIO = int(get("KOE_HOOK_GPIO", "17"))
SENSOR_GPIO = int(get("KOE_SENSOR_GPIO", "27"))
# フックスイッチが「押された（GNDにつながった）」ときに受話器が上がっている配線なら 1。
# 押しボタン版（押す＝取る）は 1。黒電話は配線しだいなので実機で確かめる
HOOK_PRESSED_IS_OFF_HOOK = _flag("KOE_HOOK_PRESSED_IS_OFF_HOOK", "1")
HOOK_SETTLE_SECONDS = 0.3  # 受話器を置いたと決めるまでの時間（置き直しで切れないように）

# 音（ALSA のデバイス名。`aplay -l` / `arecord -l` で調べて .env に書く）
HANDSET_PLAY_DEVICE = get("KOE_HANDSET_PLAY_DEVICE", "default")
HANDSET_REC_DEVICE = get("KOE_HANDSET_REC_DEVICE", "default")
SPEAKER_DEVICE = get("KOE_SPEAKER_DEVICE", "default")
JTALK_DIC = get("KOE_JTALK_DIC", "/var/lib/mecab/dic/open-jtalk/naist-jdic")
JTALK_VOICE = get("KOE_JTALK_VOICE", "/usr/share/hts-voice/nitech-jp-atr503-m001/nitech_jp_atr503_m001.htsvoice")
JTALK_SPEED = get("KOE_JTALK_SPEED", "0.9")
