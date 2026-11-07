---
name: try-letter
description: 家族からのお便り（LINE の返信）が届いたことにして、受け取り → 言い換え → チャイム → モニター → 受話器で再生 の流れを Pi で試す。家族に LINE を書いてもらわずに試したいときに使う。
---

# お便りの流れを試す（Pi の上で）

前提：`app.main` が動いている（systemd か、別の画面で `.venv/bin/python -m app.main --keyboard`）。

1. 試しのお便りを送る（引数がなければ、下の例を使う）
   - `.venv/bin/python -m tools.fake_line_event "週末にそっちへ行くね。お昼は一緒に食べよう🍙" --name たつや`
2. 数秒待ってログを見る：`journalctl _SYSTEMD_USER_UNIT=koe-dayori.service -n 20 --no-pager`
   - 「お便りを 1 件受け取りました」「お便りの準備ができました（たつやさんから）」が出ること
3. 言い換えの結果を見る：`curl -s http://127.0.0.1:8001/api/state` の `mode` が `new`、`waiting.count` が増えていること
4. 本人に受話器を取ってもらい（`--keyboard` なら Enter）、読み上げとモニターの表示を確かめてもらう
5. 「聞きました」が LINE に届いたかを本人に確かめてもらう（`KOE_DRY_RUN=1` のときはログに出るだけ）
6. 言い換えが不自然だったら、その文と結果を `docs/claude-code-log.md` に記録し、`app/agents/rewrite.py` のプロンプトを直す

絵文字・URL・英字が入った文も試すと、言い換えの確認になる。
