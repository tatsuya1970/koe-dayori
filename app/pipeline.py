"""話して送る流れ（仕様書 3.1）：録音 → 文字 → Claude（要約・緊急度を並列）→ LINE。"""

import logging
from dataclasses import dataclass
from pathlib import Path

from app import config, outbox, stt

log = logging.getLogger(__name__)


@dataclass
class Result:
    status: str  # "sent"（送った）/ "queued"（貯めた。あとで送る）/ "empty"（何も話していない）
    transcript: stt.Transcript | None = None
    remaining: int = 0
    summary: str | None = None  # LINE に送った要約（モニターに出す）。Claude に届かなかったときは None


def handle_recording(wav_path, db, *, keep_audio=False, dry_run=False):
    wav_path = Path(wav_path)
    try:
        transcript = stt.transcribe(wav_path)
    finally:
        if not keep_audio:
            wav_path.unlink(missing_ok=True)  # 録音は文字にしたら消す（仕様書 10章）

    log.info("文字起こし %.1f秒（録音 %.1f秒、話した %.1f秒）: %s",
             transcript.elapsed, transcript.duration, transcript.speech_seconds, transcript.text)

    if transcript.speech_seconds < config.MIN_SPEECH_SECONDS or not transcript.text:
        return Result("empty", transcript)

    job_id = outbox.add(db, transcript.text, transcript.unclear)
    sent, remaining = outbox.flush(db, dry_run=dry_run)
    status = "queued" if remaining else "sent"
    row = db.execute("SELECT summary FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return Result(status, transcript, remaining, row["summary"] if row else None)
