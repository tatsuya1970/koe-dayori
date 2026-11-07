import threading
from unittest import mock

from app import config, inbox, pipeline
from app.phone import Phone
from tests.helpers import TempDataTestCase, letter


class FakeAudio:
    """受話器と本体スピーカーの偽物。hang_on に含まれる文を読むところで受話器を置いたことにする。"""

    def __init__(self, hang_on=None):
        self.log = []
        self.hang_on = hang_on
        self.recording = threading.Event()
        self.spoke = threading.Event()

    def say_handset(self, text, stop):
        self.log.append(("handset", text))
        if self.hang_on and self.hang_on in text:
            stop.set()
            return False
        return not stop.is_set()

    def beep_handset(self, stop=None):
        self.log.append(("beep",))
        return True

    def record(self, path, stop, limit, warn_at=None, on_warn=None):
        self.log.append(("record",))
        path.write_bytes(b"RIFF")
        self.recording.set()
        stop.wait(5)
        return "hangup"

    def say_speaker(self, text):
        self.log.append(("speaker", text))
        self.spoke.set()

    def chime_speaker(self):
        self.log.append(("chime",))


class PhoneTest(TempDataTestCase):
    def make(self, audio, status="sent"):
        self.sent = []
        self.played = []

        def send(wav):
            self.sent.append(wav)
            return pipeline.Result(status)

        return Phone(audio, send=send, on_played=self.played.append)

    def talk(self, phone, audio):
        phone.lift()
        self.assertTrue(audio.recording.wait(2), "録音が始まらない")
        phone.hang_up()
        phone.wait_idle(2)

    def test_no_letters_then_send(self):
        audio = FakeAudio()
        phone = self.make(audio)
        self.talk(phone, audio)
        self.assertEqual(audio.log, [("beep",), ("record",), ("speaker", "送りました")])
        self.assertEqual(len(self.sent), 1)

    def test_queued_message(self):
        audio = FakeAudio()
        phone = self.make(audio, status="queued")
        self.talk(phone, audio)
        self.assertIn(("speaker", "送れませんでした。あとでもう一度送ります"), audio.log)

    def test_empty_recording_says_nothing(self):
        audio = FakeAudio()
        phone = self.make(audio, status="empty")
        self.talk(phone, audio)
        self.assertFalse([e for e in audio.log if e[0] == "speaker"])

    def test_plays_letters_in_order_then_records(self):
        letter(self.conn, "元気にしてる？", sender="たつや")
        letter(self.conn, "週末に行くね", sender="ゆうた")
        audio = FakeAudio()
        phone = self.make(audio)
        self.talk(phone, audio)
        said = [e[1] for e in audio.log if e[0] == "handset"]
        self.assertEqual(said, [
            "お便りが2件あります。",
            "1件目です。たつやさんから、お便りです。元気にしてる？",
            "2件目です。ゆうたさんから、お便りです。週末に行くね",
        ])
        self.assertEqual(inbox.unplayed(self.conn), [])
        self.assertEqual([p["sender_name"] for p in self.played], ["たつや", "ゆうた"])
        self.assertEqual(audio.log[-3:], [("beep",), ("record",), ("speaker", "送りました")])

    def test_hang_up_during_second_letter(self):
        letter(self.conn, "元気にしてる？", sender="たつや")
        letter(self.conn, "週末に行くね", sender="ゆうた")
        audio = FakeAudio(hang_on="週末")
        phone = self.make(audio)
        phone.lift()
        phone.wait_idle(2)
        left = inbox.unplayed(self.conn)
        self.assertEqual([r["sender_name"] for r in left], ["ゆうた"])  # 途中のものは未再生のまま
        self.assertNotIn(("record",), audio.log)  # 録音しない
        self.assertEqual(self.sent, [])

    def test_send_crash_does_not_promise_retry(self):
        audio = FakeAudio()
        phone = Phone(audio, send=mock.Mock(side_effect=RuntimeError("whisper")))
        self.talk(phone, audio)
        spoken = [e[1] for e in audio.log if e[0] == "speaker"]
        self.assertEqual(len(spoken), 1)
        self.assertNotIn("あとでもう一度送ります", spoken[0])

    def test_lift_again_while_sending(self):
        """送り係が文字起こし中でも、次の受話器はすぐ使える。"""
        audio = FakeAudio()
        release = threading.Event()

        def slow_send(wav):
            release.wait(5)
            return pipeline.Result("sent")

        phone = Phone(audio, send=slow_send)
        phone.lift()
        audio.recording.wait(2)
        phone.hang_up()
        phone._session.join(2)
        audio.recording.clear()
        phone.lift()
        self.assertTrue(audio.recording.wait(2), "文字起こし中に次の録音が始まらない")
        phone.hang_up()
        release.set()
        phone.wait_idle(2)
        self.assertEqual(audio.log.count(("record",)), 2)


class LimitTest(TempDataTestCase):
    def test_beep_at_limit(self):
        audio = FakeAudio()
        audio.record = lambda path, stop, limit, warn_at=None, on_warn=None: (path.write_bytes(b""), "limit")[1]
        phone = Phone(audio, send=lambda wav: pipeline.Result("sent"))
        with mock.patch.object(config, "RECORD_LIMIT_SECONDS", 1):
            phone.lift()
            phone._session.join(2)
            phone.wait_idle(2)
        self.assertEqual([e for e in audio.log if e == ("beep",)], [("beep",), ("beep",)])
