# Changelog

このプロジェクトの変更点を記録します。
書式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に、版の番号は [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [Unreleased]

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
