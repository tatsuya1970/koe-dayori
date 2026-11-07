"""フックスイッチ（GPIO17）を読み、受話器を「取った」「置いた」を phone に伝える（仕様書 3.3.1）。

- 押しボタン版でも同じコード（押す＝取る、離す＝置く）。KOE_HOOK_PRESSED_IS_OFF_HOOK で向きを変える
- チャタリングは gpiozero の bounce_time で落とす
- 置き直しで切れないよう、「置いた」は HOOK_SETTLE_SECONDS 続いてから伝える

※ 11/7 Mac で作成。Pi の実機で向きとチャタリングを確かめる。
"""

import logging
import threading

from app import config

log = logging.getLogger(__name__)


class Hook:
    def __init__(self, phone, pin=None):
        from gpiozero import Button  # Pi の上だけで読み込む

        self.phone = phone
        self._timer = None
        self._button = Button(pin or config.HOOK_GPIO, pull_up=True, bounce_time=0.05)
        if config.HOOK_PRESSED_IS_OFF_HOOK:
            self._button.when_pressed = self._lifted
            self._button.when_released = self._placed
        else:
            self._button.when_pressed = self._placed
            self._button.when_released = self._lifted

    def is_off_hook(self):
        return self._button.is_pressed == config.HOOK_PRESSED_IS_OFF_HOOK

    def _lifted(self):
        if self._timer:  # 置いてすぐ取り直した：置いたことにしない
            self._timer.cancel()
            self._timer = None
            return
        log.info("受話器を取りました")
        self.phone.lift()

    def _placed(self):
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(config.HOOK_SETTLE_SECONDS, self._settled)
        self._timer.start()

    def _settled(self):
        self._timer = None
        log.info("受話器を置きました")
        self.phone.hang_up()
