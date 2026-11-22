# 依頼：Mac の Claude Code → Pi の Claude Code（2026-11-22）

## 頼みたいこと

LINE の受け口を、Pi のトンネル（Cloudflare Quick Tunnel）から **Cloudflare Workers** に移してほしい。

- **Workers（クラウド）**：LINE の Webhook を受け、署名を確かめ、お便りを貯める
- **Pi**：自分から HTTPS で取りに行き、受け取れたらクラウド側から消してもらう

**12 月の試作機（実家に置く機）で使う**。決定の経緯は README の「決定：LINE の受け口をクラウド（Cloudflare Workers）に置き、Pi が取りに行く（2026-11-22）」に、仕様は仕様書 D29・U31・U32 にある。先にこの3つを読む。

### なぜ替えるか（短く）

- Quick Tunnel は Cloudflare 側で消されることがある。11/22 には 12:23〜13:38 のあいだ止まった。今の見張り（`app/tunnel.py` の `Watchdog`）は、消えたあとに作り直すだけ
- 今は、Pi が止まっているあいだに送られたお便りが失われる。クラウドに貯めておけば、停電や LTE の切断のあとでも届く
- Pi は外へ出ていく通信だけで済む。決まった URL になり、ドメインも要らない

## 今の流れ（変えるところ）

```
LINE ─Webhook→ trycloudflare ─→ cloudflared（koe-tunnel）─→ app/webhook.py（127.0.0.1:8000/callback）
                                                              ├ signature_ok()：署名を確かめる
                                                              ├ to_letter()：グループ・種類・メンションで絞る
                                                              └ inbox.add_event()：SQLite に貯める（event_id で二重にしない）
app/main.py の Housekeeper（30秒ごと）：tunnel.sync_webhook() → tunnel.watch() → inbox.prepare() …
```

## 新しい流れ

```
LINE ─Webhook→ Workers（koe-relay.〇〇.workers.dev/callback）
                 ├ 署名を確かめる（LINE_CHANNEL_SECRET を Workers の secret に置く）
                 ├ 決めたグループのメッセージだけを貯める（ほかは貯めずに 200）
                 └ すぐ 200 を返す

Pi（app/relay.py などを新しく作る）─HTTPS→ Workers
                 ├ 新しいイベントを受け取る
                 ├ 今の to_letter() → inbox.add_event() にそのまま渡す
                 └ SQLite に入れ終えてから「受け取った」と返す → Workers が消す
```

- **受け取ったあとの処理は変えない**：`to_letter()`、`inbox.add_event()`、`inbox.prepare()`（言い換え・写真の説明）、再生はそのまま使う
- **二重に受けても大丈夫にしてある**：`inbox.add_event()` は `event_id` が同じものを無視する。なので、Pi が SQLite に入れたあと「受け取った」を返す前に落ちても、次に同じものを受け取るだけで済む。**消すのは必ず SQLite に入れたあと**にする

## 決めてほしいこと（U31・U32）

作りながら、次のことを決めて仕様書に書く。

1. **取りに行き方（U31）**：LTE は使った量だけ料金がかかるので、待っているあいだの通信量を小さくしたい
   - 案A：数十秒おきに聞く。作るのはいちばん楽だが、何もなくても通信が出る
   - 案B：長く待つ問い合わせ。届くまで返事を待たせ、届いたらすぐ返す
   - 案C：WebSocket でつなぎっぱなしにし、届いたときだけ知らせる。Durable Objects を使えば、待っているあいだ Workers の料金もほとんどかからないはず
   - **おすすめ**：保存先に Durable Objects（SQLite）を使い、案C を基本にする。WebSocket が切れているあいだは、案A で拾う（取りこぼしを防ぐ）。KV は書いた内容がすぐ読めるとは限らないので、順番待ちの置き場には向かない
   - Workers の無料プランで Durable Objects と WebSocket がどこまで使えるかを、Cloudflare の公式ドキュメントで確かめてから決める
   - **測ること**：案ごとに、何もない状態で1時間あたりの LTE の通信量を測る（`ip -s link show wwan0` の差か、SORACOM のコンソール）
2. **写真（U31）**：写真の中身は、LINE から取り出せる期間に限りがある。Pi が何日も止まっていると取り出せなくなるおそれがある。Workers が受けたときに取り出して貯めるか、今のまま Pi が取り出すかを決める
   - Workers で取り出す場合は、`LINE_CHANNEL_ACCESS_TOKEN` も Workers の secret に置く
   - 保存先の容量の上限を確かめる
3. **クラウドに残すもの（U32）**：
   - Pi が受け取ったら、すぐ消す
   - 受け取られないまま残ったときの上限（例：7日）を決める。上限を過ぎたものは消し、消したことを Workers のログに残す
   - 貯めるのは、決めたグループのメッセージだけにする。メンションのない会話（`KOE_REPLY_FILTER=mention` で読まないもの）まで貯めるかも決める。中身がクラウドに残るので、少ないほうがよい

## 作ってほしいもの

- **Workers のコード**：リポジトリの `relay/` に置く（`wrangler.toml` と `src/`）
  - `POST /callback`：LINE から。署名が合わなければ 403
  - Pi 用の窓口（取り出す・受け取ったと返す、または WebSocket）：**Pi だけが使えるよう合言葉（トークン）で守る**。合言葉は Workers の secret と Pi の `.env` の両方に置く（例：`KOE_RELAY_URL`、`KOE_RELAY_TOKEN`）
  - ログに、お便りの本文を出さない
- **Pi のコード**：`app/relay.py`（取りに行く）
  - 起動したとき、届いたとき、30秒ごとの定期の仕事で取りに行く
  - 新しく受けたら、今の `webhook.Handler.on_received`（`keeper.wake`）と同じように、準備のスレッドを起こす
  - 通信が切れていても落ちない。つながったら続きから
- **切り替えのスイッチ**：`.env` で受け口の方式を選べるようにする（例：`KOE_INBOX_MODE=relay` か `tunnel`）。試作機で問題が出たら、すぐ今の方式に戻せるようにする
  - `relay` のときは `tunnel.sync_webhook()` と `tunnel.watch()` を**呼ばない**。呼ぶと LINE の Webhook URL が trycloudflare に書き戻されてしまう。`koe-tunnel` も止めて、`disable` する
  - `tunnel` のときは今のまま動く
- **試験**：`tests/` に足す
  - 署名の合わないものを受けない
  - SQLite に入れる前に落ちたら、もう一度受け取れる
  - 同じものを2回受けても、お便りは1件
  - 通信が切れても落ちない
  - Workers 側は `wrangler dev` などで確かめられる範囲でよい
- **README と仕様書**：決めたこと（U31・U32 の答え）、測った通信量、つまずいたことを書く。`.env.example` に新しい項目を足す（値は空のまま）

## 本人にやってもらうこと

- **Cloudflare のアカウントを作る**（無料）。Quick Tunnel と違い、Workers にはアカウントが要る。ドメインは要らない
- `wrangler login` と、secret の書き込み（`wrangler secret put …`）は本人が打つ。鍵はチャットに貼らない（CLAUDE.md）
- Pi の `.env` に `KOE_RELAY_URL`・`KOE_RELAY_TOKEN` を `nano` で書く
- LINE Developers のコンソールで、Webhook URL を Workers の `/callback` に替え、「検証」を押す。この URL は一度決めれば変わらない
- 合言葉（トークン）は、Pi で `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` を打って作り、本人が両方に書き写す

## 切り替えの順番

1. `KOE_INBOX_MODE=tunnel` のままコードを入れ、試験を通す。本番に配備しても今までどおり動くことを確かめる
2. Workers を配備し、`tools/fake_line_event.py` のように、署名つきの偽のイベントを Workers に送る。Pi で受け取れることを確かめる
3. `.env` を `KOE_INBOX_MODE=relay` にし、`koe-tunnel` を止める。LINE の Webhook URL を Workers に替える
4. LINE から1通送り、受話器で再生されるまでを確かめる
5. **Pi を止めているあいだに LINE から1通送り、Pi を起こすと届く**ことを確かめる（今はできないこと）
6. Wi-Fi を切って LTE だけでも 4 と 5 が動くことを確かめ、何もない状態で1時間あたりの通信量を測る

## 気をつけること

- trycloudflare は、作り直しが続くと 429 で断る。切り替えの試しのあいだ、`koe-tunnel` の再起動はなるべくしない
- 本番を再起動したら、`systemctl --user is-active koe-dayori` を確かめ、LINE から1通送って届くかを見る（`relay` にしたあとは `koe-tunnel` は止まっていてよい）
- お便りの本文と写真がクラウドを通るので、仕様書「10. プライバシーと安全」に、何をどれだけ残すかを書き足す
- 提出物は主催側がマーケティングに使える。Workers のコードは公開リポジトリに入るので、鍵やグループ ID をコードに書かない
