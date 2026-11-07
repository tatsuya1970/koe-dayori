---
name: test-handset
description: 黒電話の受話器（フックスイッチ・耳のスピーカー・マイク）と本体スピーカーを、部品ごとに Pi で確かめる。配線を変えたとき、改造のあと、帰省先で動かす前に使う。
---

# 受話器を確かめる（Pi の上で）

本番が動いているとデバイスを取り合うので、先に `systemctl --user stop koe-dayori` で止める。終わったら `start` で戻す。

1. **音のデバイス**：`aplay -l` と `arecord -l` で一覧を出し、USB 音声アダプター（受話器）と USB スピーカーフォン（本体）のカード番号を確かめる
   - `.env` の `KOE_HANDSET_PLAY_DEVICE`・`KOE_HANDSET_REC_DEVICE`・`KOE_SPEAKER_DEVICE` と合っているか（例：`plughw:2,0`）。違えば本人に `nano .env` で直してもらう（鍵があるファイルなので Claude は書かない）
2. **耳のスピーカー**：`.venv/bin/python -c "from app import sounds; print(sounds.beep())"` で「ピー」の WAV を作り、`aplay -D <受話器のデバイス> <そのパス>` で鳴らす。本人に受話器から聞こえるか聞く
3. **マイク**：`arecord -D <受話器の録音デバイス> -f S16_LE -r 16000 -c 1 -d 5 /tmp/handset.wav` で5秒録り、本人に受話器へ話してもらう。`.venv/bin/python -m tools.try_stt /tmp/handset.wav` で文字になるか確かめる。小さすぎれば `alsamixer` でマイクの音量を上げる
4. **フックスイッチ**：`.venv/bin/python -c "from gpiozero import Button; from signal import pause; b=Button(17, pull_up=True, bounce_time=0.05); b.when_pressed=lambda: print('pressed'); b.when_released=lambda: print('released'); pause()"` を動かし、本人に受話器を取る・置くを3回ずつしてもらう
   - 取ったときに pressed なら `KOE_HOOK_PRESSED_IS_OFF_HOOK=1`、released なら `0`
   - 1回の操作で何度も出るなら、チャタリング。`bounce_time` を 0.1 に上げて試す
5. **本体スピーカー**：`.venv/bin/python -c "from app import sounds; print(sounds.chime())"` のチャイムを `aplay -D <本体のデバイス>` で鳴らし、驚かない大きさかを本人に聞く
6. 結果（デバイス名、フックの向き、気づいたこと）を `docs/claude-code-log.md` に記録する
