#!/usr/bin/env python3
"""Claude Code のフック（PreToolUse / Bash）：git commit・git push の前に、秘密と親の記録が入っていないかを確かめる。

止めるもの（仕様書 5・10章）
- ファイル：.env（見本の .env.example は除く）、録音（.wav）、記録（.sqlite3）、写真のフォルダ、data/
- 中身：.env に書いた値そのもの、Anthropic の APIキー、LINE のトークン・グループID・ユーザーIDらしき文字列

見つけたら終了コード 2 で止め、理由を Claude に返す（Claude はそれを読んで直す）。
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])

BLOCKED_NAMES = [
    (re.compile(r"(^|/)\.env(\.(?!example$)[^/]*)?$"), ".env（秘密情報）"),
    (re.compile(r"\.wav$", re.I), "録音"),
    (re.compile(r"\.sqlite3(-\w+)?$"), "親の記録（SQLite）"),
    (re.compile(r"(^|/)(data|photos|recordings)/"), "親の記録・家族の写真のフォルダ"),
]
PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "Anthropic の APIキー"),
    (re.compile(r"\b[CU][0-9a-f]{32}\b"), "LINE のグループID・ユーザーID"),
    (re.compile(r"[A-Za-z0-9+/]{120,}={0,2}"), "LINE のトークンらしき長い文字列"),
]


def git(*args):
    res = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return res.stdout if res.returncode == 0 else ""


def env_values():
    path = ROOT / ".env"
    if not path.exists():
        return []
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            v = line.split("=", 1)[1].strip().strip('"').strip("'")
            if len(v) >= 8:
                values.append(v)
    return values


def changes(command):
    """これから記録・送信される (ファイル名の一覧, 足される行)。"""
    if re.search(r"\bgit\b.*\bpush\b", command):
        upstream = git("rev-parse", "--abbrev-ref", "@{u}").strip()
        if not upstream:
            return [], []
        names = git("diff", "--name-only", f"{upstream}..HEAD").split()
        diff = git("diff", f"{upstream}..HEAD")
    else:
        names = git("diff", "--cached", "--name-only").split()
        diff = git("diff", "--cached")
        if re.search(r"\s(-a|--all|-am)\b", command):
            names += git("diff", "--name-only").split()
            diff += git("diff")
    added = [l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++")]
    return names, added


def problems(names, added, secrets):
    found = []
    for name in names:
        for pattern, label in BLOCKED_NAMES:
            if pattern.search(name):
                found.append(f"{name}：{label}")
    # 中身は表示しない（秘密の一部が画面や記録に残らないように）。何行目あたりかだけ出す
    for n, line in enumerate(added, 1):
        if any(value in line for value in secrets):
            found.append(f"足される行の {n} 行目：.env の値がそのまま入っています")
        for pattern, label in PATTERNS:
            if pattern.search(line):
                found.append(f"足される行の {n} 行目：{label}")
    return sorted(set(found))


def main():
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    command = (data.get("tool_input") or {}).get("command", "")
    if not re.search(r"\bgit\b.*\b(commit|push)\b", command):
        return 0
    names, added = changes(command)
    found = problems(names, added, env_values())
    if not found:
        return 0
    print("秘密情報か親の記録が入りそうなので止めました（.claude/hooks/check_secrets.py）：", file=sys.stderr)
    for f in found:
        print(f"  - {f}", file=sys.stderr)
    print("該当のファイルを git restore --staged で外すか、中身を直してください。", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
