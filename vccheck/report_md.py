# 結果から人向けの Markdown と、共有用の Markdown を描く(仕様書 §9.6)。
# 7語の節を §9.5 の順に、0件の節も出す。状態は [異常] のような文字の印で示す。
# 共有用はデバイス名・ユーザー名・パス・ホスト名・IP アドレスを置き換える(INV-10)。
from __future__ import annotations

from datetime import datetime
from typing import Final

from vccheck.io_safe import MASK_INPUT, MASK_OUTPUT, Identity, local_identity, mask_text
from vccheck.model import (
    CONFIDENCES,
    MIC_ITEM_IDS,
    NET_ITEM_IDS,
    SYMPTOMS,
    WORD_LABELS,
    WORDS,
    Item,
    Result,
    Value,
)
from vccheck.rules import NO_CANDIDATE_TEXT

FOOTER: Final = (
    "- 判定はすべて、私が thresholds.json に置いた値による。"
    "Discord の内部と通話相手は測っていない。"
)
NO_SYMPTOM: Final = "選択なし"
NO_DEVICE: Final = "申告なし"
NO_ADVICE: Final = "未記入(advice.json に文が無い)"
EMPTY: Final = "なし"
DASH: Final = "—"
_VALUE_TABLE_WORDS: Final = ("bad", "warn", "ok", "none", "not_applicable")


def _cell(text: str) -> str:
    """表の中の | と改行をエスケープする。"""
    return text.replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>").replace(
        "\r", "<br>"
    )


def _line(text: str) -> str:
    return " ".join(text.split())


def format_value(value: Value | None, unit: str | None) -> str:
    if value is None:
        return DASH
    if isinstance(value, bool):
        shown = "はい" if value else "いいえ"
    else:
        shown = str(value)
    return f"{shown} {unit}" if unit else shown


def _minute(stamp: str) -> str:
    return datetime.fromisoformat(stamp).strftime("%Y-%m-%d %H:%M")


def _second(stamp: str) -> str:
    return datetime.fromisoformat(stamp).strftime("%Y-%m-%d %H:%M:%S")


def _evidence(item_id: str, items: dict[str, Item]) -> str:
    item = items.get(item_id)
    if item is None:
        return item_id
    if item.status == "measured":
        return f"{item.name} {format_value(item.value, item.unit)}"
    return f"{item.name} {WORD_LABELS[item.word]}"


def _section(key: str, label: str, items: list[Item]) -> list[str]:
    lines = [f"## [{label}] {len(items)}件"]
    if not items:
        lines.append(EMPTY)
        return lines
    if key in _VALUE_TABLE_WORDS:
        lines.append("| 項目 | 値 | 確度 | 判定 | 根拠 |")
        lines.append("|---|---|---|---|---|")
        for item in items:
            confidence = CONFIDENCES.get(item.confidence or "", DASH)
            lines.append(
                f"| {_cell(item.name)} | {_cell(format_value(item.value, item.unit))} "
                f"| {confidence} | {_cell(item.verdict_raw or DASH)} "
                f"| {_cell(item.note or item.reason or DASH)} |"
            )
        return lines
    lines.append("| 項目 | 理由 |")
    lines.append("|---|---|")
    for item in items:
        reason = item.reason or DASH
        if item.note:
            reason = f"{reason}({item.note})"
        lines.append(f"| {_cell(item.name)} | {_cell(reason)} |")
    return lines


def render_markdown(result: Result, *, share: bool = False,
                    identity: Identity | None = None) -> str:
    """Markdown を返す。share が True なら共有用(マスク済み)。"""
    cond = result.run.conditions
    by_id = {i.id: i for i in result.items}
    title = f"通話診断 {_minute(result.created_at)}"
    symptoms = " / ".join(SYMPTOMS.get(s, s) for s in result.run.symptoms) or NO_SYMPTOM
    if share:
        device_in = MASK_INPUT if cond.input_declared else NO_DEVICE
        device_out = MASK_OUTPUT if cond.output_declared else NO_DEVICE
    else:
        device_in = _line(cond.input_declared) if cond.input_declared else NO_DEVICE
        device_out = _line(cond.output_declared) if cond.output_declared else NO_DEVICE
    if cond.input_declared or cond.output_declared:
        devices = f"入力 {device_in} / 出力 {device_out}(申告)"
    else:
        devices = f"{NO_DEVICE}(この回はデバイスを選んでいない)"
    skipped = []
    if all(by_id[i].status == "unchecked" for i in MIC_ITEM_IDS if i in by_id):
        skipped.append("マイク")
    if all(by_id[i].status == "unchecked" for i in NET_ITEM_IDS if i in by_id):
        skipped.append("回線")
    lines = [
        "---",
        f"title: {title}",
        f"created: {_minute(result.created_at)}",
        f"measured: {_second(result.run.measured_at)}",
        "tags:",
        "  - 通話診断",
        "  - VcCheck",
        "vccheck: 1",
        "---",
        f"# {title}",
        "",
        f"- 症状: {symptoms}",
        f"- 申告したデバイス: {devices}",
        f"- 条件: 無言 {cond.silent_s:g} 秒 → 発話 {cond.speech_s:g} 秒、"
        f"回線は TCP {cond.net_attempts} 回 × 2 か所"
        + (f"(この回は{'・'.join(skipped)}を測っていない)" if skipped else ""),
        f"- 閾値の較正: {result.calibrated_total} 項目中 {result.calibrated} 項目"
        "(未較正の項目は判定なし)",
        "",
        "## 切り分け",
        "| 区間 | 状態 | 未チェックの項目 |",
        "|---|---|---|",
    ]
    for seg in result.segments:
        lines.append(
            f"| {seg.id} {_cell(seg.name)} | {WORD_LABELS[seg.status]} "
            f"| {seg.unchecked_count}件 |"
        )
    lines += ["", "## 原因の可能性(最大3つ)"]
    if not result.candidates:
        lines.append(NO_CANDIDATE_TEXT)
    for number, cand in enumerate(result.candidates, start=1):
        evidence = "、".join(_evidence(e, by_id) for e in cand.evidence)
        suffix = f"(根拠: {_line(evidence)})" if evidence else ""
        lines.append(f"{number}. {_line(cand.text)}{suffix}")
        lines.append(f"   次に見る場所: {_line(cand.advice) if cand.advice else NO_ADVICE}")
    lines += [
        "",
        "| " + " | ".join(label for _, label in WORDS) + " |",
        "|" + "---|" * len(WORDS),
        "| " + " | ".join(str(result.counts[key]) for key, _ in WORDS) + " |",
        "",
    ]
    for key, label in WORDS:
        lines += _section(key, label, [i for i in result.items if i.word == key])
    lines += ["", "---", FOOTER]
    text = "\n".join(lines) + "\n"
    if share:
        text = mask_text(
            text,
            input_name=cond.input_declared,
            output_name=cond.output_declared,
            identity=local_identity() if identity is None else identity,
        )
    return text
