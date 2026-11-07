"""受話器と本体スピーカーの音（Pi 上。aplay / arecord を使う）。

phone.py はこのクラスの形だけを使う。Mac で試すときは tools/dev_phone.py の偽物に差し替える。
受話器を置いたら（stop が立ったら）、再生も録音もすぐ止める。

※ 11/7 Mac で作成。デバイス名と arecord の止め方は Pi で確かめる。
"""

import logging
import signal
import subprocess
import threading
import time

from app import config, sounds, tts

log = logging.getLogger(__name__)


class Audio:
    def __init__(self, handset_play=None, handset_rec=None, speaker=None):
        self.handset_play = handset_play or config.HANDSET_PLAY_DEVICE
        self.handset_rec = handset_rec or config.HANDSET_REC_DEVICE
        self.speaker = speaker or config.SPEAKER_DEVICE
        self.beep_wav = sounds.beep()
        self.chime_wav = sounds.chime()
        self._speaker_lock = threading.Lock()

    # ---- 再生 ----

    def _play(self, wav, device, stop=None):
        """最後まで流れたら True。stop が立って途中で止めたら False。"""
        proc = subprocess.Popen(["aplay", "-q", "-D", device, str(wav)])
        while proc.poll() is None:
            if stop is not None and stop.wait(0.05):
                proc.terminate()
                proc.wait()
                return False
            if stop is None:
                time.sleep(0.05)
        return True

    def say_handset(self, text, stop):
        for wav in tts.synthesize(text):
            if stop.is_set() or not self._play(wav, self.handset_play, stop):
                return False
        return True

    def beep_handset(self, stop=None):
        return self._play(self.beep_wav, self.handset_play, stop)

    def say_speaker(self, text):
        with self._speaker_lock:
            for wav in tts.synthesize(text):
                self._play(wav, self.speaker)

    def chime_speaker(self):
        with self._speaker_lock:
            self._play(self.chime_wav, self.speaker)

    # ---- 録音 ----

    def record(self, path, stop, limit, warn_at=None, on_warn=None):
        """16kHz・モノラル・WAV で録る。受話器を置いたら 'hangup'、上限に達したら 'limit' を返す。"""
        proc = subprocess.Popen([
            "arecord", "-q", "-D", self.handset_rec,
            "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "wav", str(path),
        ])
        started = time.monotonic()
        warned = False
        reason = "limit"
        while proc.poll() is None:
            elapsed = time.monotonic() - started
            if stop.wait(0.05):
                reason = "hangup"
                break
            if warn_at and not warned and elapsed >= warn_at:
                warned = True
                if on_warn:
                    threading.Thread(target=on_warn, daemon=True).start()
            if elapsed >= limit:
                break
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)  # SIGINT なら arecord が WAV の見出しを書き直してから終わる
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        else:
            log.error("録音が止まりました（終了コード %s）。マイクのデバイス名を確かめてください", proc.returncode)
        return reason
