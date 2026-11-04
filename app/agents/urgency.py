"""緊急度の担当：高・中・低を判定する（仕様書 3.2）。迷ったら高い側に倒す。"""

from dataclasses import dataclass

from app import config
from app.agents.base import ask_json

SYSTEM = f"""あなたは、一人暮らしの高齢の親（{config.PARENT_NAME}）の見守りを手伝う係です。
親が家族に向けて話した内容の文字起こしを読み、家族がどれだけ急いで見るべきかを判定してください。

- high：体調が悪い、痛い、転んだ、けがをした、助けて、息苦しい、薬がない、火や水のトラブル、不審な人や電話、
  ひどく落ち込んでいる、いつもと様子がおかしい（話が混乱している など）
- medium：家族に何かしてほしい用件（買い物、病院の予約、手続き、来てほしい、電話がほしい）
- low：雑談、近況、お礼、元気だという報告

- 迷ったら高い側を選ぶ（見逃すより、送りすぎのほうがよい）
- 冗談や昔の話であることが明らかなときだけ、内容の強さより低くしてよい
- 文字起こしには聞き間違いが混じる。危ない言葉に聞こえるところは、危ない側に読む
- reason は、家族向けに日本語で30字以内"""

SCHEMA = {
    "type": "object",
    "properties": {
        "level": {"type": "string", "enum": ["high", "medium", "low"]},
        "reason": {"type": "string"},
    },
    "required": ["level", "reason"],
    "additionalProperties": False,
}


@dataclass
class Urgency:
    level: str   # high / medium / low
    reason: str
    fallback: bool = False  # 判定できず、安全側にしたとき


def run(transcript_text):
    result = ask_json(
        model=config.URGENCY_MODEL,
        effort=config.URGENCY_EFFORT,
        system=SYSTEM,
        user_text=f"<文字起こし>\n{transcript_text}\n</文字起こし>",
        schema=SCHEMA,
        max_tokens=2000,
    )
    if not result or result.get("level") not in ("high", "medium", "low"):
        return Urgency("high", "自動の判定ができなかったため", fallback=True)
    return Urgency(result["level"], result.get("reason", "").strip())
