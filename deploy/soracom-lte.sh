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

# 0. ModemManager を入れる（Raspberry Pi OS には入っておらず、setup_eg25.sh も入れない。
#    ないと NetworkManager がドングルを使えず "No suitable device found" になる。2026-11-20 に確認）
sudo apt-get install -y modemmanager
sudo systemctl enable --now ModemManager
echo "ドングルを認識するまで待ちます..."
for n in $(seq 1 30); do
  if mmcli -L | grep -q QUECTEL; then break; fi
  sleep 2
done
mmcli -L

# 1. SORACOM の設定スクリプトで、接続 "soracom"（APN soracom.io / sora / sora）を作る
cd /tmp
curl -fsSO https://soracom-files.s3.amazonaws.com/connect/setup_eg25.sh
chmod +x setup_eg25.sh
sudo ./setup_eg25.sh "$@"

# 2. LTE を Wi-Fi（metric 600）より優先する。つながらなくても、あきらめずに再接続し続ける
#    DNS は SORACOM のもの（100.127.0.53）を使わず、公開の DNS にする。
#    100.127.x.x は Tailscale が自分用に取っている範囲（100.64.0.0/10）と重なり、
#    Tailscale が返事を捨てるため、LTE だけのときに名前が引けず、どこにもつながらなかった（2026-11-20）
sudo nmcli connection modify soracom \
  ipv4.route-metric 50 \
  ipv6.route-metric 50 \
  ipv4.ignore-auto-dns yes \
  ipv4.dns "8.8.8.8 1.1.1.1" \
  connection.autoconnect yes \
  connection.autoconnect-retries 0

# 3. 設定を反映する
sudo nmcli connection down soracom || true
sudo nmcli connection up soracom

# 4. 確かめる（default の経路で wwan0 が一番上＝metric 50 になっていればよい）
ip route show default
ping -c 3 -I wwan0 8.8.8.8
# pong.soracom.io（100.127.100.127）は、上と同じ理由で Tailscale が入っていると返事が来ない
