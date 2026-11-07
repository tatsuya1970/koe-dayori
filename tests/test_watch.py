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
