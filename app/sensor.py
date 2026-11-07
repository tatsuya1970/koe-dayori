"""人感センサー（仕様書 3.5）：反応を記録し、「動きがない」を判定する。

- 7〜22時の昼の時間で数えて NO_MOTION_HOURS（4時間）動きがなければ知らせる。夜は数えない
- 朝 MORNING_DEADLINE_HOUR（10時）までに動きがなければ知らせる
- 同じ「動きのない間」について知らせるのは1回だけ
- センサーの記録がまだ1件もないとき（つないでいない）は何もしない

判定は check() にまとめ、時刻を渡して試せるようにしてある（GPIO なしで試験できる）。
"""

import logging
from datetime import datetime, time, timedelta

from app import clock, config, db as koe_db

log = logging.getLogger(__name__)

MIN_INTERVAL_SECONDS = 60  # 記録は1分に1回まで（SDカードを書きすぎない）


def record_motion(conn, now=None):
    now = now or datetime.now()
    last = last_motion(conn)
    if last and (now - last).total_seconds() < MIN_INTERVAL_SECONDS:
        return False
    conn.execute("INSERT INTO motion (ts) VALUES (?)", (now.isoformat(timespec="seconds"),))
    conn.commit()
    return True


def last_motion(conn, before=None):
    if before:
        row = conn.execute("SELECT MAX(ts) FROM motion WHERE ts <= ?", (before.isoformat(timespec="seconds"),)).fetchone()
    else:
        row = conn.execute("SELECT MAX(ts) FROM motion").fetchone()
    return datetime.fromisoformat(row[0]) if row and row[0] else None


def motions_between(conn, start, end):
    rows = conn.execute(
        "SELECT ts FROM motion WHERE ts >= ? AND ts < ? ORDER BY ts",
        (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
    ).fetchall()
    return [datetime.fromisoformat(r[0]) for r in rows]


def check(conn, now=None):
    """新しく知らせるべきことを (種類, 文) の一覧で返す。返したものには印をつける（二度は返さない）。"""
    now = now or datetime.now()
    name = config.PARENT_NAME
    alerts = []
    last = last_motion(conn, before=now)
    if last is None or not config.WATCH_ENABLED:
        return alerts

    # 朝の確認
    morning_from = datetime.combine(now.date(), time(config.MORNING_FROM_HOUR))
    deadline = datetime.combine(now.date(), time(config.MORNING_DEADLINE_HOUR))
    if now >= deadline and last < morning_from:
        if koe_db.mark_once(conn, "morning", now.date().isoformat(), now):
            # 同じ「動きのない間」で、あとから4時間の知らせが重ねて出ないようにする
            koe_db.mark_once(conn, "no_motion", last.isoformat(), now)
            alerts.append(("morning", (
                f"⚠️ 今朝{config.MORNING_DEADLINE_HOUR}時になっても、{name}の部屋で動きがありません。\n"
                f"最後に動いたのは {_when(last, now)} です。念のため様子を確かめてください。"
            )))

    # 昼に長く動きがない
    if clock.is_daytime(now) and clock.daytime_seconds(last, now) >= config.NO_MOTION_HOURS * 3600:
        if koe_db.mark_once(conn, "no_motion", last.isoformat(), now):
            alerts.append(("no_motion", (
                f"⚠️ {name}の部屋で、{config.NO_MOTION_HOURS:g}時間ほど動きがありません。\n"
                f"最後に動いたのは {_when(last, now)} です。念のため様子を確かめてください。\n"
                "（じっと座っていると、センサーが気づかないことがあります）"
            )))
    return alerts


def _when(dt, now):
    if dt.date() == now.date():
        return f"今日の {clock.hm(dt)}"
    if dt.date() == (now - timedelta(days=1)).date():
        return f"きのうの {clock.hm(dt)}"
    return f"{dt.month}/{dt.day} {clock.hm(dt)}"


def purge(conn, now=None):
    cutoff = ((now or datetime.now()) - timedelta(days=config.RETENTION_DAYS)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM motion WHERE ts < ?", (cutoff,))
    conn.commit()


class Watcher:
    """GPIO27 の人感センサーを見張る（Pi の上だけ）。

    SB612B はオープンコレクター出力なので、内部プルアップで読み、反応すると Low になる。
    ※ 11/7 Mac で作成。実機で反応の向きを確かめる。
    """

    def __init__(self, pin=None):
        from gpiozero import DigitalInputDevice

        self._device = DigitalInputDevice(pin or config.SENSOR_GPIO, pull_up=True, bounce_time=0.1)
        self._device.when_activated = self._on_motion

    def _on_motion(self):
        conn = koe_db.connect()
        try:
            if record_motion(conn):
                log.info("動きを記録しました")
        finally:
            conn.close()
