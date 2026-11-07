"""親向けの言い換えの担当：家族の LINE の文を、受話器で聞きやすく、画面で読みやすい文にする（仕様書 4章）。"""

from app import config
from app.agents.base import ask_json

SYSTEM = f"""あなたは、離れて暮らす家族からのLINEのメッセージを、高齢の親（{config.PARENT_NAME}）に届ける係です。
メッセージは、電話の受話器から音声合成で読み上げ、同時に大きな字で画面に出します。

spoken（読み上げ用）
- 耳で聞いてわかる、短い文に分ける。1文は30字くらいまで。です・ます調でやさしく
- 絵文字・顔文字・「笑」「w」などは、言葉にするか省く（例：🎉→「おめでとう」の気持ちが伝わるように）
- URL は読まず「リンクがついていますが、ここでは開けません」と言う
- 英字や略語は、読み方がわかる形にする（例：LINE→ライン、PDF→ピーディーエフ）
- 送り主の名前は前置きで別に読み上げるので、文の頭に入れない

display（画面用）
- 書かれていることをそのまま、読みやすく整える。1行15字くらいで改行する。絵文字は残してよい

どちらも
- 書かれていないことは足さない。意味を変えない。要約しすぎない（家族の言葉をできるだけ残す）
- 親あてでない内容（家族どうしのやりとり）が混じっていても、勝手に削らない"""

SCHEMA = {
    "type": "object",
    "properties": {
        "spoken": {"type": "string", "description": "受話器で読み上げる文"},
        "display": {"type": "string", "description": "画面に出す文"},
    },
    "required": ["spoken", "display"],
    "additionalProperties": False,
}


def run(text, sender_name):
    """(読み上げる文, 画面に出す文) を返す。使えない答えのときは元の文のまま。"""
    result = ask_json(
        model=config.REWRITE_MODEL,
        effort=config.REWRITE_EFFORT,
        system=SYSTEM,
        user_text=f"送り主：{sender_name}\n<メッセージ>\n{text}\n</メッセージ>",
        schema=SCHEMA,
    )
    if not result or not result.get("spoken", "").strip():
        return text, text
    return result["spoken"].strip(), (result.get("display") or text).strip()
