import http.server
import json
import subprocess
import threading
import unittest
from datetime import datetime, timedelta
from unittest import mock

from app import line_client, tunnel
from tests.helpers import TempDataTestCase


class TunnelTest(TempDataTestCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(tunnel, "_registered", None)
        p.start()
        self.addCleanup(p.stop)

    def test_no_cloudflared_does_nothing(self):
        with mock.patch("app.tunnel.current_hostname", return_value=None), \
             mock.patch("app.tunnel._line_request") as req:
            self.assertIsNone(tunnel.sync_webhook())
        req.assert_not_called()

    def test_registers_new_url(self):
        calls = []

        def fake(method, data=None):
            calls.append((method, data))
            return {"endpoint": "https://old.trycloudflare.com/callback"} if method == "GET" else {}

        with mock.patch("app.tunnel.current_hostname", return_value="new.trycloudflare.com"), \
             mock.patch("app.tunnel._line_request", side_effect=fake):
            self.assertEqual(tunnel.sync_webhook(), "https://new.trycloudflare.com/callback")
            # 2回目は LINE に問い合わせない
            self.assertIsNone(tunnel.sync_webhook())
        self.assertEqual(calls, [("GET", None), ("PUT", {"endpoint": "https://new.trycloudflare.com/callback"})])

    def test_already_registered_is_not_rewritten(self):
        with mock.patch("app.tunnel.current_hostname", return_value="same.trycloudflare.com"), \
             mock.patch("app.tunnel._line_request",
                        return_value={"endpoint": "https://same.trycloudflare.com/callback"}) as req:
            self.assertIsNone(tunnel.sync_webhook())
        req.assert_called_once_with("GET")

    def test_url_change_after_restart(self):
        def fake(method, data=None):
            return {"endpoint": "https://a.trycloudflare.com/callback"} if method == "GET" else {}

        with mock.patch("app.tunnel._line_request", side_effect=fake):
            with mock.patch("app.tunnel.current_hostname", return_value="a.trycloudflare.com"):
                tunnel.sync_webhook()
            with mock.patch("app.tunnel.current_hostname", return_value="b.trycloudflare.com"):
                self.assertEqual(tunnel.sync_webhook(), "https://b.trycloudflare.com/callback")

    def test_line_failure_retries_next_time(self):
        with mock.patch("app.tunnel.current_hostname", return_value="x.trycloudflare.com"), \
             mock.patch("app.tunnel._line_request", side_effect=line_client.TemporaryError("offline")):
            with self.assertRaises(line_client.TemporaryError):
                tunnel.sync_webhook()
        self.assertIsNone(tunnel._registered)  # 登録済みにしない＝次の回にやり直す


class WatchdogTest(unittest.TestCase):
    """受け口が消えたまま直らないときだけ作り直す（11/22）。作り直しの処理は差し替える（本物は 429 になる）。"""

    T0 = datetime(2026, 11, 22, 12, 23, 15)

    def setUp(self):
        self.restarts = []
        self.dog = tunnel.Watchdog(restart=lambda: self.restarts.append(1))

    def at(self, minutes, state):
        return self.dog.check(self.T0 + timedelta(minutes=minutes), state)

    def test_short_outage_does_not_restart(self):
        # 12:23 の "edge closed" のように、すぐ戻るなら作り直さない
        self.at(0, False)
        self.at(0.5, False)
        self.at(1, True)
        self.at(4.5, False)  # 数え直し
        self.at(9, False)
        self.assertEqual(self.restarts, [])

    def test_restarts_after_five_minutes(self):
        for m in range(0, 5):
            self.assertFalse(self.at(m, False))
        self.assertTrue(self.at(5, False))
        self.assertEqual(len(self.restarts), 1)

    def test_waits_at_least_ten_minutes_between_restarts(self):
        self.at(0, False)
        self.at(5, False)  # 1回目
        for m in range(6, 15):
            self.at(m, False)
        self.assertEqual(len(self.restarts), 1)
        self.at(15, False)  # 10分たった
        self.assertEqual(len(self.restarts), 2)

    def test_gap_grows_while_still_broken(self):
        times = []
        for m in range(0, 200):
            if self.at(m, False):
                times.append(m)
        self.assertEqual(times, [5, 15, 35, 75, 135, 195])  # 10, 20, 40, 60, 60 分あける

    def test_no_answer_is_not_counted(self):
        # cloudflared が動いていない（Mac・429 で落ちて起こし直し中）ときは数えない
        for m in range(0, 30):
            self.at(m, None)
        self.assertEqual(self.restarts, [])
        self.assertIsNone(self.dog.bad_since)

    def test_recovery_resets_gap(self):
        for m in range(0, 16):
            self.at(m, False)  # 5 と 15 に作り直し。次は20分あけるはず
        self.at(16, True)
        self.assertEqual(self.dog.gap, tunnel.RESTART_MIN_GAP)
        # また消えたら：5分待ち、前の作り直しから10分たっていれば作り直す
        for m in range(30, 36):
            self.at(m, False)
        self.assertEqual(len(self.restarts), 3)

    def test_restart_failure_is_logged_not_raised(self):
        def boom():
            raise subprocess.CalledProcessError(1, "systemctl")
        dog = tunnel.Watchdog(restart=boom)
        dog.check(self.T0, False)
        self.assertTrue(dog.check(self.T0 + timedelta(minutes=5), False))


class ReadyTest(unittest.TestCase):
    def _serve(self, status, body):
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(status)
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_port}"

    def test_connected(self):
        url = self._serve(200, {"status": 200, "readyConnections": 1})
        self.assertTrue(tunnel.ready(url))

    def test_not_connected(self):
        url = self._serve(503, {"status": 503, "readyConnections": 0})
        self.assertFalse(tunnel.ready(url))

    def test_no_cloudflared(self):
        self.assertIsNone(tunnel.ready("http://127.0.0.1:1"))
