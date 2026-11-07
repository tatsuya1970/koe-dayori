"""お便り・知らせ・人感センサーの記録を置く SQLite（data/koe.sqlite3）。

親の言葉を貯める outbox.sqlite3 とは分けてある（outbox はPiで先に作ったもの）。
スレッドごとに connect() して使う。
"""

import sqlite3

from app import config

SCHEMA = """
-- 家族からのお便り（仕様書 3.3）
-- status: received（受け取った）→ ready（言い換え済み・未再生）→ played（再生した）
CREATE TABLE IF NOT EXISTS letters (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id         TEXT UNIQUE,          -- LINE の webhookEventId（再送で二重にしない）
    received_at      TEXT NOT NULL,
    sender_id        TEXT,
    sender_name      TEXT,
    kind             TEXT NOT NULL,        -- text / image
    original         TEXT,                 -- 家族が書いた文
    line_message_id  TEXT,
    image_path       TEXT,
    spoken           TEXT,                 -- 読み上げる文
    display          TEXT,                 -- 画面に出す文
    status           TEXT NOT NULL,
    attempts         INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT,
    played_at        TEXT,
    reminded         INTEGER NOT NULL DEFAULT 0
);

-- 家族の LINE グループへの知らせ（聞きました・まだ聞いていません・見守り・今日の様子）
CREATE TABLE IF NOT EXISTS notices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    text        TEXT NOT NULL,
    retry_key   TEXT NOT NULL,
    status      TEXT NOT NULL,             -- queued / sent
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    sent_at     TEXT
);

-- 人感センサーが反応した時刻（仕様書 3.5）
CREATE TABLE IF NOT EXISTS motion (
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS motion_ts ON motion(ts);

-- 同じ知らせを二度送らないための印（kind と key の組で1回だけ）
CREATE TABLE IF NOT EXISTS sent_marks (
    kind     TEXT NOT NULL,
    key      TEXT NOT NULL,
    sent_at  TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);
"""


def connect(path=None):
    path = path or config.KOE_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")   # 電源が落ちても壊れにくくする（仕様書 11章）
    db.execute("PRAGMA synchronous=NORMAL")
    db.executescript(SCHEMA)
    return db


def mark_once(db, kind, key, now):
    """初めての (kind, key) なら印をつけて True。もう印があれば False。"""
    cur = db.execute(
        "INSERT OR IGNORE INTO sent_marks (kind, key, sent_at) VALUES (?, ?, ?)",
        (kind, key, now.isoformat(timespec="seconds")),
    )
    db.commit()
    return cur.rowcount == 1
