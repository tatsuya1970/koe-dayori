#!/bin/bash
# SORACOM Onyx（Quectel EG25-G）を使えるようにし、
# ドングルが挿さってつながっている間は SORACOM（LTE）を優先して通信する。
# ドングルを抜けば、NetworkManager が自動で Wi-Fi に戻す。
#
# 使い方（Pi で、ドングルに IoT SIM を挿して USB に差してから）:
#   bash deploy/soracom-lte.sh            # plan-D など
#   bash deploy/soracom-lte.sh du.soracom.io   # plan-DU の SIM のとき
#
# 手順の元: https://users.soracom.io/ja-jp/guides/usb-dongles/soracom-onyx/raspberrypi/
set -e

# 1. SORACOM の設定スクリプトで、接続 "soracom"（APN soracom.io / sora / sora）を作る
cd /tmp
curl -fsSO https://soracom-files.s3.amazonaws.com/connect/setup_eg25.sh
chmod +x setup_eg25.sh
sudo ./setup_eg25.sh "$@"

# 2. LTE を Wi-Fi（metric 600）より優先する。つながらなくても、あきらめずに再接続し続ける
sudo nmcli connection modify soracom \
  ipv4.route-metric 50 \
  ipv6.route-metric 50 \
  connection.autoconnect yes \
  connection.autoconnect-retries 0

# 3. 設定を反映する
sudo nmcli connection down soracom || true
sudo nmcli connection up soracom

# 4. 確かめる（default の経路で wwan0 が一番上＝metric 50 になっていればよい）
ip route show default
ping -c 3 pong.soracom.io
