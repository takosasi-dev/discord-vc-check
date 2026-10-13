# 項目・区間・候補の型と、7語・4確度・症状・項目の一覧(仕様書 §9.4)の定義。
# measure.json・net_targets.json の形の検査と、結果 JSON(形の版 1)の
# 変換・検査(check が見る規則。§9.2)もここに置く。
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, TypeAlias

FORMAT_VERSION: Final = 1

Value: TypeAlias = int | float | str | bool

STATUSES: Final = ("measured", "failed", "unchecked", "not_applicable")
VERDICTS: Final = ("ok", "warn", "bad", "none")
CONFIDENCES: Final[dict[str, str]] = {
    "measured": "実測",
    "reference": "参考値",
    "estimated": "推定",
    "reported": "申告",
}

# 7語。並びは §9.5 の順(Markdown の節の順)。
WORDS: Final[tuple[tuple[str, str], ...]] = (
    ("bad", "異常"),
    ("warn", "注意"),
    ("failed", "測れなかった"),
    ("unchecked", "未チェック"),
    ("not_applicable", "対象外"),
    ("ok", "正常"),
    ("none", "判定なし"),
)
WORD_LABELS: Final[dict[str, str]] = dict(WORDS)
SEGMENT_STATUSES: Final = ("bad", "warn", "failed", "unchecked", "ok", "none")

SEGMENT_NAMES: Final[dict[str, str]] = {
    "S1": "自分のマイク(Windows の入力)",
    "S2": "自分のスピーカー(Windows の出力)",
    "S3": "回線(TCP の接続時間)",
    "S4": "PC の負荷",
    "S5": "Discord の内部",
    "S6": "通話相手",
}
FIXED_UNCHECKED_SEGMENTS: Final = ("S5", "S6")

# 症状(FR-1)。表示の文は FR-1 の説明から「と言われた」を除いた物。
SYMPTOMS: Final[dict[str, str]] = {
    "crackle": "自分の声が割れる",
    "quiet": "自分の声が小さい・聞こえにくい",
    "muffled": "こもる・遠い",
    "echo": "エコー・自分の声が返ってくる",
    "noise": "ノイズが乗る",
    "drop": "途切れる・ロボット声",
    "other_side": "相手の声が割れる・小さい",
    "duck": "通話中に他の音が小さくなる",
}

REASON_SKIPPED: Final = "実行時に指定されて飛ばした"
REASON_TONE_UNCONFIRMED: Final = "テスト音は確認なしでは再生しない"
REASON_TONE_NOT_DECLARED: Final = "テスト音の聞こえ方が申告されなかった"
REASON_S5: Final = (
    "Discord 内部の入力感度・ノイズ抑制・音声サーバーとの往復時間・パケット損失・"
    "Discord 側のミュート状態は、外から読む公式の方法を確認できていないため測らない"
)
REASON_S6: Final = "通話相手のマイク・スピーカー・回線は測れない"


class ConfigError(Exception):
    """config\\ のファイルの形が壊れている(終了コード 1)。"""


@dataclass(frozen=True)
class ItemSpec:
    id: str
    segment: str
    name: str
    unit: str | None
    confidence: str
    judge: Literal["number", "enum", "none"]
    threshold_key: str | None = None
    fixed_reason: str | None = None


def _net_specs(side: str, label: str) -> tuple[ItemSpec, ...]:
    def one(leaf: str, name: str, unit: str, key: str | None) -> ItemSpec:
        return ItemSpec(
            f"net.{side}.{leaf}",
            "S3",
            f"{name}({label})",
            unit,
            "reference",
            "number" if key else "none",
            key,
        )

    return (
        one("rtt_p50_ms", "接続時間の中央値", "ms", None),
        one("rtt_p95_ms", "接続時間の 95 パーセンタイル", "ms", "net.rtt_p95_ms"),
        one("rtt_max_ms", "接続時間の最大", "ms", None),
        one("jitter_ms", "接続時間の揺れ", "ms", "net.jitter_ms"),
        one("fail_pct", "接続の失敗の割合", "%", "net.fail_pct"),
    )


def _num(item_id: str, segment: str, name: str, unit: str) -> ItemSpec:
    return ItemSpec(item_id, segment, name, unit, "measured", "number", item_id)


def _plain(item_id: str, segment: str, name: str, unit: str | None = None) -> ItemSpec:
    return ItemSpec(item_id, segment, name, unit, "measured", "none")


def _enum(item_id: str, segment: str, name: str, confidence: str = "measured") -> ItemSpec:
    return ItemSpec(item_id, segment, name, None, confidence, "enum")


ITEM_SPECS: Final[tuple[ItemSpec, ...]] = (
    _plain("env.discord_running", "S5", "Discord の起動"),
    _enum("priv.mic_machine", "S1", "マイクの許可(PC 全体)"),
    _enum("priv.mic_user", "S1", "マイクの許可(ユーザー)"),
    _enum("priv.mic_discord", "S1", "マイクの許可(Discord)"),
    _plain("duck.pref", "S2", "通信時の音量調整の設定値"),
    _plain("dev.in.format", "S1", "入力デバイスの形式"),
    _plain("dev.out.format", "S2", "出力デバイスの形式"),
    _num("mic.noise_floor_dbfs", "S1", "無言のときの音の大きさ", "dBFS"),
    _num("mic.speech_level_dbfs", "S1", "発話の大きさ", "dBFS"),
    _plain("mic.peak_dbfs", "S1", "最大の振幅", "dBFS"),
    _num("mic.clip_events", "S1", "クリップの回数", "回"),
    _num("mic.clip_ms", "S1", "クリップの長さの合計", "ms"),
    _num("mic.snr_db", "S1", "発話と無言の差", "dB"),
    _num("mic.dc_offset_pct", "S1", "DC オフセット", "%FS"),
    _plain("mic.all_zero", "S1", "全サンプルが 0"),
    *_net_specs("gw", "Discord 側"),
    *_net_specs("ref", "比較先"),
    _num("sys.cpu_avg_pct", "S4", "CPU 使用率の平均", "%"),
    _num("sys.cpu_max_pct", "S4", "CPU 使用率の最大", "%"),
    _num("sys.mem_load_pct", "S4", "メモリ使用率の最大", "%"),
    _enum("tone.heard", "S2", "テスト音の聞こえ方", "reported"),
    _num("echo.tone_in_mic_dbfs", "S1", "マイクに入ったテスト音の大きさ", "dBFS"),
    ItemSpec("s5.discord_internal", "S5", "Discord の内部の状態", None, "measured", "none",
             fixed_reason=REASON_S5),
    ItemSpec("s6.remote", "S6", "通話相手の環境", None, "measured", "none",
             fixed_reason=REASON_S6),
)
ITEM_SPEC_BY_ID: Final[dict[str, ItemSpec]] = {s.id: s for s in ITEM_SPECS}
ITEM_IDS: Final[tuple[str, ...]] = tuple(s.id for s in ITEM_SPECS)
MIC_ITEM_IDS: Final[tuple[str, ...]] = tuple(i for i in ITEM_IDS if i.startswith("mic."))
NET_ITEM_IDS: Final[tuple[str, ...]] = tuple(i for i in ITEM_IDS if i.startswith("net."))
TONE_ITEM_IDS: Final = ("tone.heard", "echo.tone_in_mic_dbfs")
# thresholds.json の数値の項目の鍵(speech_gate_db を除く 13)。
THRESHOLD_KEYS: Final[tuple[str, ...]] = tuple(
    dict.fromkeys(s.threshold_key for s in ITEM_SPECS if s.threshold_key)
)
ENUM_ITEM_IDS: Final[tuple[str, ...]] = tuple(s.id for s in ITEM_SPECS if s.judge == "enum")


@dataclass(frozen=True)
class RawItem:
    """判定の前の項目。measured 以外は value を持たない(INV-1)。"""

    id: str
    status: str
    value: Value | None = None
    reason: str | None = None
    note: str | None = None


def measured(item_id: str, value: Value, note: str | None = None) -> RawItem:
    return RawItem(item_id, "measured", value, None, note)


def failed(item_id: str, reason: str, note: str | None = None) -> RawItem:
    return RawItem(item_id, "failed", None, reason, note)


def unchecked(item_id: str, reason: str, note: str | None = None) -> RawItem:
    return RawItem(item_id, "unchecked", None, reason, note)


def fixed_unchecked_items() -> list[RawItem]:
    """S5・S6 の固定の未チェック項目(FR-12)。"""
    return [unchecked(s.id, s.fixed_reason) for s in ITEM_SPECS if s.fixed_reason]


def join_notes(*notes: str | None) -> str | None:
    parts = [n for n in notes if n]
    return " / ".join(parts) if parts else None


@dataclass(frozen=True)
class Item:
    id: str
    segment: str
    name: str
    unit: str | None
    status: str
    value: Value | None
    confidence: str | None
    verdict: str | None
    verdict_raw: str | None
    reason: str | None
    note: str | None

    @property
    def word(self) -> str:
        """7語のどれか(WORDS の鍵)。"""
        if self.status == "measured":
            return self.verdict if self.verdict in ("ok", "warn", "bad") else "none"
        return self.status


@dataclass(frozen=True)
class Segment:
    id: str
    name: str
    status: str
    unchecked_count: int
    item_ids: tuple[str, ...]


@dataclass(frozen=True)
class Candidate:
    rule_id: str
    text: str
    confidence: str
    evidence: tuple[str, ...]
    advice: str | None


@dataclass(frozen=True)
class Conditions:
    input_declared: str | None
    output_declared: str | None
    input_default_used: bool | None
    channels_used: int | None
    silent_s: float
    speech_s: float
    net_attempts: int


@dataclass(frozen=True)
class RunInfo:
    measured_at: str
    symptoms: tuple[str, ...]
    conditions: Conditions


@dataclass(frozen=True)
class Result:
    app_version: str
    created_at: str
    run: RunInfo
    calibrated: int
    calibrated_total: int
    items: tuple[Item, ...]
    segments: tuple[Segment, ...]
    candidates: tuple[Candidate, ...]
    counts: dict[str, int]


def count_words(items: Iterable[Item]) -> dict[str, int]:
    counts = {key: 0 for key, _ in WORDS}
    for item in items:
        counts[item.word] += 1
    return counts


def result_to_obj(result: Result) -> dict[str, object]:
    c = result.run.conditions
    return {
        "vccheck": FORMAT_VERSION,
        "app_version": result.app_version,
        "created_at": result.created_at,
        "run": {
            "measured_at": result.run.measured_at,
            "symptoms": list(result.run.symptoms),
            "conditions": {
                "input_declared": c.input_declared,
                "output_declared": c.output_declared,
                "input_default_used": c.input_default_used,
                "channels_used": c.channels_used,
                "silent_s": c.silent_s,
                "speech_s": c.speech_s,
                "net_attempts": c.net_attempts,
            },
        },
        "thresholds_calibrated": {
            "calibrated": result.calibrated,
            "total": result.calibrated_total,
        },
        "items": [
            {
                "id": i.id,
                "segment": i.segment,
                "name": i.name,
                "unit": i.unit,
                "status": i.status,
                "value": i.value,
                "confidence": i.confidence,
                "verdict": i.verdict,
                "verdict_raw": i.verdict_raw,
                "reason": i.reason,
                "note": i.note,
            }
            for i in result.items
        ],
        "segments": [
            {
                "id": s.id,
                "name": s.name,
                "status": s.status,
                "unchecked_count": s.unchecked_count,
                "item_ids": list(s.item_ids),
            }
            for s in result.segments
        ],
        "candidates": [
            {
                "rule_id": k.rule_id,
                "text": k.text,
                "confidence": k.confidence,
                "evidence": list(k.evidence),
                "advice": k.advice,
            }
            for k in result.candidates
        ],
        "counts": dict(result.counts),
    }


# ---- 結果 JSON の検査(§9.2) ----

_TOP_KEYS: Final = (
    "vccheck", "app_version", "created_at", "run", "thresholds_calibrated",
    "items", "segments", "candidates", "counts",
)
_RUN_KEYS: Final = ("measured_at", "symptoms", "conditions")
_CONDITION_KEYS: Final = (
    "input_declared", "output_declared", "input_default_used", "channels_used",
    "silent_s", "speech_s", "net_attempts",
)
_ITEM_KEYS: Final = (
    "id", "segment", "name", "unit", "status", "value", "confidence",
    "verdict", "verdict_raw", "reason", "note",
)
_SEGMENT_KEYS: Final = ("id", "name", "status", "unchecked_count", "item_ids")
_CANDIDATE_KEYS: Final = ("rule_id", "text", "confidence", "evidence", "advice")
MAX_CANDIDATES: Final = 3
CANDIDATE_SUFFIX: Final = "可能性"


def is_int(x: object) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def is_number(x: object) -> bool:
    """有限の数(bool を除く)。NaN と Infinity は数として認めない。"""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _is_text(x: object) -> bool:
    return isinstance(x, str) and x != ""


def _is_datetime(x: object) -> bool:
    if not isinstance(x, str):
        return False
    try:
        datetime.fromisoformat(x)
    except ValueError:
        return False
    return True


def _check_keys(out: list[str], where: str, obj: Mapping[str, object],
                keys: Sequence[str]) -> None:
    for key in obj:
        if key not in keys:
            out.append(f"{where}: 未知の鍵 {key!r}")
    for key in keys:
        if key not in obj:
            out.append(f"{where}: 鍵 {key!r} が無い")


def _check_item(out: list[str], index: int, item: object, seen: set[str]) -> None:
    where = f"items[{index}]"
    if not isinstance(item, dict):
        out.append(f"{where}: object でない")
        return
    item_id = item.get("id")
    if _is_text(item_id):
        where = f"items[{index}] {item_id}"
        if item_id in seen:
            out.append(f"{where}: id が重複している")
        seen.add(str(item_id))
    else:
        out.append(f"{where}: id が空でない文字列でない")
    _check_keys(out, where, item, _ITEM_KEYS)
    if item.get("segment") not in SEGMENT_NAMES:
        out.append(f"{where}: segment が S1〜S6 でない")
    if not _is_text(item.get("name")):
        out.append(f"{where}: name が空でない文字列でない")
    for key in ("unit", "note"):
        if item.get(key) is not None and not isinstance(item.get(key), str):
            out.append(f"{where}: {key} が文字列でも null でもない")
    value = item.get("value")
    if isinstance(value, float) and not math.isfinite(value):
        out.append(f"{where}: value が NaN または Infinity")
    elif value is not None and not isinstance(value, (int, float, str, bool)):
        out.append(f"{where}: value が数・文字列・真偽・null のどれでもない")
    status = item.get("status")
    verdict = item.get("verdict")
    raw = item.get("verdict_raw")
    if status not in STATUSES:
        out.append(f"{where}: status が不正")
    elif status == "measured":
        if value is None:
            out.append(f"{where}: measured なのに value が null")
        if item.get("confidence") not in CONFIDENCES:
            out.append(f"{where}: measured なのに confidence が不正")
        if verdict not in VERDICTS:
            out.append(f"{where}: measured なのに verdict が不正")
        elif verdict == "none":
            if raw is not None:
                out.append(f"{where}: verdict が none なのに verdict_raw がある")
        elif not _is_text(raw):
            out.append(f"{where}: verdict が {verdict} なのに verdict_raw が無い")
        if item.get("reason") is not None:
            out.append(f"{where}: measured なのに reason が null でない")
    else:
        for key in ("value", "confidence", "verdict", "verdict_raw"):
            if item.get(key) is not None:
                out.append(f"{where}: {status} なのに {key} が null でない")
        if not _is_text(item.get("reason")):
            out.append(f"{where}: {status} なのに reason が空")


def _check_run(out: list[str], run: object) -> None:
    if not isinstance(run, dict):
        out.append("run: object でない")
        return
    _check_keys(out, "run", run, _RUN_KEYS)
    if not _is_datetime(run.get("measured_at")):
        out.append("run.measured_at: 日時として読めない")
    symptoms = run.get("symptoms")
    if not isinstance(symptoms, list) or any(s not in SYMPTOMS for s in symptoms):
        out.append("run.symptoms: 症状の ID の配列でない")
    elif len(set(symptoms)) != len(symptoms):
        out.append("run.symptoms: 重複がある")
    cond = run.get("conditions")
    if not isinstance(cond, dict):
        out.append("run.conditions: object でない")
        return
    _check_keys(out, "run.conditions", cond, _CONDITION_KEYS)
    for key in ("input_declared", "output_declared"):
        if cond.get(key) is not None and not _is_text(cond.get(key)):
            out.append(f"run.conditions.{key}: 空でない文字列でも null でもない")
    if cond.get("input_default_used") is not None and not isinstance(
        cond.get("input_default_used"), bool
    ):
        out.append("run.conditions.input_default_used: 真偽でも null でもない")
    if cond.get("channels_used") is not None and not is_int(cond.get("channels_used")):
        out.append("run.conditions.channels_used: 整数でも null でもない")
    for key in ("silent_s", "speech_s"):
        if not is_number(cond.get(key)):
            out.append(f"run.conditions.{key}: 数でない")
    if not is_int(cond.get("net_attempts")):
        out.append("run.conditions.net_attempts: 整数でない")


def _check_segments(out: list[str], segments: object) -> None:
    if not isinstance(segments, list):
        out.append("segments: 配列でない")
        return
    seen: dict[str, object] = {}
    for index, seg in enumerate(segments):
        where = f"segments[{index}]"
        if not isinstance(seg, dict):
            out.append(f"{where}: object でない")
            continue
        _check_keys(out, where, seg, _SEGMENT_KEYS)
        seg_id = seg.get("id")
        if seg_id not in SEGMENT_NAMES:
            out.append(f"{where}: id が S1〜S6 でない")
        elif seg_id in seen:
            out.append(f"{where}: id が重複している")
        else:
            seen[str(seg_id)] = seg.get("status")
        if not _is_text(seg.get("name")):
            out.append(f"{where}: name が空でない文字列でない")
        if seg.get("status") not in SEGMENT_STATUSES:
            out.append(f"{where}: status が不正")
        count = seg.get("unchecked_count")
        if not is_int(count) or (isinstance(count, int) and count < 0):
            out.append(f"{where}: unchecked_count が 0 以上の整数でない")
        ids = seg.get("item_ids")
        if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
            out.append(f"{where}: item_ids が文字列の配列でない")
    for seg_id in FIXED_UNCHECKED_SEGMENTS:
        if seg_id not in seen:
            out.append(f"segments: {seg_id} が無い")
        elif seen[seg_id] != "unchecked":
            out.append(f"segments: {seg_id} の status が unchecked でない")


def _check_candidates(out: list[str], candidates: object) -> None:
    if not isinstance(candidates, list):
        out.append("candidates: 配列でない")
        return
    if len(candidates) > MAX_CANDIDATES:
        out.append(f"candidates: {MAX_CANDIDATES} 件を超えている")
    for index, cand in enumerate(candidates):
        where = f"candidates[{index}]"
        if not isinstance(cand, dict):
            out.append(f"{where}: object でない")
            continue
        _check_keys(out, where, cand, _CANDIDATE_KEYS)
        if not _is_text(cand.get("rule_id")):
            out.append(f"{where}: rule_id が空でない文字列でない")
        text = cand.get("text")
        if not isinstance(text, str) or not text.endswith(CANDIDATE_SUFFIX):
            out.append(f"{where}: text が「{CANDIDATE_SUFFIX}」で終わらない")
        if cand.get("confidence") != "estimated":
            out.append(f"{where}: confidence が estimated でない")
        evidence = cand.get("evidence")
        if not isinstance(evidence, list) or any(not isinstance(e, str) for e in evidence):
            out.append(f"{where}: evidence が文字列の配列でない")
        if cand.get("advice") is not None and not _is_text(cand.get("advice")):
            out.append(f"{where}: advice が空でない文字列でも null でもない")


def check_result(obj: object) -> list[str]:
    """§9.2 の規則の違反を1行ずつ返す。空なら違反なし。"""
    out: list[str] = []
    if not isinstance(obj, dict):
        return ["最上位が object でない"]
    _check_keys(out, "最上位", obj, _TOP_KEYS)
    if not is_int(obj.get("vccheck")) or obj.get("vccheck") != FORMAT_VERSION:
        out.append(f"vccheck が {FORMAT_VERSION} でない")
    if not _is_text(obj.get("app_version")):
        out.append("app_version: 空でない文字列でない")
    if not _is_datetime(obj.get("created_at")):
        out.append("created_at: 日時として読めない")
    _check_run(out, obj.get("run"))
    cal = obj.get("thresholds_calibrated")
    if not isinstance(cal, dict):
        out.append("thresholds_calibrated: object でない")
    else:
        _check_keys(out, "thresholds_calibrated", cal, ("calibrated", "total"))
        done, total = cal.get("calibrated"), cal.get("total")
        if not (isinstance(done, int) and isinstance(total, int) and is_int(done)
                and is_int(total) and 0 <= done <= total):
            out.append("thresholds_calibrated: 0 <= calibrated <= total の整数でない")
    items = obj.get("items")
    item_count: int | None = None
    if not isinstance(items, list):
        out.append("items: 配列でない")
    else:
        item_count = len(items)
        seen: set[str] = set()
        for index, item in enumerate(items):
            _check_item(out, index, item, seen)
    _check_segments(out, obj.get("segments"))
    _check_candidates(out, obj.get("candidates"))
    counts = obj.get("counts")
    if not isinstance(counts, dict):
        out.append("counts: object でない")
    else:
        keys = tuple(key for key, _ in WORDS)
        _check_keys(out, "counts", counts, keys)
        values = [counts.get(key) for key in keys]
        numbers = [v for v in values if isinstance(v, int) and is_int(v) and v >= 0]
        if len(numbers) != len(values):
            out.append("counts: 0 以上の整数でない値がある")
        elif item_count is not None and sum(numbers) != item_count:
            out.append(
                f"counts: 7つの和 {sum(numbers)} が items の数 {item_count} と一致しない"
            )
    return out


def result_from_obj(obj: Mapping[str, object]) -> Result:
    """check_result を通った object を Result にする。通っていない物を渡してはならない。"""
    if check_result(obj):
        raise ValueError("check の違反がある結果は読み込めない")
    run = _as_map(obj["run"])
    cond = _as_map(run["conditions"])
    cal = _as_map(obj["thresholds_calibrated"])
    items = tuple(
        Item(
            id=str(i["id"]), segment=str(i["segment"]), name=str(i["name"]),
            unit=_opt_str(i["unit"]), status=str(i["status"]), value=_as_value(i["value"]),
            confidence=_opt_str(i["confidence"]), verdict=_opt_str(i["verdict"]),
            verdict_raw=_opt_str(i["verdict_raw"]), reason=_opt_str(i["reason"]),
            note=_opt_str(i["note"]),
        )
        for i in map(_as_map, _as_list(obj["items"]))
    )
    segments = tuple(
        Segment(
            id=str(s["id"]), name=str(s["name"]), status=str(s["status"]),
            unchecked_count=_as_int(s["unchecked_count"]),
            item_ids=tuple(str(x) for x in _as_list(s["item_ids"])),
        )
        for s in map(_as_map, _as_list(obj["segments"]))
    )
    candidates = tuple(
        Candidate(
            rule_id=str(k["rule_id"]), text=str(k["text"]), confidence=str(k["confidence"]),
            evidence=tuple(str(x) for x in _as_list(k["evidence"])),
            advice=_opt_str(k["advice"]),
        )
        for k in map(_as_map, _as_list(obj["candidates"]))
    )
    default_used = cond["input_default_used"]
    channels = cond["channels_used"]
    return Result(
        app_version=str(obj["app_version"]),
        created_at=str(obj["created_at"]),
        run=RunInfo(
            measured_at=str(run["measured_at"]),
            symptoms=tuple(str(s) for s in _as_list(run["symptoms"])),
            conditions=Conditions(
                input_declared=_opt_str(cond["input_declared"]),
                output_declared=_opt_str(cond["output_declared"]),
                input_default_used=default_used if isinstance(default_used, bool) else None,
                channels_used=None if channels is None else _as_int(channels),
                silent_s=_as_float(cond["silent_s"]),
                speech_s=_as_float(cond["speech_s"]),
                net_attempts=_as_int(cond["net_attempts"]),
            ),
        ),
        calibrated=_as_int(cal["calibrated"]),
        calibrated_total=_as_int(cal["total"]),
        items=items,
        segments=segments,
        candidates=candidates,
        counts={key: _as_int(_as_map(obj["counts"])[key]) for key, _ in WORDS},
    )


def _as_map(x: object) -> Mapping[str, object]:
    if not isinstance(x, dict):
        raise ValueError("object でない")
    return x


def _as_list(x: object) -> list[object]:
    if not isinstance(x, list):
        raise ValueError("配列でない")
    return x


def _as_int(x: object) -> int:
    if not isinstance(x, int) or isinstance(x, bool):
        raise ValueError("整数でない")
    return x


def _as_float(x: object) -> float:
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        raise ValueError("数でない")
    return x


def _opt_str(x: object) -> str | None:
    if x is None:
        return None
    if not isinstance(x, str):
        raise ValueError("文字列でない")
    return x


def _as_value(x: object) -> Value | None:
    if x is None or isinstance(x, (int, float, str, bool)):
        return x
    raise ValueError("value の型が不正")


# ---- measure.json・net_targets.json(§9.3) ----


@dataclass(frozen=True)
class Measure:
    """測定のパラメーター。値はすべて仮(仕様書 Q-17)。判定の閾値は持たない。"""

    provisional: bool
    silent_s: float
    speech_s: float
    discard_head_ms: float
    window_ms: float
    speech_percentile: float
    clip_level: float
    clip_run_min: int
    net_attempts: int
    net_interval_ms: float
    net_timeout_ms: float
    net_min_success: int
    sys_interval_ms: float
    tone_hz: float
    tone_dbfs: float
    tone_s: float
    discord_process_names: tuple[str, ...]
    results_dir: str | None


_MEASURE_NUMBERS: Final = (
    "silent_s", "speech_s", "discard_head_ms", "window_ms", "speech_percentile",
    "clip_level", "net_interval_ms", "net_timeout_ms", "sys_interval_ms",
    "tone_hz", "tone_dbfs", "tone_s",
)
_MEASURE_INTS: Final = ("clip_run_min", "net_attempts", "net_min_success")
_MEASURE_KEYS: Final = (
    "_provisional", *_MEASURE_NUMBERS, *_MEASURE_INTS, "discord_process_names", "results_dir",
)


def parse_measure(obj: object) -> Measure:
    name = "measure.json"
    if not isinstance(obj, dict):
        raise ConfigError(f"{name}: object でない")
    problems: list[str] = []
    _check_keys(problems, name, obj, _MEASURE_KEYS)
    if problems:
        raise ConfigError(problems[0])
    if not isinstance(obj["_provisional"], bool):
        raise ConfigError(f"{name}: _provisional が真偽でない")
    num: dict[str, float] = {}
    for key in _MEASURE_NUMBERS:
        value = obj[key]
        if not isinstance(value, (int, float)) or not is_number(value):
            raise ConfigError(f"{name}: {key} が数でない")
        num[key] = value
    ints: dict[str, int] = {}
    for key in _MEASURE_INTS:
        value = obj[key]
        if not isinstance(value, int) or not is_int(value) or value < 1:
            raise ConfigError(f"{name}: {key} が 1 以上の整数でない")
        ints[key] = value
    for key in ("silent_s", "speech_s", "window_ms", "net_timeout_ms", "sys_interval_ms",
                "tone_hz", "tone_s"):
        if num[key] <= 0:
            raise ConfigError(f"{name}: {key} が正の数でない")
    for key in ("discard_head_ms", "net_interval_ms"):
        if num[key] < 0:
            raise ConfigError(f"{name}: {key} が負の数")
    if not 0 < num["speech_percentile"] <= 100:
        raise ConfigError(f"{name}: speech_percentile が 0 より大きく 100 以下でない")
    if not 0 < num["clip_level"] <= 1:
        raise ConfigError(f"{name}: clip_level が 0 より大きく 1 以下でない")
    if num["tone_dbfs"] > 0:
        raise ConfigError(f"{name}: tone_dbfs が 0 を超えている")
    names = obj["discord_process_names"]
    if not isinstance(names, list) or not names or any(not _is_text(n) for n in names):
        raise ConfigError(f"{name}: discord_process_names が空でない文字列の配列でない")
    results_dir = obj["results_dir"]
    if results_dir is not None and not (isinstance(results_dir, str) and results_dir):
        raise ConfigError(f"{name}: results_dir が空でない文字列でも null でもない")
    return Measure(
        provisional=obj["_provisional"],
        silent_s=num["silent_s"], speech_s=num["speech_s"],
        discard_head_ms=num["discard_head_ms"], window_ms=num["window_ms"],
        speech_percentile=num["speech_percentile"], clip_level=num["clip_level"],
        clip_run_min=ints["clip_run_min"], net_attempts=ints["net_attempts"],
        net_interval_ms=num["net_interval_ms"], net_timeout_ms=num["net_timeout_ms"],
        net_min_success=ints["net_min_success"], sys_interval_ms=num["sys_interval_ms"],
        tone_hz=num["tone_hz"], tone_dbfs=num["tone_dbfs"], tone_s=num["tone_s"],
        discord_process_names=tuple(str(n) for n in names),
        results_dir=results_dir,
    )


@dataclass(frozen=True)
class NetTarget:
    """回線の測定先。host が None なら未確定(仕様書 §8.5 の実物の後に決める)。"""

    host: str | None
    port: int


@dataclass(frozen=True)
class NetTargets:
    gw: NetTarget
    ref: NetTarget


def parse_net_targets(obj: object) -> NetTargets:
    name = "net_targets.json"
    if not isinstance(obj, dict):
        raise ConfigError(f"{name}: object でない")
    problems: list[str] = []
    _check_keys(problems, name, obj, ("gw", "ref"))
    if problems:
        raise ConfigError(problems[0])
    targets: dict[str, NetTarget] = {}
    for key in ("gw", "ref"):
        one = obj[key]
        if not isinstance(one, dict):
            raise ConfigError(f"{name}: {key} が object でない")
        _check_keys(problems, f"{name}: {key}", one, ("host", "port"))
        if problems:
            raise ConfigError(problems[0])
        host, port = one["host"], one["port"]
        if host is not None and not (isinstance(host, str) and host):
            raise ConfigError(f"{name}: {key}.host が空でない文字列でも null でもない")
        if not isinstance(port, int) or not is_int(port) or not 1 <= port <= 65535:
            raise ConfigError(f"{name}: {key}.port が 1〜65535 の整数でない")
        targets[key] = NetTarget(host, port)
    return NetTargets(targets["gw"], targets["ref"])
