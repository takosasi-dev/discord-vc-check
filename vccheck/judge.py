# thresholds.json の形の検査と、それを使った判定(ok / warn / bad / none)。
# 閾値の数値はこのファイルに書かない。warn と bad の片方でも null なら判定なし(INV-5)。
# 値が無い項目には確度も判定も付けない(INV-1)。
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

from vccheck.model import (
    ENUM_ITEM_IDS,
    ITEM_IDS,
    ITEM_SPEC_BY_ID,
    THRESHOLD_KEYS,
    ConfigError,
    Item,
    ItemSpec,
    RawItem,
    is_number,
    join_notes,
)

NOTE_UNCALIBRATED: Final = "閾値が未較正"
NOTE_NOT_JUDGED: Final = "判定しない項目"
NOTE_ENUM_UNKNOWN: Final = "enum_verdicts に無い値"
_ENUM_RESULTS: Final = ("ok", "warn", "bad")


@dataclass(frozen=True)
class NumberThreshold:
    direction: str
    warn: float | None
    bad: float | None
    use_abs: bool = False

    @property
    def calibrated(self) -> bool:
        return self.warn is not None and self.bad is not None


@dataclass(frozen=True)
class Thresholds:
    numbers: Mapping[str, NumberThreshold]
    speech_gate_db: float | None
    enum_verdicts: Mapping[str, Mapping[str, str]]

    def calibrated_counts(self) -> tuple[int, int]:
        """(warn と bad の両方が数値の項目の数, 数値の項目の数)。"""
        done = sum(1 for t in self.numbers.values() if t.calibrated)
        return done, len(self.numbers)


def _opt_number(name: str, where: str, value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or not is_number(value):
        raise ConfigError(f"{name}: {where} が数でも null でもない")
    return value


def parse_thresholds(obj: object) -> Thresholds:
    name = "thresholds.json"
    if not isinstance(obj, dict):
        raise ConfigError(f"{name}: object でない")
    expected = (*THRESHOLD_KEYS, "speech_gate_db", "enum_verdicts")
    for key in obj:
        if key not in expected:
            raise ConfigError(f"{name}: 未知の鍵 {key!r}")
    for key in expected:
        if key not in obj:
            raise ConfigError(f"{name}: 鍵 {key!r} が無い")
    numbers: dict[str, NumberThreshold] = {}
    for key in THRESHOLD_KEYS:
        one = obj[key]
        if not isinstance(one, dict):
            raise ConfigError(f"{name}: {key} が object でない")
        for sub in one:
            if sub not in ("direction", "warn", "bad", "use"):
                raise ConfigError(f"{name}: {key} に未知の鍵 {sub!r}")
        for sub in ("direction", "warn", "bad"):
            if sub not in one:
                raise ConfigError(f"{name}: {key} に鍵 {sub!r} が無い")
        direction = one["direction"]
        if direction not in ("high", "low"):
            raise ConfigError(f"{name}: {key}.direction が high でも low でもない")
        if "use" in one and one["use"] != "abs":
            raise ConfigError(f"{name}: {key}.use が abs でない")
        numbers[key] = NumberThreshold(
            direction=str(direction),
            warn=_opt_number(name, f"{key}.warn", one["warn"]),
            bad=_opt_number(name, f"{key}.bad", one["bad"]),
            use_abs="use" in one,
        )
    gate = obj["speech_gate_db"]
    if not isinstance(gate, dict) or list(gate) != ["min"]:
        raise ConfigError(f"{name}: speech_gate_db が {{\"min\": 値}} の形でない")
    enums = obj["enum_verdicts"]
    if not isinstance(enums, dict):
        raise ConfigError(f"{name}: enum_verdicts が object でない")
    if sorted(enums) != sorted(ENUM_ITEM_IDS):
        raise ConfigError(f"{name}: enum_verdicts の鍵が列挙値の項目と一致しない")
    table: dict[str, dict[str, str]] = {}
    for item_id, mapping in enums.items():
        if not isinstance(mapping, dict):
            raise ConfigError(f"{name}: enum_verdicts.{item_id} が object でない")
        for value, verdict in mapping.items():
            if verdict not in _ENUM_RESULTS:
                raise ConfigError(
                    f"{name}: enum_verdicts.{item_id}.{value} が ok・warn・bad のどれでもない"
                )
        table[str(item_id)] = {str(k): str(v) for k, v in mapping.items()}
    return Thresholds(numbers, _opt_number(name, "speech_gate_db.min", gate["min"]), table)


def _fmt(value: float) -> str:
    return str(value)


def _judge_number(value: float, th: NumberThreshold) -> tuple[str, str | None, str | None]:
    """(verdict, verdict_raw, note)。境界: 値が warn ちょうどなら ok。"""
    if th.warn is None or th.bad is None:
        return "none", None, NOTE_UNCALIBRATED
    used = abs(value) if th.use_abs else value
    shown = f"|{_fmt(value)}|" if th.use_abs else _fmt(value)
    if th.direction == "high":
        if used > th.bad:
            return "bad", f"{shown} > {_fmt(th.bad)}", None
        if used > th.warn:
            return "warn", f"{shown} > {_fmt(th.warn)}", None
        return "ok", f"{shown} <= {_fmt(th.warn)}", None
    if used < th.bad:
        return "bad", f"{shown} < {_fmt(th.bad)}", None
    if used < th.warn:
        return "warn", f"{shown} < {_fmt(th.warn)}", None
    return "ok", f"{shown} >= {_fmt(th.warn)}", None


def _judge_measured(spec: ItemSpec, raw: RawItem,
                    thresholds: Thresholds) -> tuple[str, str | None, str | None]:
    value = raw.value
    if spec.judge == "number":
        if not isinstance(value, (int, float)) or not is_number(value):
            raise ValueError(f"{spec.id}: 数値の項目の値が有限の数でない")
        if spec.threshold_key is None:
            raise ValueError(f"{spec.id}: 閾値の鍵が無い")
        return _judge_number(value, thresholds.numbers[spec.threshold_key])
    if spec.judge == "enum":
        table = thresholds.enum_verdicts.get(spec.id, {})
        if isinstance(value, str) and value in table:
            return table[value], f"{value} = {table[value]}", None
        return "none", None, NOTE_ENUM_UNKNOWN
    if isinstance(value, float) and not is_number(value):
        raise ValueError(f"{spec.id}: 値が有限の数でない")
    return "none", None, NOTE_NOT_JUDGED


def judge_item(raw: RawItem, thresholds: Thresholds) -> Item:
    spec = ITEM_SPEC_BY_ID[raw.id]
    if raw.status != "measured":
        if raw.status not in ("failed", "unchecked", "not_applicable"):
            raise ValueError(f"{raw.id}: status が不正")
        if raw.value is not None:
            raise ValueError(f"{raw.id}: 値が無い状態なのに値がある")
        if not raw.reason:
            raise ValueError(f"{raw.id}: 値が無い状態なのに理由が無い")
        return Item(spec.id, spec.segment, spec.name, spec.unit, raw.status,
                    None, None, None, None, raw.reason, raw.note)
    if raw.value is None:
        raise ValueError(f"{raw.id}: measured なのに値が無い")
    if raw.reason is not None:
        raise ValueError(f"{raw.id}: measured なのに理由がある")
    verdict, verdict_raw, note = _judge_measured(spec, raw, thresholds)
    return Item(spec.id, spec.segment, spec.name, spec.unit, "measured", raw.value,
                spec.confidence, verdict, verdict_raw, None, join_notes(raw.note, note))


def judge_items(raws: Iterable[RawItem], thresholds: Thresholds) -> list[Item]:
    """全項目(§9.4)をちょうど1回ずつ受け取り、項目の一覧の順に並べて判定する。"""
    by_id: dict[str, RawItem] = {}
    for raw in raws:
        if raw.id not in ITEM_SPEC_BY_ID:
            raise ValueError(f"項目の一覧に無い ID: {raw.id}")
        if raw.id in by_id:
            raise ValueError(f"項目が重複している: {raw.id}")
        by_id[raw.id] = raw
    missing = [i for i in ITEM_IDS if i not in by_id]
    if missing:
        raise ValueError(f"項目が足りない: {', '.join(missing)}")
    return [judge_item(by_id[i], thresholds) for i in ITEM_IDS]
