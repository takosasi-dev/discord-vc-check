# 項目の一覧から、区間 S1〜S6 の状態を決める(仕様書 §9.5)。
# S5・S6 は常に未チェック。測れなかった項目を含む区間は「正常」にならない(INV-6)。
from __future__ import annotations

from collections.abc import Sequence

from vccheck.model import FIXED_UNCHECKED_SEGMENTS, SEGMENT_NAMES, Item, Segment


def segment_status(segment_id: str, items: Sequence[Item]) -> str:
    """優先順で最初に当たる物: 異常 → 注意 → 測れなかった → 未チェック → 正常 → 判定なし。"""
    if segment_id in FIXED_UNCHECKED_SEGMENTS:
        return "unchecked"
    measured = [i for i in items if i.status == "measured"]
    if any(i.verdict == "bad" for i in measured):
        return "bad"
    if any(i.verdict == "warn" for i in measured):
        return "warn"
    if any(i.status == "failed" for i in items):
        return "failed"
    if not measured:
        return "unchecked"
    if any(i.verdict == "ok" for i in measured):
        return "ok"
    return "none"


def summarize_segments(items: Sequence[Item]) -> list[Segment]:
    segments: list[Segment] = []
    for segment_id, name in SEGMENT_NAMES.items():
        mine = [i for i in items if i.segment == segment_id]
        segments.append(
            Segment(
                id=segment_id,
                name=name,
                status=segment_status(segment_id, mine),
                unchecked_count=sum(1 for i in mine if i.status == "unchecked"),
                item_ids=tuple(i.id for i in mine),
            )
        )
    return segments
