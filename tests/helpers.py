"""試験の共通部分：データの置き場所を一時フォルダに替える。"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import config, db as koe_db


class TempDataTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        patches = [
            mock.patch.object(config, "KOE_DB_PATH", tmp / "koe.sqlite3"),
            mock.patch.object(config, "OUTBOX_PATH", tmp / "outbox.sqlite3"),
            mock.patch.object(config, "PHOTO_DIR", tmp / "photos"),
            mock.patch.object(config, "PARENT_NAME", "お母さん"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.tmp = tmp
        self.conn = koe_db.connect()
        self.addCleanup(self.conn.close)
        self.addCleanup(self._tmp.cleanup)


def letter(conn, text="元気？", sender="たつや", kind="text", event_id=None, now=None, spoken=None):
    """未再生（ready）のお便りを1件作る。"""
    from app import inbox

    letter_id = inbox.add_event(conn, event_id=event_id or f"ev-{text}-{now}", kind=kind,
                                sender_name=sender, text=text, now=now)
    conn.execute("UPDATE letters SET spoken = ?, display = ?, status = 'ready' WHERE id = ?",
                 (spoken or text, text, letter_id))
    conn.commit()
    return letter_id
