import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.request
from unittest import mock

from app import config, inbox, webhook
from tests.helpers import TempDataTestCase

GROUP = "Cgroup"
SECRET = "test-secret"


def text_event(text, mentionees=None, group=GROUP, event_id="ev1"):
    message = {"type": "text", "id": "m1", "text": text}
    if mentionees is not None:
        message["mention"] = {"mentionees": mentionees}
    return {"type": "message", "webhookEventId": event_id, "message": message,
            "source": {"type": "group", "groupId": group, "userId": "U1"}}


class ToLetterTest(TempDataTestCase):
    def test_mention_only(self):
        self.assertIsNone(webhook.to_letter(text_event("今日は寒いね"), GROUP, "mention"))
        ev = text_event("@声だより お母さん、元気？", [{"index": 0, "length": 5, "isSelf": True}])
        self.assertEqual(webhook.to_letter(ev, GROUP, "mention")["text"], "お母さん、元気？")

    def test_mention_to_someone_else_is_ignored(self):
        ev = text_event("@たつや 了解", [{"index": 0, "length": 4, "isSelf": False}])
        self.assertIsNone(webhook.to_letter(ev, GROUP, "mention"))

    def test_all_mode(self):
        self.assertEqual(webhook.to_letter(text_event("今日は寒いね"), GROUP, "all")["text"], "今日は寒いね")

    def test_other_group_ignored(self):
        self.assertIsNone(webhook.to_letter(text_event("x", group="Cother"), GROUP, "all"))

    def test_image_always_accepted(self):
        ev = {"type": "message", "webhookEventId": "e2", "message": {"type": "image", "id": "m9"},
              "source": {"type": "group", "groupId": GROUP, "userId": "U1"}}
        self.assertEqual(webhook.to_letter(ev, GROUP, "mention")["kind"], "image")

    def test_dev_sender_name(self):
        ev = text_event("x")
        ev["koeSenderName"] = "ゆうた"
        self.assertEqual(webhook.to_letter(ev, GROUP, "all")["sender_name"], "ゆうた")
        self.assertIsNone(webhook.to_letter(text_event("x"), GROUP, "all")["sender_name"])

    def test_strip_mentions_counts_utf16(self):
        # 絵文字は UTF-16 で2つ分。そのあとのメンションの位置がずれないこと
        text = "🎉@声だより おめでとう"
        self.assertEqual(webhook.strip_mentions(text, [{"index": 2, "length": 5}]), "🎉 おめでとう")


class ServerTest(TempDataTestCase):
    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(config, "LINE_CHANNEL_SECRET", SECRET),
                  mock.patch.object(config, "LINE_GROUP_ID", GROUP),
                  mock.patch.object(config, "REPLY_FILTER", "all")):
            p.start()
            self.addCleanup(p.stop)
        self.woken = []
        self.server = webhook.start(on_received=lambda: self.woken.append(1), port=0)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/callback"

    def post(self, payload, secret=SECRET):
        body = json.dumps(payload).encode("utf-8")
        sig = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers={"X-Line-Signature": sig, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_stores_letter_once(self):
        payload = {"events": [text_event("ただいま")]}
        self.assertEqual(self.post(payload), 200)
        self.assertEqual(self.post(payload), 200)  # LINE の再送
        rows = self.conn.execute("SELECT * FROM letters").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "received")
        self.assertEqual(len(self.woken), 1)

    def test_bad_signature(self):
        self.assertEqual(self.post({"events": [text_event("x")]}, secret="wrong"), 403)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM letters").fetchone()[0], 0)

    def test_verify_request_has_no_events(self):
        self.assertEqual(self.post({"events": []}), 200)

    def test_get_is_not_served(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.url)
        self.assertEqual(cm.exception.code, 404)


class PrepareTest(TempDataTestCase):
    def test_text_and_image(self):
        inbox.add_event(self.conn, event_id="a", kind="text", sender_id="U1", text="晩ごはん何食べた？")
        inbox.add_event(self.conn, event_id="b", kind="image", sender_id="U1", line_message_id="m9")
        ready = []
        with mock.patch("app.line_client.group_member_name", return_value="Tatsuya T"), \
             mock.patch.object(config, "SENDER_NAMES", {"Tatsuya T": "たつや"}), \
             mock.patch("app.line_client.get_content", return_value=(b"\xff\xd8jpeg", "image/jpeg")), \
             mock.patch("app.agents.rewrite.run", return_value=("晩ごはんは、何を食べましたか。", "晩ごはん何食べた？")), \
             mock.patch("app.agents.photo.run", return_value="男の子が公園で遊んでいる写真です。"):
            self.assertEqual(inbox.prepare(self.conn, on_ready=ready.append), 2)
        rows = inbox.unplayed(self.conn)
        self.assertEqual([r["sender_name"] for r in rows], ["たつや", "たつや"])
        self.assertEqual(rows[0]["spoken"], "晩ごはんは、何を食べましたか。")
        self.assertTrue((self.tmp / "photos" / f"{rows[1]['id']}.jpg").exists())
        self.assertEqual(len(ready), 2)

    def test_offline_keeps_for_later(self):
        from app.agents.base import TemporaryError
        inbox.add_event(self.conn, event_id="a", kind="text", sender_name="たつや", text="元気？")
        with mock.patch("app.agents.rewrite.run", side_effect=TemporaryError("offline")):
            self.assertEqual(inbox.prepare(self.conn), 0)
        row = self.conn.execute("SELECT * FROM letters").fetchone()
        self.assertEqual((row["status"], row["attempts"]), ("received", 1))
