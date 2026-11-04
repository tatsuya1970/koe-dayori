"""要約の担当：親が話したことを、家族向けの要点（2〜3行）にする。"""

from app import config
from app.agents.base import ask_json

SYSTEM = f"""あなたは、離れて暮らす家族に、高齢の親（{config.PARENT_NAME}）の声のメッセージを伝える係です。
親が電話の受話器に向かって話した内容の文字起こしが届きます。家族がLINEでひと目でわかる要点にしてください。

- 2〜3行、全体で120字以内。です・ます調
- 用件（買ってきてほしい物、予定、頼みごと）があれば、それを先に書く
- 体調や気持ちについて言っていれば、必ず入れる
- 文字起こしには聞き間違いが混じります。前後から明らかな誤りは直してよいが、話していないことは足さない
- 意味が取れないところは、無理に埋めず「（聞き取れず）」とする
- 親を「{config.PARENT_NAME}」と呼ぶ"""

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "家族向けの要点（2〜3行）"},
    },
    "required": ["summary"],
    "additionalProperties": False,
}


def run(transcript_text, unclear=False):
    note = "\n\n（注：録音に聞き取りにくいところがありました）" if unclear else ""
    result = ask_json(
        model=config.SUMMARY_MODEL,
        effort=config.SUMMARY_EFFORT,
        system=SYSTEM,
        user_text=f"<文字起こし>\n{transcript_text}\n</文字起こし>{note}",
        schema=SCHEMA,
    )
    if not result or not result.get("summary", "").strip():
        # 安全側：文字起こしをそのまま送る
        return transcript_text
    return result["summary"].strip()
