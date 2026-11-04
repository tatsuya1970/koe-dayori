# Claude Code の使い方と試行錯誤の記録

審査の「Claudeの活用」「深さと実行力」で見せるための記録。日付順に追記する。

## 2026-11-04 LINEのグループIDを取る

- Pi上のClaude Codeが `tools/capture_group_id.py`（仮の受け口）を裏で動かし、LINEのAPI（`/v2/bot/info`、`/v2/bot/group/{id}/summary`）で、トークンとグループIDが正しいかを確かめた。鍵は画面に出さないようにした
- 外から入れるトンネル（cloudflared）を開く操作は、Claude Codeの自動モードに止められた。外への入口を開く操作は本人が動かす、という分担にした
- **つまずき**：公式アカウントを招待すると、join の直後に leave になる
  - 参加を許可する設定はオンになっていた（Official Account Manager と LINE Developers の両方で確認した）
  - Claude CodeがLINEの公式ドキュメントを調べ、「グループに同時に入れる公式アカウントは1つだけ」という制限を見つけた
  - テスト用グループに別の公式アカウントが入っていた。それを外したら参加できた
- 学んだこと：実家の家族グループに入れるときも、先にほかの公式アカウントがないか確かめる

## 2026-11-05 Tailscale Funnel を入れる

- インストール・ログイン・Funnel を開く操作は、`sudo` のパスワードと外への入口が関わるので、本人がSSHの画面で動かした。Claude Code は状態の確認（`tailscale status`、`tailscale funnel status`）と試しの送信を受け持った
- **つまずき**：LINE の「検証」が失敗し、受け口には何も届いていなかった
  - Claude Code が Pi から試すと届いていた。ところが Pi は MagicDNS で名前を tailnet の中のアドレス（100.x）に引いていて、外を通っていなかった
  - Google の公開DNS（DNS over HTTPS）で引くと、Tailscale の中継サーバー（103.84.155.x）に向いていた。`curl --resolve` で中継サーバーを通して試すと、届いた
  - もう一度検証したら成功した。Funnel を開いた直後で、外から名前を引けるようになる前に検証したためと考えられる（確かめてはいない）
- 学んだこと：tailnet の中のマシンから Funnel を試しても、外からの確認にはならない。中継サーバーを指定して試す
