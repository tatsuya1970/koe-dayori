"""受話器の流れ（仕様書 3.1・3.3・3.3.1）。留守番電話と同じ方式。

受話器を取る
  → 未再生のお便りがあれば、古い順に受話器から読み上げる（画面にも本文と写真を出す）
  → 「ピー」→ 録音（上限60秒、50秒で「もうすぐ終わります」）
受話器を置く
  → 話していれば送る（文字起こし → Claude → LINE は送り係のスレッドで。次に受話器を取るのを待たせない）
  → 結果を本体のスピーカーで伝える（「送りました」「送れませんでした。あとでもう一度送ります」）

再生の途中で置いたら、そこまで最後まで聞いたお便りを「聞いた」とし、途中のものと残りは未再生のまま残す。
"""

import logging
import queue
import tempfile
import threading
from pathlib import Path

from app import config, db as koe_db, inbox

log = logging.getLogger(__name__)

RESULT_WORDS = {
    "sent": "送りました",
    "queued": "送れませんでした。あとでもう一度送ります",
}
# 文字起こしなどで止まったとき。録音は残っていないので「あとで送る」とは言わない
FAILED_WORDS = "うまく送れませんでした。お手数ですが、もう一度お話しください"
# 受話器の音が出せない・録れないとき（USB 音声アダプターが抜けた など）。本体のスピーカーから伝える
HANDSET_TROUBLE_WORDS = "受話器の調子がよくないようです。いったん置いて、もう一度取ってください"


class Phone:
    def __init__(self, audio, *, send, display_state=None, on_played=None, db_path=None):
        """
        audio         ：audio.Audio と同じ形のもの（Mac では偽物）
        send(wav)     ：録音を送り、pipeline.Result を返す
        display_state ：display.State（画面に「再生中」を出す）
        on_played     ：お便りを最後まで聞いたら呼ぶ（家族に「聞きました」を送る）
        """
        self.audio = audio
        self.send = send
        self.display = display_state
        self.on_played = on_played
        self.db_path = db_path
        self.hung_up = threading.Event()
        self.hung_up.set()  # 初めは置いてある
        self._session = None
        self._lock = threading.Lock()
        self._outgoing = queue.Queue()
        threading.Thread(target=self._sender, name="sender", daemon=True).start()

    # ---- 受話器のスイッチから呼ばれる ----

    def lift(self):
        with self._lock:
            if self._session and self._session.is_alive():
                # 置いた直後に取り直した：前の流れが止まりきるのを少し待つ
                self.hung_up.set()
                self._session.join(timeout=2)
            self.hung_up.clear()
            self._session = threading.Thread(target=self._run, name="session", daemon=True)
            self._session.start()

    def hang_up(self):
        self.hung_up.set()

    def wait_idle(self, timeout=None):
        """試験用：今の流れと、送り係の仕事が終わるまで待つ。"""
        if self._session:
            self._session.join(timeout)
        self._outgoing.join()

    # ---- 1回分の流れ ----

    def _run(self):
        conn = koe_db.connect(self.db_path)
        try:
            if not self._play_letters(conn):
                if not self.hung_up.is_set():  # 置いていないのに止まった＝受話器の音が出せない
                    self._handset_trouble()
                return
            if self.hung_up.is_set():
                return
            if not self.audio.beep_handset(self.hung_up):
                if not self.hung_up.is_set():
                    self._handset_trouble()
                return
            wav = Path(tempfile.mkdtemp(prefix="koe-rec-")) / "voice.wav"
            reason = self.audio.record(
                wav, self.hung_up,
                limit=config.RECORD_LIMIT_SECONDS,
                warn_at=config.RECORD_WARN_SECONDS,
                on_warn=lambda: self.audio.say_handset("もうすぐ終わります", self.hung_up),
            )
            if reason == "error" or not wav.exists():
                self._handset_trouble()  # 録れていないものは送らない
                return
            if reason == "limit":
                self.audio.beep_handset()
            self._outgoing.put(wav)
        except Exception:
            log.exception("受話器の流れで止まりました")
        finally:
            if self.display:
                self.display.clear_letter()
            conn.close()

    def _handset_trouble(self):
        log.error("受話器の音が出せないか、録音できません。USB 音声アダプターがつながっているか確かめてください")
        if self.display:
            self.display.show_notice("受話器の調子がよくありません")
        self.audio.say_speaker(HANDSET_TROUBLE_WORDS)

    def _play_letters(self, conn):
        """お便りを再生する。途中で受話器を置かれたら False。"""
        letters = inbox.unplayed(conn)
        total = len(letters)
        if total > 1 and not self.audio.say_handset(f"お便りが{total}件あります。", self.hung_up):
            return False
        for i, letter in enumerate(letters, 1):
            if self.display:
                self.display.show_letter(letter["id"])
            text = inbox.intro(letter, i, total) + (letter["spoken"] or "")
            if not self.audio.say_handset(text, self.hung_up):
                return False
            inbox.mark_played(conn, letter["id"])
            if self.on_played:
                try:
                    self.on_played(inbox.get(conn, letter["id"]))
                except Exception:
                    log.exception("「聞きました」の知らせを作れませんでした")
        if self.display:
            self.display.clear_letter()
        return True

    # ---- 送り係（文字起こしは時間がかかるので、受話器の流れとは別にする） ----

    def _sender(self):
        while True:
            wav = self._outgoing.get()
            try:
                result = self.send(wav)
                words = RESULT_WORDS.get(result.status)
                if words:
                    if self.display:
                        self.display.show_notice(words)
                    self.audio.say_speaker(words)
            except Exception:
                log.exception("送る途中で止まりました")
                self.audio.say_speaker(FAILED_WORDS)
            finally:
                self._outgoing.task_done()
