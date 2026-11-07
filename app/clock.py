"""昼（7〜22時）と夜の扱い。「まだ聞いていません」と「動きがない」は昼の時間だけを数える。"""

from datetime import datetime, time, timedelta

from app import config


def is_daytime(dt):
    return config.DAY_START_HOUR <= dt.hour < config.DAY_END_HOUR


def daytime_seconds(start, end):
    """start から end までのうち、昼（DAY_START_HOUR〜DAY_END_HOUR）にあたる秒数。"""
    total = 0.0
    cur = start
    while cur < end:
        day = cur.date()
        lo = max(cur, datetime.combine(day, time(config.DAY_START_HOUR)))
        hi = min(end, datetime.combine(day, time(config.DAY_END_HOUR)))
        if hi > lo:
            total += (hi - lo).total_seconds()
        cur = datetime.combine(day + timedelta(days=1), time(0))
    return total


def hm(dt):
    return f"{dt.hour}:{dt.minute:02d}"
