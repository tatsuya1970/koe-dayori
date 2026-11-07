"""家族の LINE グループへの知らせを貯めて、つながったら送る。

親の言葉（outbox）以外の知らせはすべてここを通す。送った数もここで数える（U28：無料枠の見積もり）。
"""

import logging
import uuid
from datetime import datetime

from app import config, line_client

log = logging.getLogger(__name__)


def add(db, kind, text, now=None):
    now = now or datetime.now()
    db.execute(
        "INSERT INTO notices (created_at, kind, text, retry_key, status) VALUES (?, ?, ?, ?, 'queued')",
        (now.isoformat(timespec="seconds"), kind, text, str(uuid.uuid4())),
    )
    db.commit()


def flush(db, dry_run=None):
    dry_run = config.DRY_RUN if dry_run is None else dry_run
    sent = 0
    for row in db.execute("SELECT * FROM notices WHERE status = 'queued' ORDER BY id").fetchall():
        if dry_run:
            print(f"----- LINE への知らせ（試し：送っていません）[{row['kind']}] -----")
            print(row["text"])
            print("-----------------------------------------------")
        else:
            try:
                line_client.push_text(row["text"], row["retry_key"])
            except (line_client.TemporaryError, line_client.PermanentError) as e:
                log.warning("知らせを送れません（あとで再試行）: %s", e)
                db.execute("UPDATE notices SET attempts = attempts + 1, last_error = ? WHERE id = ?", (str(e), row["id"]))
                db.commit()
                break
        db.execute(
            "UPDATE notices SET status = 'sent', sent_at = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), row["id"]),
        )
        db.commit()
        sent += 1
    return sent


def count_by_kind(db, since):
    """since 以降に送った知らせの数を種類ごとに。"""
    rows = db.execute(
        "SELECT kind, COUNT(*) AS n FROM notices WHERE status = 'sent' AND sent_at >= ? GROUP BY kind",
        (since.isoformat(timespec="seconds"),),
    ).fetchall()
    return {r["kind"]: r["n"] for r in rows}
