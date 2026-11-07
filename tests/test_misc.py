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
