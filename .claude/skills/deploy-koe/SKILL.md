---
name: deploy-koe
description: 声だより を Pi の systemd（ユーザー単位）に配備し直し、起動したことを確かめる。コードを変えたあと、本番の動きで試したいときに使う。
---

# 声だより を配備する（Pi の上で）

1. `git pull` で最新にする
2. 試験を流す：`.venv/bin/python -m unittest discover -s tests -t .`。失敗したら配備しないで報告する
3. 初めてのときだけ（`~/.config/systemd/user/koe-dayori.service` がなければ）
   - `mkdir -p ~/.config/systemd/user`
   - `cp deploy/koe-dayori.service ~/.config/systemd/user/`
   - `systemctl --user daemon-reload`
   - `systemctl --user enable koe-dayori`
   - `sudo loginctl enable-linger $USER` は **本人に** 打ってもらう（sudo のパスワードが要るため）
   - キオスク表示：`chmod +x deploy/kiosk.sh`、`mkdir -p ~/.config/autostart`、`cp deploy/kiosk.desktop ~/.config/autostart/`
4. 起動し直す：`systemctl --user restart koe-dayori`
5. 確かめる
   - `systemctl --user is-active koe-dayori` が active
   - `journalctl _SYSTEMD_USER_UNIT=koe-dayori.service -n 30 --no-pager` に「準備ができました」がある
   - `curl -s http://127.0.0.1:8001/api/state` が JSON を返す（モニター表示）
   - `curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:8000/callback` が 403（署名なしは断る＝受け口が動いている）
6. 結果を短く報告する。止まっていたらログの最後のエラーを示す

シェルのコマンドは `&&` や `;` でつなげず、1つずつ実行する（CLAUDE.md）。
