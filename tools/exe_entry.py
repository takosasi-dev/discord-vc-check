# exe の入口。PyInstaller が最初に動かすファイルで、vccheck の main を呼ぶだけ。
# 引数なしなら画面(GUI)、引数ありなら CLI として動く(判断は vccheck/__main__.py)。
from __future__ import annotations

from vccheck.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
