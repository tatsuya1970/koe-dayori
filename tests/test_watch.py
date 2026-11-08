from datetime import datetime
from unittest import mock

from app import clock, daily, display, inbox, outbox, sensor
from tests.helpers import TempDataTestCase, letter


def at(text):
    return datetime.fromisoformat(f"2026-11-{text}")


class ClockTest(TempDataTestCase):
    def test_daytime_seconds_skips_night(self):
        # 21:00 → 翌 9:30：昼は 21〜22時の1時間と 7〜9:30 の2.5時間
        self.assertEqual(clock.daytime_seconds(at("07T21:00"), at("08T09:30")), 3.5 * 3600)
        self.assertEqual(clock.daytime_seconds(at("07T23:00"), at("08T06:00")), 0)


class ReminderTest(TempDataTestCase):
    def test_counts_only_daytime(self):
        letter(self.conn, "おやすみ", now=at("07T21:00"))
        self.assertEqual(inbox.due_reminders(self.conn, at("08T08:30")), [])   # 1 + 1.5 時間
        self.assertEqual(len(inbox.due_reminders(self.conn, at("08T09:00"))), 1)  # 1 + 2 = 3 時間

    def test_once_per_letter(self):
        letter_id = letter(self.conn, "x", now=at("07T08:00"))
        inbox.mark_reminded(self.conn, letter_id)
        self.assertEqual(inbox.due_reminders(self.conn, at("07T15:00")), [])


class SensorTest(TempDataTestCase):
    def test_no_records_no_alert(self):
        self.assertEqual(sensor.check(self.conn, at("07T15:00")), [])

    def test_no_motion_four_hours_once(self):
        sensor.record_motion(self.conn, at("07T09:00"))
        self.assertEqual(sensor.check(self.conn, at("07T12:59")), [])
        alerts = sensor.check(self.conn, at("07T13:00"))
        self.assertEqual([k for k, _ in alerts], ["no_motion"])
        self.assertIn("今日の 9:00", alerts[0][1])
        self.assertEqual(sensor.check(self.conn, at("07T14:00")), [])  # 2回目は出さない
        sensor.record_motion(self.conn, at("07T14:30"))
        self.assertEqual([k for k, _ in sensor.check(self.conn, at("07T18:30"))], ["no_motion"])  # 新しい間

    def test_night_is_not_counted(self):
        sensor.record_motion(self.conn, at("07T21:30"))
        self.assertEqual(sensor.check(self.conn, at("08T06:30")), [])

    def test_morning_alert_without_double(self):
        sensor.record_motion(self.conn, at("07T20:00"))
        alerts = sensor.check(self.conn, at("08T10:00"))  # 昼の時間で 2 + 3 = 5時間たっている
        self.assertEqual([k for k, _ in alerts], ["morning"])
        self.assertIn("きのうの 20:00", alerts[0][1])
        self.assertEqual(sensor.check(self.conn, at("08T11:00")), [])

    def test_morning_ok_if_moved_early(self):
        sensor.record_motion(self.conn, at("08T06:10"))
        self.assertEqual(sensor.check(self.conn, at("08T10:00")), [])

    def test_record_throttled(self):
        self.assertTrue(sensor.record_motion(self.conn, at("07T09:00:00")))
        self.assertFalse(sensor.record_motion(self.conn, at("07T09:00:30")))
        self.assertTrue(sensor.record_motion(self.conn, at("07T09:01:00")))


class DailyTest(TempDataTestCase):
    def setUp(self):
        super().setUp()
        self.out = outbox.connect()
        self.addCleanup(self.out.close)
        for t in ("06T07:05", "06T12:00", "07T09:10", "07T15:00"):
            sensor.record_motion(self.conn, at(t))
        self.out.execute(
            "INSERT INTO jobs (created_at, transcript, summary, urgency, retry_key, status) VALUES (?, ?, ?, ?, ?, 'sent')",
            ("2026-11-07T10:00:00", "膝が痛い", "膝が痛いそうです", "high", "k"),
        )
        self.out.commit()

    def test_record_shape(self):
        rec = daily.day_record(self.conn, self.out, at("07T00:00").date())
        self.assertEqual(rec["motion"]["first"], "9:10")
        self.assertEqual(rec["talks"][0]["summary"], "膝が痛いそうです")

    def test_with_claude(self):
        with mock.patch("app.agents.daily_check.run", return_value=("今日は9時ごろ起きました。", ["起きるのが2時間遅い"])) as run:
            text = daily.build(self.conn, self.out, at("07T20:00"))
        today, history = run.call_args.args
        self.assertEqual(len(history), 1)  # 記録のある日だけ渡す
        self.assertIn("📋 今日のお母さんの様子（11/7（土））", text)
        self.assertIn("・起きるのが2時間遅い", text)

    def test_fallback_when_claude_unavailable(self):
        with mock.patch("app.agents.daily_check.run", return_value=None):
            text = daily.build(self.conn, self.out, at("07T20:00"))
        self.assertIn("朝最初に動いたのは 9:10 でした。", text)
        self.assertIn("10:00 膝が痛いそうです", text)


class DisplayTest(TempDataTestCase):
    def test_modes(self):
        state = display.build_state(self.conn, at("07T10:00"))
        self.assertEqual((state["mode"], state["night"]), ("idle", False))
        self.assertTrue(display.build_state(self.conn, at("07T23:00"))["night"])

        letter_id = letter(self.conn, "元気？", sender="たつや")
        state = display.build_state(self.conn, at("07T23:00"))
        self.assertEqual((state["mode"], state["night"]), ("new", False))  # 夜でも新着は明るく出す
        self.assertEqual(state["waiting"], {"count": 1, "senders": ["たつや"]})

        display.STATE.show_letter(letter_id)
        self.addCleanup(display.STATE.clear_letter)
        state = display.build_state(self.conn, at("07T10:00"))
        self.assertEqual(state["mode"], "playing")
        self.assertEqual(state["playing"]["text"], "元気？")


class SentStateTest(TempDataTestCase):
    def test_sent_hidden_while_playing(self):
        display.STATE.show_sent("要約です")
        self.addCleanup(lambda: display.STATE.show_sent(None, seconds=0))
        self.assertEqual(display.build_state(self.conn, at("07T10:00"))["sent"], "要約です")
        letter_id = letter(self.conn, "元気？")
        display.STATE.show_letter(letter_id)
        self.addCleanup(display.STATE.clear_letter)
        self.assertIsNone(display.build_state(self.conn, at("07T10:00"))["sent"])


class PipelineSummaryTest(TempDataTestCase):
    def test_returns_summary_sent_to_line(self):
        from app import agents, outbox, pipeline, stt
        from app.agents.urgency import Urgency

        transcript = stt.Transcript(text="膝が痛い", duration=3, speech_seconds=2, unclear=False, elapsed=1)
        out = outbox.connect()
        self.addCleanup(out.close)
        wav = self.tmp / "v.wav"
        wav.write_bytes(b"")
        with mock.patch.object(stt, "transcribe", return_value=transcript), \
             mock.patch.object(agents, "analyze", return_value=("膝が痛いそうです", Urgency("high", "体の痛み"))), \
             mock.patch("app.line_client.push_text"):
            result = pipeline.handle_recording(wav, out)
        self.assertEqual((result.status, result.summary), ("sent", "膝が痛いそうです"))
        self.assertIsInstance(result, pipeline.Result)


class ChatTest(TempDataTestCase):
    """モニターの LINE のような画面：親の言葉と家族のお便りを、時刻の順に並べる。"""

    def setUp(self):
        super().setUp()
        self.out = outbox.connect()
        self.addCleanup(self.out.close)

    def _job(self, created, text, status="sent", urgency="low"):
        self.out.execute(
            "INSERT INTO jobs (created_at, transcript, urgency, retry_key, status) VALUES (?, ?, ?, ?, ?)",
            (created, text, urgency, f"k-{created}", status),
        )
        self.out.commit()

    def test_merged_in_time_order(self):
        self._job("2026-11-07T09:00:00", "年末はいつ帰るの？")
        played = letter(self.conn, "29日に帰るよ", now=at("07T10:00"))
        inbox.mark_played(self.conn, played, now=at("07T11:00"))
        self._job("2026-11-07T12:00:00", "転んで腰を打った", status="ready", urgency="high")

        chat = display.build_state(self.conn, at("07T13:00"), out=self.out)["chat"]
        self.assertEqual([m["from"] for m in chat], ["parent", "family", "parent"])
        self.assertEqual(chat[1]["sender"], "たつや")
        self.assertEqual((chat[2]["urgent"], chat[2]["pending"]), (True, True))
        self.assertEqual((chat[0]["urgent"], chat[0]["pending"]), (False, False))

    def test_unplayed_letter_not_shown(self):
        """まだ聞いていないお便りは、先に画面に出さない（留守番電話の方式）。"""
        letter(self.conn, "元気？", now=at("07T10:00"))
        self.assertEqual(display.build_state(self.conn, at("07T10:05"), out=self.out)["chat"], [])

    def test_limit_keeps_newest(self):
        for i in range(12):
            self._job(f"2026-11-07T09:{i:02d}:00", f"話{i}")
        chat = display.build_chat(self.conn, self.out, limit=8)
        self.assertEqual([m["text"] for m in chat], [f"話{i}" for i in range(4, 12)])


class ServerIdTest(TempDataTestCase):
    def test_state_has_server_id(self):
        """ブラウザが「本番が起動し直した」と気づいて、ページを読み込み直すための印。"""
        self.assertEqual(display.build_state(self.conn, at("07T10:00"))["server"], display.SERVER_ID)
