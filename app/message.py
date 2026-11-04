"""LINE に送る文面を組み立てる（仕様書 3.2：高は目立つ印をつける）。"""

from app import config

HEADERS = {
    "high": "🚨【至急】{name}から",
    "medium": "📞 {name}から（用件）",
    "low": "☺️ {name}から",
}


def compose(created, summary, urgency, transcript, unclear=False):
    header = HEADERS[urgency.level].format(name=config.PARENT_NAME)
    lines = [f"{header}　{created:%-m/%-d %H:%M}", "", summary]

    if urgency.level == "high" and urgency.reason:
        lines += ["", f"（至急とした理由：{urgency.reason}）"]

    lines += ["", "── 話した言葉 ──", transcript]
    if unclear:
        lines.append("※ 聞き取りにくいところがありました")
    return "\n".join(lines)
