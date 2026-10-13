# 純関数だけを置く: 正規化・dBFS・クリップ・DC・パーセンタイル・Goertzel・統計と、
# 正規化済みの配列からマイクの項目(mic.*)を作る集計(仕様書 §9.4)。
# 入出力をしない。受け取ったサンプルを保持しない(INV-2)。
from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from vccheck.model import Measure, RawItem, failed, join_notes, measured

DBFS_FLOOR: Final = -120.0
NOTE_FLOOR: Final = "下限丸め"
NOTE_NOISE_GATE: Final = "ドライバーのノイズゲートの可能性(未確認)"
NOTE_UNGATED: Final = "発話の検出は未較正のため確認していない"
REASON_ALL_ZERO: Final = "全サンプルが 0"
REASON_NO_SPEECH: Final = "声が検出できなかった(または無言の区間に声が入った)"

# サンプル形式の名前(Qt の呼び方)と、正規化の式。
_FORMATS: Final[dict[str, tuple[float, float]]] = {
    "Int16": (0.0, 32768.0),
    "Int32": (0.0, 2147483648.0),
    "Float": (0.0, 1.0),
    "UInt8": (128.0, 128.0),
}


def is_supported_format(sample_format: str) -> bool:
    return sample_format in _FORMATS


def normalize(samples: Sequence[float], sample_format: str) -> list[float]:
    """サンプルを −1.0〜1.0 にする。未対応の形式は ValueError。"""
    if sample_format not in _FORMATS:
        raise ValueError(f"未対応のサンプル形式: {sample_format}")
    offset, scale = _FORMATS[sample_format]
    return [(x - offset) / scale for x in samples]


def first_channel(samples: Sequence[float], channels: int) -> list[float]:
    """チャンネルが交互に並んだ配列から、先頭の1チャンネルだけを取り出す。"""
    if channels < 1:
        raise ValueError("チャンネル数が 1 未満")
    return list(samples[::channels])


def rms(samples: Sequence[float]) -> float:
    if not samples:
        raise ValueError("サンプルが無い")
    return math.sqrt(sum(x * x for x in samples) / len(samples))


def to_dbfs(amplitude: float) -> tuple[float, bool]:
    """20·log10(値)を小数第1位に丸める。0 以下と下限未満は −120.0 に丸め、True を返す。"""
    if amplitude <= 0:
        return DBFS_FLOOR, True
    value = 20.0 * math.log10(amplitude)
    if value < DBFS_FLOOR:
        return DBFS_FLOOR, True
    return round(value, 1) + 0.0, False


def window_rms(samples: Sequence[float], window: int) -> list[float]:
    """重なりのない窓ごとの RMS。窓に満たない端数は使わない。"""
    if window < 1:
        raise ValueError("窓の長さが 1 未満")
    return [rms(samples[i:i + window]) for i in range(0, len(samples) - window + 1, window)]


def nearest_rank(values: Sequence[float], percentile: float) -> float:
    """最近傍順位法: 昇順に並べた n 個の ceil(p/100·n) 番目。"""
    if not values:
        raise ValueError("値が無い")
    if not 0 < percentile <= 100:
        raise ValueError("パーセンタイルが 0 より大きく 100 以下でない")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100.0 * len(ordered)))
    return ordered[rank - 1]


def clip_runs(samples: Sequence[float], level: float, run_min: int) -> tuple[int, int]:
    """絶対値が level 以上のサンプルが run_min 個以上続く並びの (個数, サンプル数の合計)。"""
    events = 0
    total = 0
    run = 0
    for x in samples:
        if abs(x) >= level:
            run += 1
            continue
        if run >= run_min:
            events += 1
            total += run
        run = 0
    if run >= run_min:
        events += 1
        total += run
    return events, total


def dc_offset_pct(samples: Sequence[float]) -> float:
    if not samples:
        raise ValueError("サンプルが無い")
    return sum(samples) / len(samples) * 100.0


def is_all_zero(samples: Sequence[float]) -> bool:
    return all(x == 0 for x in samples)


def goertzel_amplitude(samples: Sequence[float], hz: float, rate: int) -> float:
    """hz の成分の振幅(正弦波の最大値に当たる大きさ)。"""
    count = len(samples)
    if count == 0 or rate <= 0:
        raise ValueError("サンプルが無い、またはサンプルレートが正でない")
    coeff = 2.0 * math.cos(2.0 * math.pi * hz / rate)
    s1 = 0.0
    s2 = 0.0
    for x in samples:
        s1, s2 = x + coeff * s1 - s2, s1
    power = s1 * s1 + s2 * s2 - coeff * s1 * s2
    return 2.0 * math.sqrt(max(power, 0.0)) / count


@dataclass(frozen=True)
class Summary:
    count: int
    minimum: float
    median: float
    maximum: float


def summarize(values: Sequence[float]) -> Summary:
    """件数・最小・中央値・最大(stats 用)。"""
    if not values:
        raise ValueError("値が無い")
    return Summary(len(values), min(values), statistics.median(values), max(values))


def _round(value: float, digits: int) -> float:
    return round(value, digits) + 0.0


def measure_mic(recording: Sequence[float], rate: int, params: Measure,
                speech_gate_db: float | None) -> list[RawItem]:
    """正規化済み・1チャンネルの録音(先頭の捨てる分を含む)から mic.* の8項目を作る。"""
    head = round(rate * params.discard_head_ms / 1000.0)
    n_silent = round(rate * params.silent_s)
    n_speech = round(rate * params.speech_s)
    window = round(rate * params.window_ms / 1000.0)
    if rate <= 0 or window < 1 or n_silent < 1 or n_speech < window:
        raise ValueError("サンプルレートと測定の長さの組み合わせが不正")
    if len(recording) < head + n_silent + n_speech:
        raise ValueError("録音が、捨てる分・無言・発話の合計より短い")
    silent = list(recording[head:head + n_silent])
    speech = list(recording[head + n_silent:head + n_silent + n_speech])
    body = silent + speech

    if is_all_zero(body):
        others = (
            "mic.noise_floor_dbfs", "mic.speech_level_dbfs", "mic.peak_dbfs",
            "mic.clip_events", "mic.clip_ms", "mic.snr_db", "mic.dc_offset_pct",
        )
        return [*(failed(i, REASON_ALL_ZERO) for i in others), measured("mic.all_zero", True)]

    noise, noise_floored = to_dbfs(rms(silent))
    levels = [to_dbfs(w) for w in window_rms(speech, window)]
    speech_level = nearest_rank([level for level, _ in levels], params.speech_percentile)
    speech_floored = any(floored for _, floored in levels)
    peak, peak_floored = to_dbfs(max(abs(x) for x in body))
    events, clip_samples = clip_runs(body, params.clip_level, params.clip_run_min)
    snr = _round(speech_level - noise, 1)

    noise_note = join_notes(
        NOTE_FLOOR if noise_floored else None,
        NOTE_NOISE_GATE if is_all_zero(silent) else None,
    )
    gate_note = NOTE_UNGATED if speech_gate_db is None else None
    if speech_gate_db is not None and snr < speech_gate_db:
        speech_items = [
            failed("mic.speech_level_dbfs", REASON_NO_SPEECH),
            failed("mic.snr_db", REASON_NO_SPEECH),
        ]
    else:
        speech_items = [
            measured("mic.speech_level_dbfs", speech_level,
                     join_notes(NOTE_FLOOR if speech_floored else None, gate_note)),
            measured("mic.snr_db", snr,
                     join_notes(NOTE_FLOOR if noise_floored or speech_floored else None,
                                gate_note)),
        ]
    return [
        measured("mic.noise_floor_dbfs", noise, noise_note),
        speech_items[0],
        measured("mic.peak_dbfs", peak, NOTE_FLOOR if peak_floored else None),
        measured("mic.clip_events", events),
        measured("mic.clip_ms", _round(clip_samples * 1000.0 / rate, 3)),
        speech_items[1],
        measured("mic.dc_offset_pct", _round(dc_offset_pct(silent), 3)),
        measured("mic.all_zero", False),
    ]
