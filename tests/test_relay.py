"""クラウド（Workers）から取りに行く（11/22 D29）。クラウドは偽物に差し替える。"""

import json

from app import config, inbox, relay
from tests.helpers import TempDataTestCase

GROUP = "Cgroup"


def event(eid, text="元気？", kind="text"):
    msg = {"type": kind, "id": f"m-{eid}"}
    if kind == "text":
        msg["text"] = f"@声だより {text}"
        msg["mention"] = {"mentionees": [{"index": 0, "length": 5, "isSelf": True}]}
    return {"type": "message", "webhookEventId": eid,
            "source": {"type": "group", "groupId": GROUP, "userId": "U1"}, "message": msg}


class FakeCloud:
    """Workers の代わり。ack されるまで消さない。"""

    def __init__(self, items, fail_on=None):
        self.items = list(items)
        self.acked = []
        self.fail_on = fail_on or set()  # この path で通信が切れる

    def request(self, path, method="GET", data=None, timeout=20):
        if path in self.fail_on:
            raise relay.RelayError("offline")
        if path == "/events":
            return json.dumps({"events": self.items[:20]}).encode(), "application/json"
        if path == "/ack":
            self.acked += data["ids"]
            self.items = [i for i in self.items if i["id"] not in data["ids"]]
            return b"{}", "application/json"
        raise AssertionError(path)


class RelayPullTest(TempDataTestCase):
    def setUp(self):
        super().setUp()
        from unittest import mock
        for p in (mock.patch.object(config, "LINE_GROUP_ID", GROUP),
                  mock.patch.object(config, "REPLY_FILTER", "mention")):
            p.start()
            self.addCleanup(p.stop)

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM letters").fetchone()[0]

    def test_pull_adds_and_acks(self):
        cloud = FakeCloud([{"id": "a", "event": event("a")}, {"id": "b", "event": event("b", "こんにちは")}])
        self.assertEqual(relay.pull(self.conn, request=cloud.request), 2)
        self.assertEqual(cloud.acked, ["a", "b"])
        self.assertEqual(cloud.items, [])
        texts = [r["original"] for r in self.conn.execute("SELECT original FROM letters ORDER BY id")]
        self.assertEqual(texts, ["元気？", "こんにちは"])

    def test_same_event_twice_is_one_letter(self):
        cloud = FakeCloud([{"id": "a", "event": event("a")}])
        relay.pull(self.conn, request=cloud.request)
        cloud.items = [{"id": "a2", "event": event("a")}]  # LINE の再送・ack の前に落ちたとき
        self.assertEqual(relay.pull(self.conn, request=cloud.request), 0)
        self.assertEqual(self.count(), 1)
        self.assertEqual(cloud.items, [])  # 二重のものも消してもらう

    def test_crash_before_sqlite_is_received_again(self):
        cloud = FakeCloud([{"id": "a", "event": event("a")}])
        from unittest import mock
        with mock.patch("app.inbox.add_event", side_effect=RuntimeError("停電")):
            with self.assertRaises(RuntimeError):
                relay.pull(self.conn, request=cloud.request)
        self.assertEqual(cloud.acked, [])  # 入れる前に落ちたので消していない
        self.assertEqual(relay.pull(self.conn, request=cloud.request), 1)
        self.assertEqual(self.count(), 1)

    def test_crash_after_sqlite_before_ack(self):
        cloud = FakeCloud([{"id": "a", "event": event("a")}], fail_on={"/ack"})
        with self.assertRaises(relay.RelayError):
            relay.pull(self.conn, request=cloud.request)
        self.assertEqual(self.count(), 1)
        cloud.fail_on = set()
        self.assertEqual(relay.pull(self.conn, request=cloud.request), 0)  # もう一度受けても1件のまま
        self.assertEqual(self.count(), 1)
        self.assertEqual(cloud.items, [])

    def test_filtered_events_are_acked_but_not_added(self):
        other = event("x")
        other["source"]["groupId"] = "Cother"
        no_mention = event("y")
        no_mention["message"].pop("mention")
        cloud = FakeCloud([{"id": "x", "event": other}, {"id": "y", "event": no_mention}])
        self.assertEqual(relay.pull(self.conn, request=cloud.request), 0)
        self.assertEqual(self.count(), 0)
        self.assertEqual(cloud.acked, ["x", "y"])

    def test_image_saved_before_ack(self):
        cloud = FakeCloud([{"id": "p", "event": event("p", kind="image"), "image": True}])
        path = self.tmp / "relay-p.jpg"

        def save(item):
            path.write_bytes(b"jpg")
            return path

        self.assertEqual(relay.pull(self.conn, request=cloud.request, save_image=save), 1)
        row = self.conn.execute("SELECT * FROM letters").fetchone()
        self.assertEqual(row["image_path"], str(path))
        self.assertEqual(row["line_message_id"], "m-p")  # 取り出せなかったときは LINE から取る

    def test_image_download_failure_keeps_it_in_cloud(self):
        cloud = FakeCloud([{"id": "a", "event": event("a")},
                           {"id": "p", "event": event("p", kind="image"), "image": True}])

        def save(item):
            raise relay.RelayError("offline")

        with self.assertRaises(relay.RelayError):
            relay.pull(self.conn, request=cloud.request, save_image=save)
        self.assertEqual(cloud.acked, ["a"])  # 写真の前までは入れて消してもらう
        self.assertEqual([i["id"] for i in cloud.items], ["p"])
        self.assertEqual(self.count(), 1)

    def test_more_than_one_page(self):
        cloud = FakeCloud([{"id": f"e{i}", "event": event(f"e{i}", f"文{i}")} for i in range(25)])
        self.assertEqual(relay.pull(self.conn, request=cloud.request), 25)
        self.assertEqual(cloud.items, [])


class RelayScheduleTest(TempDataTestCase):
    """いつ取りに行くか。通信が切れても落ちない。"""

    def setUp(self):
        super().setUp()
        self.calls = []
        from unittest import mock
        p = mock.patch("app.relay.pull", side_effect=self._pull)
        p.start()
        self.addCleanup(p.stop)
        self.fail = False

    def _pull(self, conn, now=None):
        self.calls.append(1)
        if self.fail:
            raise relay.RelayError("offline")
        return 0

    def test_pulls_at_start(self):
        r = relay.Relay()
        r.connected = True
        r.run_once(self.conn, now_mono=0)
        self.assertEqual(len(self.calls), 1)

    def test_connected_waits_for_notice(self):
        r = relay.Relay()
        r.connected = True
        r.run_once(self.conn, now_mono=0)
        for t in range(30, 600, 30):
            r.run_once(self.conn, now_mono=t)
        self.assertEqual(len(self.calls), 1)  # 知らせが来るまで聞かない（LTE の通信量を抑える）
        r.notify()
        r.run_once(self.conn, now_mono=630)
        self.assertEqual(len(self.calls), 2)

    def test_safety_poll_while_connected(self):
        r = relay.Relay()
        r.connected = True
        r.run_once(self.conn, now_mono=0)
        r.run_once(self.conn, now_mono=relay.SAFETY_POLL)
        self.assertEqual(len(self.calls), 2)

    def test_disconnected_polls_every_time(self):
        r = relay.Relay()
        r.connected = False
        for t in range(0, 90, 30):
            r.run_once(self.conn, now_mono=t)
        self.assertEqual(len(self.calls), 3)

    def test_offline_does_not_raise_and_retries(self):
        r = relay.Relay()
        r.connected = True
        self.fail = True
        self.assertEqual(r.run_once(self.conn, now_mono=0), 0)  # 落ちない
        self.fail = False
        r.run_once(self.conn, now_mono=30)  # つながったら続きから
        self.assertEqual(len(self.calls), 2)

    def test_notice_wakes_housekeeper(self):
        woke = []
        r = relay.Relay(on_received=lambda: woke.append(1))
        r._on_message(None, "new")
        self.assertEqual(woke, [1])
        r._on_message(None, "something else")
        self.assertEqual(woke, [1])


class RealRequestTest(TempDataTestCase):
    def test_unreachable_cloud_is_relay_error(self):
        from unittest import mock
        with mock.patch.object(config, "RELAY_URL", "http://127.0.0.1:1"):
            with self.assertRaises(relay.RelayError):
                relay._request("/events", timeout=2)


class HousekeeperModeTest(TempDataTestCase):
    def test_relay_mode_does_not_touch_tunnel(self):
        from unittest import mock
        from app import main

        class R:
            ran = 0

            def run_once(self, conn, now=None):
                R.ran += 1

        keeper = main.Housekeeper(audio=mock.Mock(), relay_client=R())
        with mock.patch("app.tunnel.sync_webhook") as sync, mock.patch("app.tunnel.watch") as watch:
            keeper.run_once()
        sync.assert_not_called()  # 呼ぶと LINE の Webhook URL が trycloudflare に書き戻される
        watch.assert_not_called()
        self.assertEqual(R.ran, 1)

    def test_tunnel_mode_still_syncs(self):
        from unittest import mock
        from app import main

        keeper = main.Housekeeper(audio=mock.Mock())
        with mock.patch("app.tunnel.sync_webhook") as sync, mock.patch("app.tunnel.watch") as watch:
            keeper.run_once()
        sync.assert_called_once()
        watch.assert_called_once()
