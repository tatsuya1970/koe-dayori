"""声だより 全体の流れ。systemd から起動する（deploy/koe-dayori.service）。

    .venv/bin/python -m app.main              # 本番（フックスイッチ・人感センサーを GPIO で読む）
    .venv/bin/python -m app.main --keyboard   # Enter キーで受話器を取る／置く（改造前の確認用）

動かすもの
- 受話器の流れ（phone）と、フックスイッチ（hook）
- LINE の受け口（webhook, 127.0.0.1:8000 → Tailscale Funnel）
- モニター表示（display, 127.0.0.1:8001 → Chromium のキオスクモード）
- 人感センサー（sensor.Watcher）
- 定期の仕事（Housekeeper）：再送、お便りの準備、新着のチャイム、「まだ聞いていません」、見守り、今日の様子、古い記録の削除
"""

import argparse
import logging
import signal
import threading
from datetime import datetime

from app import clock, config, daily, db as koe_db, display, inbox, notify, outbox, pipeline, sensor, tunnel, webhook

log = logging.getLogger("koe")

INTERVAL_SECONDS = 30


class Housekeeper:
    """定期の仕事。お便りが届いたときは wake() で、待たずにすぐ回す。"""

    def __init__(self, audio):
        self.audio = audio
        self._wake = threading.Event()
        self._night_arrivals = False  # 夜に届いたお便りがある（朝7時にチャイムを鳴らす）

    def wake(self):
        self._wake.set()

    def run_forever(self):
        while True:
            try:
                self.run_once()
            except Exception:
                log.exception("定期の仕事で止まりました（次の回に続けます）")
            self._wake.wait(INTERVAL_SECONDS)
            self._wake.clear()

    def run_once(self, now=None):
        now = now or datetime.now()
        # LINE の受け口をトンネルの今の URL に合わせる（失敗しても、ほかの仕事は続ける）
        try:
            tunnel.sync_webhook()
        except Exception as e:
            log.warning("LINE の受け口を登録し直せません（次の回に再試行）: %s", e)
        conn = koe_db.connect()
        out = outbox.connect()
        try:
            # 親の声：通信が切れていた分を送り直す
            outbox.flush(out, dry_run=config.DRY_RUN)

            # 家族からのお便り：言い換え・写真の説明をして、未再生にする
            inbox.prepare(conn, on_ready=self.on_new_letter)

            # 夜に届いたお便りのチャイムは、朝に1回だけ
            if self._night_arrivals and clock.is_daytime(now):
                self._night_arrivals = False
                if inbox.unplayed(conn):
                    self.audio.chime_speaker()

            # まだ聞いていません
            for letter in inbox.due_reminders(conn, now):
                inbox.mark_reminded(conn, letter["id"])
                if config.NOTIFY_UNPLAYED:
                    received = datetime.fromisoformat(letter["received_at"])
                    notify.add(conn, "unplayed", (
                        f"📭 {letter['sender_name']}さんのお便り（{clock.hm(received)}に届いたもの）を、"
                        f"{config.PARENT_NAME}はまだ聞いていません（{config.UNPLAYED_REMIND_HOURS:g}時間たちました）"
                    ), now)

            # 見守り
            for kind, text in sensor.check(conn, now):
                notify.add(conn, kind, text, now)

            # 今日の様子（1日1回）
            if daily.due(now) and koe_db.mark_once(conn, "daily", daily.today_key(now), now):
                notify.add(conn, "daily", daily.build(conn, out, now), now)

            # 30日より古い記録を消す（1日1回）
            if koe_db.mark_once(conn, "purge", daily.today_key(now), now):
                inbox.purge(conn, now)
                sensor.purge(conn, now)

            notify.flush(conn)
        finally:
            conn.close()
            out.close()

    def on_new_letter(self, letter):
        log.info("お便りの準備ができました（%sさんから）", letter["sender_name"])
        if clock.is_daytime(datetime.now()):
            self.audio.chime_speaker()
        else:
            self._night_arrivals = True


def on_played(letter):
    log.info("お便りを再生しました（%sさんから）", letter["sender_name"])
    if not config.NOTIFY_PLAYED:
        return
    conn = koe_db.connect()
    try:
        played = datetime.fromisoformat(letter["played_at"])
        notify.add(conn, "played", f"👂 {config.PARENT_NAME}が、{letter['sender_name']}さんのお便りを聞きました（{clock.hm(played)}）")
    finally:
        conn.close()


def make_send():
    def send(wav):
        conn = outbox.connect()
        try:
            return pipeline.handle_recording(wav, conn, dry_run=config.DRY_RUN)
        finally:
            conn.close()
    return send


def keyboard_loop(phone):
    print("Enter で受話器を取る／置く。Ctrl+C で終わる")
    off_hook = False
    while True:
        input()
        off_hook = not off_hook
        print("〔受話器を取りました〕" if off_hook else "〔受話器を置きました〕")
        if off_hook:
            phone.lift()
        else:
            phone.hang_up()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--keyboard", action="store_true", help="フックスイッチの代わりに Enter キーを使う")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from app.audio import Audio
    from app.phone import Phone

    audio = Audio()
    keeper = Housekeeper(audio)
    phone = Phone(audio, send=make_send(), display_state=display.STATE, on_played=on_played)

    webhook.start(on_received=keeper.wake)
    display.start()
    threading.Thread(target=keeper.run_forever, name="housekeeper", daemon=True).start()

    hook = None
    if not args.keyboard:
        from app.hook import Hook
        hook = Hook(phone)
    if config.WATCH_ENABLED:
        try:
            watcher = sensor.Watcher()  # noqa: F841（gpiozero が見張り続けるよう、参照を持っておく）
        except Exception as e:
            log.warning("人感センサーを読めません: %s", e)

    audio.say_speaker("準備ができました")
    log.info("準備ができました")
    if hook and hook.is_off_hook():
        log.warning("受話器が外れたまま起動しました。置き直すと使えます")

    if args.keyboard:
        keyboard_loop(phone)
    else:
        signal.pause()


if __name__ == "__main__":
    main()
