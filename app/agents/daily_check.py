"""「いつもと違う」の検知の担当：今日の記録を直近7日と比べ、「今日の様子」の文を作る（仕様書 3.6・4章）。"""

import json

from app import config
from app.agents.base import ask_json

SYSTEM = f"""あなたは、一人暮らしの高齢の親（{config.PARENT_NAME}）の見守りを手伝う係です。
毎晩、離れて暮らす家族のLINEグループに「今日の様子」を送ります。今日の記録と、直近の日の記録（JSON）が届きます。

記録の中身
- motion：人感センサーの反応。first は朝最初に動いた時刻、by_hour は時間帯ごとの反応の回数
- talks：親が話して家族に送ったメッセージ（時刻・要点・緊急度）
- letters：家族からのお便りを、届いてから何分で聞いたか（聞いていなければ null）

report（今日の様子）
- 3〜5行、全体で200字以内。です・ます調。家族がひと目で安心できるか、気にすべきかがわかるように
- 朝最初に動いた時刻、よく動いていた時間帯、話した回数と主な内容を入れる
- 記録がない項目は「記録がありません」と書き、推測で埋めない

unusual（いつもと違うこと）
- 直近の日と比べて、はっきり違う点だけを、1つにつき40字以内で。なければ空の配列
- 例：「いつもは7時ごろ動き出しますが、今日は9時でした」「この3日、膝が痛いという話が続いています」
- 体の不調や気持ちの落ち込みが何日か続いているときは、必ず入れる
- 比べる日が少ないとき（3日未満）は、そのことを踏まえて控えめに。1日だけの小さな違いは入れない
- 病名を推測したり、診断のようなことは書かない"""

SCHEMA = {
    "type": "object",
    "properties": {
        "report": {"type": "string"},
        "unusual": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["report", "unusual"],
    "additionalProperties": False,
}


def run(today, history):
    """(report, unusual) を返す。使えない答えのときは None。"""
    payload = json.dumps({"today": today, "history": history}, ensure_ascii=False, indent=1)
    result = ask_json(
        model=config.DAILY_MODEL,
        effort=config.DAILY_EFFORT,
        system=SYSTEM,
        user_text=f"<記録>\n{payload}\n</記録>",
        schema=SCHEMA,
    )
    if not result or not result.get("report", "").strip():
        return None
    return result["report"].strip(), [u.strip() for u in result.get("unusual", []) if u.strip()]
