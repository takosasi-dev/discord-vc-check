# pytest の共通設定。GUI のテストで実際のウィンドウが出ないように、
# Qt を読み込む前に、画面なし(offscreen)の描画に切り替える。
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
