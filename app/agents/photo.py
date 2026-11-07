"""写真の説明の担当：家族が送った写真の内容を、一言で（読み上げと画面用）（仕様書 3.4・4章）。"""

import base64

from app import config
from app.agents.base import ask_json

FALLBACK = "写真が届きました。画面を見てください。"
MAX_BYTES = 4_500_000  # Claude に送れる画像の大きさの上限より少し小さく
MEDIA_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")

SYSTEM = f"""あなたは、離れて暮らす家族が送った写真を、高齢の親（{config.PARENT_NAME}）に言葉で伝える係です。
説明は受話器から読み上げ、同時に写真を画面に大きく出します。

- 何が写っているかを、1〜2文、全体で50字以内で。です・ます調
- 人が写っていれば、何をしているところかを中心に（例：「男の子が公園で滑り台をしている写真です」）
- 名前は、送り主のメッセージや写真の中の文字からはっきりわかるときだけ使う。顔から誰かを推測しない
- 写真に写っていないことは言わない。見えにくいときは見えている範囲だけ言う"""

SCHEMA = {
    "type": "object",
    "properties": {"caption": {"type": "string"}},
    "required": ["caption"],
    "additionalProperties": False,
}


def run(image_bytes, media_type, sender_name):
    media_type = (media_type or "").split(";")[0].strip()
    if media_type not in MEDIA_TYPES or len(image_bytes) > MAX_BYTES:
        return FALLBACK
    content = [
        {"type": "image", "source": {
            "type": "base64", "media_type": media_type,
            "data": base64.standard_b64encode(image_bytes).decode("ascii"),
        }},
        {"type": "text", "text": f"送り主：{sender_name}"},
    ]
    result = ask_json(
        model=config.PHOTO_MODEL,
        effort=config.PHOTO_EFFORT,
        system=SYSTEM,
        user_text=content,  # 文字の代わりに、画像と文字の組をそのまま渡す
        schema=SCHEMA,
        max_tokens=2000,
    )
    if not result or not result.get("caption", "").strip():
        return FALLBACK
    return result["caption"].strip()
