"""家族からのお便り（仕様書 3.3）：受け取る → 言い換える → 未再生として貯める → 再生したら印をつける。

受け取った直後は LINE と Claude に問い合わせず、すぐ 200 を返せるよう貯めるだけにする。
言い換えや写真の取り出しは prepare() で行い、通信が切れていれば次の回に続きから進める。
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path

from app import clock, config, line_client
from app.agents import photo, rewrite
from app.agents.base import TemporaryError

log = logging.getLogger(__name__)

DEFAULT_SENDER = "ご家族"


def _now_iso(now=None):
    return (now or datetime.now()).isoformat(timespec="seconds")


def add_event(db, *, event_id, kind, sender_id=None, sender_name=None, text=None,
              line_message_id=None, now=None):
    """受け取ったお便りを貯める。同じ event_id が来たら（LINE の再送）何もせず None。"""
    cur = db.execute(
        """INSERT OR IGNORE INTO letters
           (event_id, received_at, sender_id, sender_name, kind, original, line_message_id, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'received')""",
        (event_id, _now_iso(now), sender_id, sender_name, kind, text, line_message_id),
    )
    db.commit()
    return cur.lastrowid if cur.rowcount == 1 else None


def _sender_name(row):
    if row["sender_name"]:
        name = row["sender_name"]
    elif row["sender_id"]:
        try:
            name = line_client.group_member_name(row["sender_id"]) or DEFAULT_SENDER
        except line_client.PermanentError as e:
            log.warning("送り主の名前が取れません: %s", e)
            name = DEFAULT_SENDER
    else:
        name = DEFAULT_SENDER
    return config.SENDER_NAMES.get(name, name)


def _save_photo(row):
    data, content_type = line_client.get_content(row["line_message_id"])
    config.PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    ext = ".png" if "png" in content_type else ".jpg"
    path = config.PHOTO_DIR / f"{row['id']}{ext}"
    path.write_bytes(data)
    return path, data, content_type


def prepare(db, on_ready=None):
    """受け取ったままのお便りを、再生できる形にする。できた件数を返す。

    通信が切れているとき（LINE・Claude）は、そこで止めて次の回に回す。
    """
    done = 0
    for row in db.execute("SELECT * FROM letters WHERE status = 'received' ORDER BY id").fetchall():
        try:
            name = _sender_name(row)
            if row["kind"] == "image":
                if row["image_path"]:
                    path = row["image_path"]
                    data = open(path, "rb").read()
                    content_type = "image/png" if path.endswith(".png") else "image/jpeg"
                else:
                    path, data, content_type = _save_photo(row)
                    db.execute("UPDATE letters SET image_path = ? WHERE id = ?", (str(path), row["id"]))
                    db.commit()
                caption = photo.run(data, content_type, name)
                spoken, display = caption, caption
            else:
                spoken, display = rewrite.run(row["original"] or "", name)
        except (TemporaryError, line_client.TemporaryError) as e:
            log.warning("お便りの準備ができません（あとで再試行）: %s", e)
            db.execute("UPDATE letters SET attempts = attempts + 1, last_error = ? WHERE id = ?", (str(e), row["id"]))
            db.commit()
            break
        except line_client.PermanentError as e:
            # 写真が取り出せない（期限切れなど）。お便りが来たことだけは伝える
            log.error("写真を取り出せません: %s", e)
            spoken = display = "写真が届きましたが、取り出せませんでした。"

        db.execute(
            "UPDATE letters SET sender_name = ?, spoken = ?, display = ?, status = 'ready' WHERE id = ?",
            (name, spoken, display, row["id"]),
        )
        db.commit()
        done += 1
        if on_ready:
            on_ready(get(db, row["id"]))
    return done


def get(db, letter_id):
    return db.execute("SELECT * FROM letters WHERE id = ?", (letter_id,)).fetchone()


def unplayed(db):
    return db.execute("SELECT * FROM letters WHERE status = 'ready' ORDER BY id").fetchall()


def recent_played(db, limit=5):
    return db.execute(
        "SELECT * FROM letters WHERE status = 'played' ORDER BY played_at DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()


def mark_played(db, letter_id, now=None):
    db.execute("UPDATE letters SET status = 'played', played_at = ? WHERE id = ?", (_now_iso(now), letter_id))
    db.commit()


def due_reminders(db, now=None):
    """昼の時間で数えて UNPLAYED_REMIND_HOURS 以上、未再生のままのお便り（1件につき1回だけ）。"""
    now = now or datetime.now()
    rows = db.execute("SELECT * FROM letters WHERE status = 'ready' AND reminded = 0 ORDER BY id").fetchall()
    limit = config.UNPLAYED_REMIND_HOURS * 3600
    return [r for r in rows if clock.daytime_seconds(datetime.fromisoformat(r["received_at"]), now) >= limit]


def mark_reminded(db, letter_id):
    db.execute("UPDATE letters SET reminded = 1 WHERE id = ?", (letter_id,))
    db.commit()


def intro(letter, index, total):
    """再生の前置き。誰からかを最初に言う（仕様書 3.3）。"""
    name = letter["sender_name"] or DEFAULT_SENDER
    head = f"{index}件目です。" if total > 1 else ""
    if letter["kind"] == "image":
        return f"{head}{name}さんから、写真が届いています。"
    return f"{head}{name}さんから、お便りです。"


def purge(db, now=None):
    """30日より古いお便りと写真を消す（仕様書 10章）。"""
    cutoff = ((now or datetime.now()) - timedelta(days=config.RETENTION_DAYS)).isoformat(timespec="seconds")
    old = db.execute("SELECT id, image_path FROM letters WHERE received_at < ?", (cutoff,)).fetchall()
    for row in old:
        if row["image_path"]:
            try:
                Path(row["image_path"]).unlink(missing_ok=True)
            except OSError as e:
                log.warning("写真を消せません: %s", e)
    db.execute("DELETE FROM letters WHERE received_at < ?", (cutoff,))
    db.commit()
    return len(old)
