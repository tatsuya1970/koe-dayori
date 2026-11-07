"""毎晩20時の「今日の様子」（仕様書 3.6）。

その日の記録（動き・話したこと・お便りを聞くまでの時間）をまとめ、直近7日分と一緒に Claude に渡す。
Claude が使えないときは、決まった形の文で送る（安全側：送らないよりはよい）。
"""

import logging
import sqlite3
from datetime import date, datetime, time, timedelta

from app import clock, config, sensor
from app.agents import daily_check

log = logging.getLogger(__name__)

HISTORY_DAYS = 7


def _day_range(day):
    start = datetime.combine(day, time(0))
    return start, start + timedelta(days=1)


def _talks(outbox_conn, day):
    if outbox_conn is None:
        return []
    start, end = _day_range(day)
    try:
        rows = outbox_conn.execute(
            "SELECT created_at, summary, transcript, urgency FROM jobs WHERE created_at >= ? AND created_at < ? ORDER BY id",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    except sqlite3.OperationalError:  # まだ一度も話していない（表がない）
        return []
    return [{
        "time": clock.hm(datetime.fromisoformat(r["created_at"])),
        "summary": r["summary"] or r["transcript"],
        "urgency": r["urgency"],
    } for r in rows]


def _letters(conn, day):
    start, end = _day_range(day)
    rows = conn.execute(
        "SELECT received_at, played_at, sender_name FROM letters WHERE received_at >= ? AND received_at < ? ORDER BY id",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    out = []
    for r in rows:
        wait = None
        if r["played_at"]:
            wait = int((datetime.fromisoformat(r["played_at"]) - datetime.fromisoformat(r["received_at"])).total_seconds() // 60)
        out.append({"from": r["sender_name"], "received": clock.hm(datetime.fromisoformat(r["received_at"])), "minutes_to_listen": wait})
    return out


def day_record(conn, outbox_conn, day, until=None):
    start, end = _day_range(day)
    if until:
        end = min(end, until)
    motions = sensor.motions_between(conn, start, end)
    by_hour = {}
    for m in motions:
        by_hour[m.hour] = by_hour.get(m.hour, 0) + 1
    return {
        "date": f"{day.month}/{day.day}（{'月火水木金土日'[day.weekday()]}）",
        "motion": {
            "first": clock.hm(motions[0]) if motions else None,
            "last": clock.hm(motions[-1]) if motions else None,
            "count": len(motions),
            "by_hour": {f"{h}時": n for h, n in sorted(by_hour.items())},
        },
        "talks": _talks(outbox_conn, day),
        "letters": _letters(conn, day),
    }


def _has_any(record):
    return record["motion"]["count"] or record["talks"] or record["letters"]


def fallback_text(today):
    m = today["motion"]
    lines = [f"朝最初に動いたのは {m['first']} でした。" if m["first"] else "今日は動きの記録がありません。"]
    if today["talks"]:
        lines.append(f"話して送ったメッセージは {len(today['talks'])} 件でした。")
        lines += [f"・{t['time']} {t['summary']}" for t in today["talks"][:3]]
    else:
        lines.append("今日は話して送ったメッセージはありません。")
    return "\n".join(lines)


def build(conn, outbox_conn, now=None):
    now = now or datetime.now()
    today = day_record(conn, outbox_conn, now.date(), until=now)
    history = []
    for i in range(1, HISTORY_DAYS + 1):
        rec = day_record(conn, outbox_conn, now.date() - timedelta(days=i))
        if _has_any(rec):
            history.append(rec)

    result = None
    try:
        result = daily_check.run(today, history)
    except Exception as e:  # 通信切れなど。今日の様子は待たせず、決まった形で送る
        log.warning("今日の様子を Claude で作れませんでした: %s", e)

    head = f"📋 今日の{config.PARENT_NAME}の様子（{today['date']}）"
    if result:
        report, unusual = result
        text = f"{head}\n\n{report}"
        if unusual:
            text += "\n\n🔎 いつもと違うこと\n" + "\n".join(f"・{u}" for u in unusual)
    else:
        text = f"{head}\n\n{fallback_text(today)}"
    return text


def due(now):
    return now.hour >= config.DAILY_REPORT_HOUR


def today_key(now):
    return date.isoformat(now.date())
