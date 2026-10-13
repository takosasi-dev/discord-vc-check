# rules.json・advice.json の形の検査と評価。原因の可能性を最大3つ選ぶ(仕様書 §9.5)。
# 規則は判定結果と列挙値だけを見る。数値の閾値を持たない(INV-5)。
# 候補の文は rules.json の text だけから出し、「可能性」で終わる(INV-7)。
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from vccheck.model import (
    CANDIDATE_SUFFIX,
    ITEM_SPEC_BY_ID,
    MAX_CANDIDATES,
    STATUSES,
    SYMPTOMS,
    VERDICTS,
    Candidate,
    ConfigError,
    Item,
    is_number,
)

NO_CANDIDATE_TEXT: Final = (
    "Windows 側では原因の可能性が見つからなかった。"
    "Discord の内部・相手側・通信のいずれかかもしれず、VcCheck からは判断できない"
)
PLACEHOLDER_MARK: Final = "{{"
_RULE_KEYS: Final = ("id", "enabled", "symptoms", "when_all", "when_any", "text", "evidence")
_TARGETS: Final = ("item", "item_any", "item_all")
_CLAUSES: Final = ("verdict_in", "value_in", "status_in")


@dataclass(frozen=True)
class Condition:
    target: str
    items: tuple[str, ...]
    clause: str
    values: tuple[object, ...]


@dataclass(frozen=True)
class Rule:
    id: str
    enabled: bool
    symptoms: tuple[str, ...]
    when_all: tuple[Condition, ...]
    when_any: tuple[Condition, ...]
    text: str
    evidence: tuple[str, ...]


def _item_ids(where: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{where}: 項目 ID の空でない配列でない")
    for item_id in value:
        if item_id not in ITEM_SPEC_BY_ID:
            raise ConfigError(f"{where}: 存在しない項目 ID {item_id!r}")
    return tuple(str(v) for v in value)


def _parse_condition(where: str, obj: object) -> Condition:
    if not isinstance(obj, dict):
        raise ConfigError(f"{where}: object でない")
    targets = [k for k in obj if k in _TARGETS]
    clauses = [k for k in obj if k in _CLAUSES]
    if len(targets) != 1 or len(clauses) != 1 or len(obj) != 2:
        raise ConfigError(
            f"{where}: item・item_any・item_all のどれか1つと、"
            "verdict_in・value_in・status_in のどれか1つだけを持つ形でない"
        )
    target, clause = targets[0], clauses[0]
    items = _item_ids(where, [obj[target]]) if target == "item" else _item_ids(
        where, obj[target]
    )
    values = obj[clause]
    if not isinstance(values, list) or not values:
        raise ConfigError(f"{where}: {clause} が空でない配列でない")
    if clause == "verdict_in" and any(v not in VERDICTS for v in values):
        raise ConfigError(f"{where}: verdict_in に ok・warn・bad・none 以外がある")
    if clause == "status_in" and any(v not in STATUSES for v in values):
        raise ConfigError(f"{where}: status_in に不正な状態がある")
    if clause == "value_in" and any(
        not (isinstance(v, (str, bool)) or is_number(v)) for v in values
    ):
        raise ConfigError(f"{where}: value_in に数・文字列・真偽以外がある")
    return Condition(str(target), items, str(clause), tuple(values))


def parse_rules(obj: object) -> list[Rule]:
    name = "rules.json"
    if not isinstance(obj, list):
        raise ConfigError(f"{name}: 配列でない")
    rules: list[Rule] = []
    seen: set[str] = set()
    for index, one in enumerate(obj):
        where = f"{name}[{index}]"
        if not isinstance(one, dict):
            raise ConfigError(f"{where}: object でない")
        for key in one:
            if key not in _RULE_KEYS:
                raise ConfigError(f"{where}: 未知の鍵 {key!r}")
        for key in _RULE_KEYS:
            if key not in one:
                raise ConfigError(f"{where}: 鍵 {key!r} が無い")
        rule_id = one["id"]
        if not isinstance(rule_id, str) or not rule_id or rule_id in seen:
            raise ConfigError(f"{where}: id が空でない文字列でない、または重複している")
        seen.add(rule_id)
        where = f"{name} {rule_id}"
        if not isinstance(one["enabled"], bool):
            raise ConfigError(f"{where}: enabled が真偽でない")
        symptoms = one["symptoms"]
        if not isinstance(symptoms, list) or any(s not in SYMPTOMS for s in symptoms):
            raise ConfigError(f"{where}: symptoms が症状の ID の配列でない")
        groups: dict[str, tuple[Condition, ...]] = {}
        for key in ("when_all", "when_any"):
            raw = one[key]
            if not isinstance(raw, list):
                raise ConfigError(f"{where}: {key} が配列でない")
            groups[key] = tuple(
                _parse_condition(f"{where} {key}[{i}]", c) for i, c in enumerate(raw)
            )
        text = one["text"]
        if not isinstance(text, str) or not text.endswith(CANDIDATE_SUFFIX):
            raise ConfigError(f"{where}: text が「{CANDIDATE_SUFFIX}」で終わらない")
        evidence = one["evidence"]
        if not isinstance(evidence, list):
            raise ConfigError(f"{where}: evidence が配列でない")
        for item_id in evidence:
            if item_id not in ITEM_SPEC_BY_ID:
                raise ConfigError(f"{where}: evidence に存在しない項目 ID {item_id!r}")
        if one["enabled"] and not groups["when_all"] and not groups["when_any"]:
            raise ConfigError(f"{where}: 有効な規則なのに条件が1つも無い")
        rules.append(
            Rule(
                id=rule_id,
                enabled=one["enabled"],
                symptoms=tuple(str(s) for s in symptoms),
                when_all=groups["when_all"],
                when_any=groups["when_any"],
                text=text,
                evidence=tuple(str(e) for e in evidence),
            )
        )
    return rules


def parse_advice(obj: object, rules: Sequence[Rule]) -> dict[str, str]:
    """{規則 ID: 次に見る場所の文}。置き換え前の {{ が残っていれば壊れている扱い。"""
    name = "advice.json"
    if not isinstance(obj, dict):
        raise ConfigError(f"{name}: object でない")
    known = {r.id for r in rules}
    advice: dict[str, str] = {}
    for key, text in obj.items():
        if key not in known:
            raise ConfigError(f"{name}: rules.json に無い規則 ID {key!r}")
        if not isinstance(text, str) or not text:
            raise ConfigError(f"{name}: {key} が空でない文字列でない")
        if PLACEHOLDER_MARK in text:
            raise ConfigError(f"{name}: {key} に置き換え前の {PLACEHOLDER_MARK} が残っている")
        advice[str(key)] = text
    return advice


def _same(left: object, right: object) -> bool:
    """型を混ぜずに比べる(true と 1 を同じ値にしない)。"""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, str) or isinstance(right, str):
        return isinstance(left, str) and isinstance(right, str) and left == right
    return left == right


def _holds_for(item: Item | None, cond: Condition) -> bool:
    if item is None:
        return False
    if cond.clause == "verdict_in":
        return item.verdict in cond.values
    if cond.clause == "status_in":
        return item.status in cond.values
    return item.status == "measured" and any(_same(item.value, v) for v in cond.values)


def _holds(cond: Condition, items: Mapping[str, Item]) -> bool:
    results = [_holds_for(items.get(i), cond) for i in cond.items]
    return any(results) if cond.target == "item_any" else all(results)


def rule_holds(rule: Rule, items: Mapping[str, Item]) -> bool:
    """when_all は全条件成立、when_any は(あれば)1つ以上成立。条件が無い規則は成立しない。"""
    if not rule.when_all and not rule.when_any:
        return False
    if not all(_holds(c, items) for c in rule.when_all):
        return False
    return not rule.when_any or any(_holds(c, items) for c in rule.when_any)


def _id_key(rule_id: str) -> tuple[tuple[int, int, str], ...]:
    """規則 ID の昇順(RL-2 を RL-10 より先にする)。"""
    return tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part)
        for part in re.split(r"(\d+)", rule_id)
        if part
    )


def _worst_rank(rule: Rule, items: Mapping[str, Item]) -> int:
    verdicts = {items[i].verdict for i in rule.evidence if i in items}
    if "bad" in verdicts:
        return 0
    return 1 if "warn" in verdicts else 2


def pick_candidates(items: Sequence[Item], symptoms: Sequence[str], rules: Sequence[Rule],
                    advice: Mapping[str, str]) -> list[Candidate]:
    """成立した規則を、症状の一致 → 根拠の最悪値 → 規則 ID の順に並べ、上位3件を返す。"""
    by_id = {i.id: i for i in items}
    chosen = set(symptoms)
    matched = [r for r in rules if r.enabled and rule_holds(r, by_id)]
    matched.sort(
        key=lambda r: (
            0 if chosen and chosen.intersection(r.symptoms) else 1,
            _worst_rank(r, by_id),
            _id_key(r.id),
        )
    )
    return [
        Candidate(r.id, r.text, "estimated", r.evidence, advice.get(r.id))
        for r in matched[:MAX_CANDIDATES]
    ]
