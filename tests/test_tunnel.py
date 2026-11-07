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
