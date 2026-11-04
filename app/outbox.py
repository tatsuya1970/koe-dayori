"""送れなかったものを貯めて、つながったら順に送る（仕様書 7章）。

1件の流れ： transcribed（文字にした）→ ready（Claudeで文面ができた）→ sent（送った）
通信が切れていれば、その段階で止まり、次に flush() したときに続きから進む。
録音そのものは貯めない（文字にしたら消す）。
"""

import logging
import sqlite3
import uuid
from datetime import datetime

from app import agents, config, line_client
from app.message import compose

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    transcript  TEXT NOT NULL,
    unclear     INTEGER NOT NULL DEFAULT 0,
    summary     TEXT,
    urgency     TEXT,
    reason      TEXT,
    message     TEXT,
    retry_key   TEXT NOT NULL,
    status      TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    sent_at     TEXT
);
"""


def connect(path=None):
    path = path or config.OUTBOX_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")   # 電源が落ちても壊れにくくする（仕様書 11章）
    db.execute("PRAGMA synchronous=NORMAL")
    db.executescript(SCHEMA)
    return db


def add(db, transcript_text, unclear=False):
    cur = db.execute(
        "INSERT INTO jobs (created_at, transcript, unclear, retry_key, status) VALUES (?, ?, ?, ?, 'transcribed')",
        (datetime.now().isoformat(timespec="seconds"), transcript_text, int(unclear), str(uuid.uuid4())),
    )
    db.commit()
    return cur.lastrowid


def _fail(db, job_id, error):
    db.execute("UPDATE jobs SET attempts = attempts + 1, last_error = ? WHERE id = ?", (error, job_id))
    db.commit()


def flush(db, dry_run=False):
    """貯まっているものを古い順に進める。送れた件数と、残った件数を返す。

    dry_run のときは LINE に送らず、文面を表示するだけ。
    """
    sent = 0
    rows = db.execute("SELECT * FROM jobs WHERE status IN ('transcribed', 'ready') ORDER BY id").fetchall()
    for row in rows:
        job_id = row["id"]

        if row["status"] == "transcribed":
            try:
                summary, urgency = agents.analyze(row["transcript"], bool(row["unclear"]))
            except agents.TemporaryError as e:
                log.warning("Claude に届きません（あとで再試行）: %s", e)
                _fail(db, job_id, str(e))
                break  # 通信が切れているなら、残りも同じなので止める
            created = datetime.fromisoformat(row["created_at"])
            message = compose(created, summary, urgency, row["transcript"], bool(row["unclear"]))
            db.execute(
                "UPDATE jobs SET summary = ?, urgency = ?, reason = ?, message = ?, status = 'ready' WHERE id = ?",
                (summary, urgency.level, urgency.reason, message, job_id),
            )
            db.commit()
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

        if dry_run:
            print("----- LINE に送る文面（試し：送っていません）-----")
            print(row["message"])
            print("-----------------------------------------------")
            continue

        try:
            line_client.push_text(row["message"], row["retry_key"])
        except line_client.TemporaryError as e:
            log.warning("LINE に届きません（あとで再試行）: %s", e)
            _fail(db, job_id, str(e))
            break
        except line_client.PermanentError as e:
            log.error("LINE に送れません（設定を確認）: %s", e)
            _fail(db, job_id, str(e))
            break
        db.execute(
            "UPDATE jobs SET status = 'sent', sent_at = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), job_id),
        )
        db.commit()
        sent += 1

    remaining = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('transcribed', 'ready')").fetchone()[0]
    return sent, remaining
