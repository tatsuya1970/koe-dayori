import wave
from datetime import datetime
from unittest import mock

from app import config, notify, sounds, tts
from app.agents import photo
from tests.helpers import TempDataTestCase


class TtsTest(TempDataTestCase):
    def test_split(self):
        self.assertEqual(tts.split_sentences("元気？\n週末に行くね。待っててね！"),
                         ["元気？", "週末に行くね。", "待っててね！"])

    def test_long_sentence_is_cut(self):
        chunks = tts.split_sentences("あ" * 50 + "、" + "い" * 50)
        self.assertTrue(all(len(c) <= tts.MAX_CHARS for c in chunks))
        self.assertEqual("".join(chunks), "あ" * 50 + "、" + "い" * 50)


class SoundsTest(TempDataTestCase):
    def test_wav_format(self):
        for make in (sounds.beep, sounds.chime):
            path = make(self.tmp / f"{make.__name__}.wav")
            with wave.open(str(path)) as w:
                self.assertEqual((w.getframerate(), w.getnchannels()), (16000, 1))
                self.assertGreater(w.getnframes(), 0)


class PhotoTest(TempDataTestCase):
    def test_unsupported_or_too_big_skips_claude(self):
        with mock.patch("app.agents.photo.ask_json") as ask:
            self.assertEqual(photo.run(b"x", "image/heic", "たつや"), photo.FALLBACK)
            self.assertEqual(photo.run(b"x" * (photo.MAX_BYTES + 1), "image/jpeg", "たつや"), photo.FALLBACK)
        ask.assert_not_called()

    def test_sends_image_block(self):
        with mock.patch("app.agents.photo.ask_json", return_value={"caption": "猫の写真です。"}) as ask:
            self.assertEqual(photo.run(b"\xff\xd8", "image/jpeg; charset=binary", "たつや"), "猫の写真です。")
        content = ask.call_args.kwargs["user_text"]
        self.assertEqual(content[0]["source"]["media_type"], "image/jpeg")


class NotifyTest(TempDataTestCase):
    def test_retry_after_offline(self):
        from app import line_client
        notify.add(self.conn, "played", "聞きました")
        notify.add(self.conn, "daily", "今日の様子")
        with mock.patch.object(config, "DRY_RUN", False), \
             mock.patch("app.line_client.push_text", side_effect=line_client.TemporaryError("offline")):
            self.assertEqual(notify.flush(self.conn), 0)
        with mock.patch.object(config, "DRY_RUN", False), mock.patch("app.line_client.push_text") as push:
            self.assertEqual(notify.flush(self.conn), 2)
        self.assertEqual([c.args[0] for c in push.call_args_list], ["聞きました", "今日の様子"])
        self.assertEqual(notify.count_by_kind(self.conn, datetime(2000, 1, 1)), {"played": 1, "daily": 1})


class SttModelTest(TempDataTestCase):
    def setUp(self):
        super().setUp()
        from app import stt
        p = mock.patch.object(stt, "_model", None)
        p.start()
        self.addCleanup(p.stop)

    def test_uses_local_model_without_asking_hub(self):
        from app import stt
        with mock.patch("faster_whisper.WhisperModel") as model:
            stt._get_model()
            stt._get_model()  # 2回目は読み込まない
        model.assert_called_once()
        self.assertTrue(model.call_args.kwargs["local_files_only"])

    def test_downloads_when_not_cached(self):
        from app import stt
        with mock.patch("faster_whisper.WhisperModel", side_effect=[RuntimeError("not cached"), "m"]) as model:
            self.assertEqual(stt._get_model(), "m")
        self.assertEqual(model.call_count, 2)
        self.assertNotIn("local_files_only", model.call_args.kwargs)


class ResentTest(TempDataTestCase):
    """貯めていた分をあとから送れたら「送りました」と知らせる（夜は声を出さない）。"""

    def _keeper(self):
        from app.main import Housekeeper
        audio = mock.Mock()
        return Housekeeper(audio), audio

    def test_says_sent_in_daytime(self):
        from app import display
        keeper, audio = self._keeper()
        with mock.patch.object(display.STATE, "show_sent") as show:
            keeper.on_resent(1, datetime(2026, 11, 20, 20, 53))
        show.assert_called_once()
        audio.say_speaker.assert_called_once_with("送りました")

    def test_silent_at_night(self):
        from app import display
        keeper, audio = self._keeper()
        with mock.patch.object(display.STATE, "show_sent") as show:
            keeper.on_resent(1, datetime(2026, 11, 20, 23, 30))
        show.assert_called_once()
        audio.say_speaker.assert_not_called()

    def test_run_once_announces_only_when_sent(self):
        from app import main
        keeper, _ = self._keeper()
        for sent, expected in ((0, 0), (2, 1)):
            with mock.patch.object(main.outbox, "flush", return_value=(sent, 0)), \
                 mock.patch.object(main.tunnel, "sync_webhook"), \
                 mock.patch.object(main.notify, "flush"), \
                 mock.patch.object(main.daily, "due", return_value=False), \
                 mock.patch.object(keeper, "on_resent") as on_resent:
                keeper.run_once(datetime(2026, 11, 20, 20, 53))
            self.assertEqual(on_resent.call_count, expected)


class RetryOnlyFlushTest(TempDataTestCase):
    """定期の送り直しは、話したばかりの1件に手を出さない（受話器を置いたあとの送信と重ならない）。"""

    def test_fresh_job_is_left_to_sender(self):
        from app import agents, outbox
        from app.agents.urgency import Urgency

        out = outbox.connect()
        self.addCleanup(out.close)
        outbox.add(out, "もしもし")  # いま話したばかり（失敗もしていない）
        with mock.patch.object(agents, "analyze", return_value=("あいさつ", Urgency("low", ""))) as analyze, \
             mock.patch("app.line_client.push_text") as push:
            self.assertEqual(outbox.flush(out, retry_only=True), (0, 1))
            analyze.assert_not_called()
            push.assert_not_called()

            # 一度失敗したものは、送り直しで送る
            out.execute("UPDATE jobs SET attempts = 1")
            out.commit()
            self.assertEqual(outbox.flush(out, retry_only=True), (1, 0))
            push.assert_called_once()

    def test_old_job_is_retried(self):
        from app import agents, outbox
        from app.agents.urgency import Urgency

        out = outbox.connect()
        self.addCleanup(out.close)
        outbox.add(out, "もしもし")
        out.execute("UPDATE jobs SET created_at = '2026-11-20T20:00:00'")  # 送り係が落ちて残ったもの
        out.commit()
        with mock.patch.object(agents, "analyze", return_value=("あいさつ", Urgency("low", ""))), \
             mock.patch("app.line_client.push_text"):
            self.assertEqual(outbox.flush(out, retry_only=True), (1, 0))


class RecentSentTest(TempDataTestCase):
    def test_returns_latest_in_order(self):
        from app import outbox
        out = outbox.connect()
        self.addCleanup(out.close)
        for i, text in enumerate(["一つ目", "二つ目", "三つ目"]):
            job = outbox.add(out, text)
            out.execute("UPDATE jobs SET status = 'sent', sent_at = ? WHERE id = ?", (f"2026-11-20T21:0{i}:00", job))
        out.commit()
        self.assertEqual(outbox.recent_sent_transcripts(out, 2), ["二つ目", "三つ目"])

    def test_resent_shows_what_was_said(self):
        from app import display
        from app.main import Housekeeper
        keeper = Housekeeper(mock.Mock())
        with mock.patch.object(display.STATE, "show_sent") as show:
            keeper.on_resent(1, datetime(2026, 11, 20, 20, 53), ["もしもし、こんばんは"])
        show.assert_called_once_with("もしもし、こんばんは")
