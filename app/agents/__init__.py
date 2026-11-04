"""役割ごとに Claude を呼ぶ（仕様書 4章）。"""

from concurrent.futures import ThreadPoolExecutor

from app.agents import summary, urgency
from app.agents.base import TemporaryError  # noqa: F401（呼び出し側で使う）


def analyze(transcript_text, unclear=False):
    """要約と緊急度の判定を並列で行う。

    通信切れなど、あとでやり直せば済む失敗は TemporaryError を投げる。
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_summary = pool.submit(summary.run, transcript_text, unclear)
        f_urgency = pool.submit(urgency.run, transcript_text)
        return f_summary.result(), f_urgency.result()
