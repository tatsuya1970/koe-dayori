"""faster-whisper で、録音（WAV）を日本語の文字にする。"""

import time
from dataclasses import dataclass

from app import config

_model = None


@dataclass
class Transcript:
    text: str
    duration: float        # 録音の長さ（秒）
    speech_seconds: float  # 話していた長さ（無音を除く）
    unclear: bool          # 聞き取りにくかったところがある
    elapsed: float         # 文字起こしにかかった秒数


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
    return _model


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
