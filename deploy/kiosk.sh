#!/bin/sh
# モニター表示を全画面で開く（仕様書 3.4）。デスクトップにログインしたら自動で動く（kiosk.desktop から呼ぶ）。
# 表示のサーバー（app.main）が立ち上がるまで待ってから開く。

URL="http://127.0.0.1:8001/"

i=0
while [ "$i" -lt 60 ]; do
  if curl -s -o /dev/null "$URL"; then
    break
  fi
  i=$((i + 1))
  sleep 2
done

# 画面が消えないようにする（夜の扱いはページ側で暗くする。U26）
xset s off 2>/dev/null
xset -dpms 2>/dev/null

# --password-store=basic：自動ログインだとパスワード保管庫（Default Keyring）に鍵がかかったままで、
# 起動のたびに「Authentication required」が画面に出る（11/8）。保管庫を使わないようにして出さない
exec chromium --kiosk --noerrdialogs --disable-infobars --incognito \
  --disable-session-crashed-bubble --check-for-update-interval=31536000 \
  --password-store=basic \
  "$URL"
