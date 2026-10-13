# 画面の文言。画面設計書 §11(MSG-01〜31)と、各画面の表・図の文字列をそのまま置く。
# ここに無い文言を画面に出さない。例外は DEV-90 の1つだけ(この版の測定部の制限の知らせ)。
# 「問題なし」「合格」「大丈夫」の語は使わない(INV-6、AC-G5)。
from __future__ import annotations

from typing import Final

from vccheck.runner import (
    STAGE_MIC_SILENT,
    STAGE_MIC_SPEECH,
    STAGE_NET,
    STAGE_PREPARE,
    STAGE_SUMMARY,
    STAGE_TONE,
)

WINDOW_TITLE: Final = "Discord(VcCheck)"

# ---- §11 文言一覧 ----
MSG_01: Final = "当てはまる症状を選んでください。選ばなくても測れます"
MSG_02: Final = (
    "VcCheck が測れるのは Windows 側(マイク・スピーカー・回線・PC の負荷)だけです。"
    "Discord の内部と通話相手は測れません"
)
MSG_03: Final = "閾値の較正: {total} 項目中 {n} 項目(未較正の項目は「判定なし」と出ます)"
MSG_04: Final = (
    "Discord の設定で選んでいる入力と出力のデバイスを確認してから選んでください。"
    "選んだデバイスは「申告」として結果に残ります"
)
MSG_05: Final = "所要時間の目安: 約 {n} 秒(設定値の合計。準備と保存の時間は含みません)"
MSG_06: Final = "測定中の声は、Discord の通話に接続していると通話にも流れます"
MSG_07: Final = "測定内容を1つ以上選んでください"
MSG_08: Final = "Discord で自分をミュートしていても、マイクの測定はできます"
MSG_09: Final = "測定を中止しました(何も保存されていません)"
MSG_10: Final = "測定を準備しています"
MSG_11: Final = "黙ってください"
MSG_12: Final = "普通の声で話してください"
MSG_13: Final = "回線を測っています"
MSG_14: Final = "テスト音を再生しています"
MSG_15: Final = "結果をまとめています"
MSG_16: Final = (
    "テスト音が鳴ります。Discord の通話に接続していると、スピーカーの音がマイクに入って"
    "通話に流れることがあります。通話から出るか、Discord でミュートしてから進んでください"
)
MSG_17: Final = "テスト音の聞こえ方を選んでください"
MSG_18: Final = (
    "入力デバイスが見つかりません。マイクが接続されているか、"
    "Windows のサウンド設定で有効になっているかを確認してください"
)
MSG_19: Final = "QtMultimedia を読み込めないため、測定を始められません"
MSG_20: Final = "設定ファイル {file} の形が壊れています。測定を始めません"
MSG_21: Final = "結果を保存しました: {stem}"
MSG_22: Final = "結果を保存できませんでした({reason})。結果は画面にだけ表示しています"
MSG_23: Final = "同じ名前の結果があるため、保存しませんでした。結果は画面にだけ表示しています"
MSG_24: Final = (
    "共有用の結果をクリップボードにコピーしました。デバイス名とユーザー名は置き換えてあります"
)
MSG_25: Final = (
    "Windows 側では原因の可能性が見つからなかった。"
    "Discord の内部・相手側・通信のいずれかかもしれず、VcCheck からは判断できない"
)
MSG_26_REASON: Final = (
    "Discord 内部の入力感度・ノイズ抑制・音声サーバーとの往復時間・パケット損失・"
    "Discord 側のミュート状態は、外から読む公式の方法を確認できていないため測らない"
)
MSG_26: Final = "S5: " + MSG_26_REASON
MSG_27_REASON: Final = "通話相手のマイク・スピーカー・回線は測れない"
MSG_27: Final = "S6: " + MSG_27_REASON
MSG_28: Final = (
    "判定はすべて、私が thresholds.json に置いた値による。"
    "Discord の内部と通話相手は測っていない。"
)
MSG_29: Final = "測定できた項目がありません。理由は詳細の「測れなかった」の節を見てください"
# MSG-30: DLG-02 の4つのラジオ。(tone.heard に入れる値, 文言) の順。
MSG_30: Final[tuple[tuple[str, str], ...]] = (
    ("ok", "正常に聞こえた"),
    ("crackle", "音が割れていた"),
    ("quiet", "音が小さかった"),
    ("none", "聞こえなかった"),
)
MSG_31: Final = "テスト音の聞こえ方が申告されなかった"

# ---- 画面設計書に無い唯一の文言(この版の測定部が、マイク・回線・テスト音を測れない知らせ) ----
DEV_90: Final = (
    "この版では、マイク・回線・テスト音の測定はまだ実装していません。"
    "Windows の設定と PC の負荷だけを測ります"
)

# ---- 手順表示(STEP-01) ----
STEP_SYMPTOMS: Final = "手順 1 / 3  症状"
STEP_DEVICES: Final = "手順 2 / 3  デバイスと測定内容"
STEP_RUN: Final = "手順 3 / 3  測定"

# ---- SCR-01(§4 の図)。(症状の ID, 文言) の順が SYM-01〜08 ----
SYMPTOM_LABELS: Final[tuple[tuple[str, str], ...]] = (
    ("crackle", "自分の声が割れると言われた"),
    ("quiet", "自分の声が小さい・聞こえにくいと言われた"),
    ("muffled", "自分の声がこもる・遠いと言われた"),
    ("echo", "エコーする・自分の声が返ってくると言われた"),
    ("noise", "自分の声にノイズが乗ると言われた"),
    ("drop", "途切れる・ロボットのような声になる"),
    ("other_side", "相手の声が割れる・小さい"),
    ("duck", "通話中にゲームや音楽の音が小さくなる"),
)
BUTTON_NEXT: Final = "次へ (Enter)"
BUTTON_CLOSE: Final = "閉じる (Esc)"
BUTTON_CLOSE_BOTH: Final = "閉じる (Esc・Enter)"

# ---- SCR-02(§5 の図) ----
LABEL_INPUT: Final = "入力デバイス"
LABEL_OUTPUT: Final = "出力デバイス"
LABEL_CONTENTS: Final = "測る内容"
DEFAULT_SUFFIX: Final = " (既定)"
CHECK_MIC: Final = "マイクを測る(黙る {silent} 秒 → 話す {speech} 秒)"
CHECK_NET: Final = "回線を測る"
CHECK_TONE: Final = "出力のテスト音も確認する(音が鳴ります)"
BUTTON_BACK: Final = "戻る (Esc)"
BUTTON_START: Final = "測定を始める (Enter)"

# ---- SCR-03(§6) ----
STAGE_NAMES: Final[dict[str, str]] = {
    STAGE_PREPARE: "準備",
    STAGE_MIC_SILENT: "マイク: 黙る",
    STAGE_MIC_SPEECH: "マイク: 話す",
    STAGE_NET: "回線",
    STAGE_TONE: "テスト音",
    STAGE_SUMMARY: "まとめ",
}
STAGE_MESSAGES: Final[dict[str, str]] = {
    STAGE_PREPARE: MSG_10,
    STAGE_MIC_SILENT: MSG_11,
    STAGE_MIC_SPEECH: MSG_12,
    STAGE_NET: MSG_13,
    STAGE_TONE: MSG_14,
    STAGE_SUMMARY: MSG_15,
}
MARK_DONE: Final = "[完了]"
MARK_RUNNING: Final = "[実行中]"
MARK_WAITING: Final = "[待ち]"
MARK_SKIPPED: Final = "[飛ばした]"
BUTTON_CANCEL: Final = "中止 (Esc)"

# ---- SCR-04(§7) ----
RESULT_TITLE: Final = "通話の音の切り分け  {when}"
SYMPTOMS_LINE: Final = "症状: {names}"
SYMPTOMS_NONE: Final = "選ばなかった"
SYMPTOM_SEPARATOR: Final = "・"
DEVICES_LINE: Final = "入力 {input} / 出力 {output}"
DECLARED: Final = "(申告)"
DEFAULT_USED: Final = "(既定を使った)"
HEADING_SEGMENTS: Final = "切り分け"
SEGMENT_COLUMNS: Final = ("区間", "状態", "未チェックの項目")
COUNT_ITEMS: Final = "{n} 件"
HEADING_CANDIDATES: Final = "原因の可能性(最大 3 つ)"
EVIDENCE: Final = "根拠: "
EVIDENCE_SEPARATOR: Final = "、"
ADVICE: Final = "次に見る場所: "
HEADING_DETAILS: Final = "詳細"
SECTION_TITLE: Final = "[{word}] {n}件"
SECTION_EMPTY: Final = "なし"
VALUE_COLUMNS: Final = ("区間", "項目", "値", "確度", "判定", "根拠")
REASON_COLUMNS: Final = ("区間", "項目", "理由")
SAVED_TO: Final = "保存先: {path} ほか 2 件"
BUTTON_COPY: Final = "共有用をコピー"
BUTTON_OPEN_FOLDER: Final = "結果のフォルダを開く"
BUTTON_AGAIN: Final = "もう一度測る"
YES: Final = "はい"
NO: Final = "いいえ"
DASH: Final = "—"
UNIT_COUNT: Final = "回"
TONE_HEARD_VALUES: Final[dict[str, str]] = {
    "ok": "正常",
    "crackle": "割れる",
    "quiet": "小さい",
    "none": "聞こえない",
}

# ---- ダイアログ(§9) ----
BUTTON_PLAY: Final = "鳴らす"
BUTTON_DO_NOT_PLAY: Final = "鳴らさない"
BUTTON_ANSWER: Final = "回答"


def mark(word: str) -> str:
    """状態を文字の印にする(色を使わない。§10)。"""
    return f"[{word}]"
