# Changelog

このプロジェクトの変更点を記録します。
書式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に、版の番号は [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [Unreleased]

## [0.2.0] - 2026-10-13

画面(GUI)と exe のビルドを足した版。測れる内容は 0.1.0 と同じで、マイク・回線・テスト音の測定は未実装、判定の閾値は未較正(null)のままです。GUI は合成データとスタブでしか確かめておらず、実機での確認はまだです。

### Added

- GUI(`python -m vccheck gui`。PySide6 が要る): 症状 → デバイスと測定内容 → 測定中 → 結果、の4画面と、開始できない画面、テスト音の確認・聞こえ方・設定ファイルの異常の3つのダイアログ。測定は別のスレッドで動かし、中止できる。結果の画面は結果の JSON だけから描く。色は使わず、状態は文字の印で示す
- GUI では、入力・出力のデバイスを一覧(QtMultimedia)から選び、名前を「申告」として結果に残す。録音・再生はしない
- マイク・回線・テスト音の測定が未実装のあいだ、GUI の3つのチェックを無効にし、その旨を画面に出す(画面設計書には無い文言で、この1つだけ)
- 共有用 Markdown と同じ内容をクリップボードにコピーするボタンと、結果のフォルダを開くボタン
- exe のビルド(`python tools/build_exe.py`): `dist\VcCheck.exe`(1ファイル・コンソールなし・署名なし)。引数なしで GUI、引数ありで CLI として動く。exe はリポジトリに入れていない
- exe のとき、`config\` は同梱の物を読む。exe の隣に `config\` があれば、そちらをフォルダごと優先する
- 実行層(`vccheck\runner.py`): CLI の `run` と GUI が同じ関数を呼ぶ。測れる内容(マイク・回線・テスト音)を1か所で持つ
- README の画面の画像(`docs\images\`。合成データから `tools\make_screenshots.py` で作る)
- 任意の依存 `gui`(`PySide6-Essentials` と `PySide6-Addons`)。CLI は今までどおり標準ライブラリだけで動く

### Changed

- `gui` コマンドは、未実装のエラー(終了コード 1)ではなく、画面を開く。PySide6 が無い環境では案内を出して終了コード 2
- `config\` の読み込みと結果の組み立てを、`__main__.py` から `runner.py` へ移した(CLI の動きは変えていない)
- `measure.json` の `results_dir` が相対パスのとき、exe では exe のあるフォルダから数える

### 未実装

- マイクの録音と測定、CLI でのデバイスの選択、回線の測定、テスト音と回り込みの測定、閾値の較正、「次に見る場所」の文、GUI の実機での確認

## [0.1.0] - 2026-10-13

最初の版。測定の大半は未実装で、判定の閾値はすべて未較正(null)です。

### Added

- 計算と判定: 音声の集計(dBFS・クリップ・DC オフセット・無音・パーセンタイル・Goertzel)、`thresholds.json` による判定、区間 S1〜S6 の状態、`rules.json` による原因の可能性(最大3つ)
- 出力: 結果の JSON(形の版 1)、Markdown、共有用 Markdown(デバイス名・ユーザー名・パス・ホスト名・IP アドレスを置き換える)。UTF-8(BOM なし)・LF で、既にあるファイルは上書きしない
- Windows の設定の読み取り(レジストリは読み取り専用): マイクのプライバシー許可、通信時の音量調整の設定値
- Discord の起動の有無(プロセス名の有無だけ)と、PC の CPU 使用率・メモリ使用率
- CLI: `run --skip-mic --skip-net`・`report`・`check`・`stats`・`--selftest`
- `config\` の初期ファイル: `thresholds.json`(数値はすべて null)、`measure.json`(仮の値)、`rules.json`(RL-1〜RL-12。RL-9 と RL-12 は無効)、`advice.json`(空)、`net_targets.json`(ホスト名は未確定で null)

### 未実装

- マイクの録音と測定、入出力デバイスの選択、回線の測定、テスト音と回り込みの測定、閾値の較正、「次に見る場所」の文、GUI
