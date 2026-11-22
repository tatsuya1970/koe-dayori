# 依頼：Mac の Claude Code → Pi の Claude Code（2026-11-22）

LINE の受け口（Cloudflare Quick Tunnel）が Cloudflare 側で消されると、自分では直らない。
**消えたことに気づいたら `koe-tunnel` を自動で再起動する見張り**を、Pi で作ってほしい。
経緯は README の「起きたこと：LINE の受け口が切れたまま直らなかった（2026-11-22）」。

## 起きたこと（11/22 Pi のログより）

- 12:23:15 `ERR Serve tunnel error error="connection with edge closed"`。そのあと `Retrying connection` が続く
- 遅くとも 13:04 から `ERR ... error="Unauthorized: Tunnel not found"` が数分おきに出続けた。Cloudflare 側でトンネル自体が消えている
- `cloudflared` は消えたトンネルにつなぎ直そうとし続けるだけで、プロセスは落ちない。`Restart=always` は効かず、`systemctl` は `active` のまま
- URL が変わらないので、`app/tunnel.py` は LINE に登録し直さない。LINE から送ったお便りが1通も Pi に届かなかった
- 13:38 に `systemctl --user restart koe-tunnel` を手で1回実行。4秒で新しい URL につながり、6秒後に `app/tunnel.py` が LINE に登録し直した。LINE から届くことを本人が確かめた

## 作ってほしいもの

- **見張り方**：ログを読むより、`cloudflared` の metrics の `/ready`（`127.0.0.1:20241/ready`）を見るほうが素直だと思う。11/22 13:4x に Mac から確かめたところ、つながっているときは `HTTP 200` と `{"status":200,"readyConnections":1,...}` が返った。接続が1つもないと 503（`readyConnections:0`）になるはずだが、こちらはまだ確かめていない。切れている状態を作れない場合は、ログの `Tunnel not found` を数えるやり方も考える
- **再起動の条件**：だめな状態が**数分（例：5分）続いたら**再起動する。数秒の切断（12:23 のような `edge closed` のあと、すぐ戻る場合）では再起動しない
- **再起動の間隔**：trycloudflare は作り直しが続くと 429 を返し、しばらく受け口が使えなくなる（11/7 夜に起きた）。再起動したら、少なくとも10分は次の再起動をしない。429 が出たら、さらに間を空ける
- **置き場所**：今ある定期の仕事（`app/tunnel.py` の `sync_webhook` を30秒ごとに呼んでいるところ）に足すのが自然。本番はユーザー単位の systemd で動いているので、`systemctl --user restart koe-tunnel` を呼べるはず。別のタイマーにするかどうかは Pi で決めてよい
- **記録**：再起動したらログに理由を残す（例：「受け口が5分つながらないので作り直します」）。家族の LINE グループへの知らせは今は要らない
- **試験**：`tests/test_tunnel.py` に、だめな状態が続いたときだけ再起動すること、間隔を空けることの試験を足す
- **締めくくり**：本番に配備し（`/deploy-koe`）、README の上の節に「直したこと」を書き足して push する

## 気をつけること

- 見張りの試しで、何度も再起動しない（429 になる）。試験では再起動の処理を差し替えて確かめる
- 本番を再起動したら、`systemctl --user is-active koe-tunnel koe-dayori` が両方 active か、LINE から1通送って届くかを確かめる
