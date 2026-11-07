"""faster-whisper で、録音（WAV）を日本語の文字にする。"""

import logging
import threading
import time
from dataclasses import dataclass

from app import config

log = logging.getLogger(__name__)

_model = None
_lock = threading.Lock()  # 起動時の先読みと、送り係が同時に読み込まないように


@dataclass
class Transcript:
    text: str
    duration: float        # 録音の長さ（秒）
    speech_seconds: float  # 話していた長さ（無音を除く）
    unclear: bool          # 聞き取りにくかったところがある
    elapsed: float         # 文字起こしにかかった秒数


def _get_model():
    """モデルを読み込む（1回だけ）。

    11/7：手元にあるモデルを、Hugging Face に問い合わせずに使う。問い合わせると、
    この Wi-Fi（IPv6 が外に出ない）では数十秒〜5分以上待たされ、8秒の録音の文字起こしに 79.5 秒かかった。
    手元から読めば 1.8 秒。手元にないとき（初めて動かすとき）だけダウンロードする。
    """
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            started = time.monotonic()
            try:
                _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8",
                                      local_files_only=True)
            except Exception as e:
                log.info("手元に文字起こしのモデルがないので、ダウンロードします（%s）: %s", config.WHISPER_MODEL, e)
                _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
            log.info("文字起こしのモデルを読み込みました（%s、%.1f秒）", config.WHISPER_MODEL, time.monotonic() - started)
    return _model


def preload():
    """起動時に裏で読み込んでおき、最初に話した1回目を待たせない。"""
    threading.Thread(target=_get_model, name="stt-preload", daemon=True).start()


def transcribe(wav_path):
    started = time.monotonic()
    segments, info = _get_model().transcribe(
        str(wav_path),
        language="ja",
        vad_filter=True,  # 無音を飛ばす。何も話さず置いたときを見分けるのにも使う
        beam_size=5,
    )
    segments = list(segments)

    text = "".join(s.text for s in segments).strip()
    speech = sum(s.end - s.start for s in segments)
    # 自信のない区間（平均の対数確率が低い、または無音らしい）があれば「聞き取りにくい」とする
    unclear = any(s.avg_logprob < -1.0 or s.no_speech_prob > 0.6 for s in segments)

    return Transcript(
        text=text,
        duration=info.duration,
        speech_seconds=speech,
        unclear=unclear,
        elapsed=time.monotonic() - started,
    )
