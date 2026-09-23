"""通り芯(グリッド線)・通り芯番号(丸囲みラベル)の分類。

判定方針(詳細はdocs/adr/0006-grid-line-classification.mdを参照):

1. `curve`のうち、点列の重心距離のばらつきが小さい閉じたポリゴンを
   「円」候補として抽出する
2. 円の中心近傍にある`char`を連結し、英数字1〜3文字程度の妥当な
   ラベル(通り芯番号)としてマッチするか検証する
3. 円の中心と同じy座標(水平)またはx座標(垂直)を共有する`line`が
   ページの広い範囲にわたって複数存在するかを確認し、存在すれば
   その線分の集合を「通り芯」として円ラベルと結び付ける

半径のような絶対値の決め打ちを避け、「妥当なラベルが存在する」
「対応する長い共線クラスタが存在する」という2条件の組み合わせのみで
判定する(単なる装飾的な円や寸法線との誤検出を避けるため)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ..schema import VectorRecord
from .geometry import MIN_SPAN_RATIO, find_chain_runs, find_circles, record_center

_LABEL_PATTERN = re.compile(r"^[A-Za-z]{0,3}[0-9]{0,3}$")
_SEQUENCE_PATTERN = re.compile(r"^([A-Za-z]*)([0-9]*)$")

_LABEL_SEARCH_RADIUS_FACTOR = 1.5


@dataclass(frozen=True)
class GridLabel:
    text: str
    center: tuple[float, float]
    radius: float
    circle_index: int
    char_indices: tuple[int, ...]


@dataclass(frozen=True)
class GridLine:
    label: GridLabel
    axis: Literal["horizontal", "vertical"]
    coordinate: float
    segment_indices: tuple[int, ...]
    span: tuple[float, float]
    sequence_group: str
    sequence_number: int | None
    sequence_plausible: bool


def _find_labels(records: list[VectorRecord]) -> list[GridLabel]:
    labels: list[GridLabel] = []
    for circle_index, center, radius in find_circles(records):
        cx, cy = center
        search_radius = radius * _LABEL_SEARCH_RADIUS_FACTOR
        nearby: list[tuple[int, float, str]] = []
        for index, record in enumerate(records):
            if record["object_type"] != "char":
                continue
            char_center = record_center(record)
            if char_center is None:
                continue
            chx, chy = char_center
            if ((chx - cx) ** 2 + (chy - cy) ** 2) ** 0.5 <= search_radius:
                text = record.get("text") or ""
                nearby.append((index, chx, text))

        if not nearby:
            continue

        nearby.sort(key=lambda item: item[1])
        label_text = "".join(item[2] for item in nearby)
        if not label_text or not _LABEL_PATTERN.match(label_text):
            continue

        labels.append(
            GridLabel(
                text=label_text,
                center=center,
                radius=radius,
                circle_index=circle_index,
                char_indices=tuple(item[0] for item in nearby),
            )
        )
    return labels


def classify_grid_lines(
    records: list[VectorRecord],
    page_width: float | None = None,
    page_height: float | None = None,
) -> list[GridLine]:
    labels = _find_labels(records)

    raw_lines: list[dict] = []
    for label in labels:
        cx, cy = label.center
        best: tuple[Literal["horizontal", "vertical"], list[int], float, float] | None = None

        for axis, coordinate, page_dim in (
            ("horizontal", cy, page_height),
            ("vertical", cx, page_width),
        ):
            # 通り芯は無関係な線に分断され、複数のランとして検出されることが
            # あるため、その座標にある有効なラン(交互リズムを持つもの)を
            # すべて合算した上で、通り芯としての規模(スパン)を判定する
            runs = find_chain_runs(records, coordinate, axis)
            if not runs:
                continue
            matched = [index for run in runs for index in run.indices]
            lo = min(run.lo for run in runs)
            hi = max(run.hi for run in runs)
            if page_dim is not None and (hi - lo) < page_dim * MIN_SPAN_RATIO:
                continue
            if best is None or len(matched) > len(best[1]):
                best = (axis, matched, lo, hi)

        if best is None:
            continue

        axis, matched, lo, hi = best
        coordinate = cy if axis == "horizontal" else cx
        seq_match = _SEQUENCE_PATTERN.match(label.text)
        sequence_group = seq_match.group(1) if seq_match else ""
        sequence_number = (
            int(seq_match.group(2)) if seq_match and seq_match.group(2) else None
        )

        raw_lines.append(
            {
                "label": label,
                "axis": axis,
                "coordinate": coordinate,
                "segment_indices": tuple(matched),
                "span": (lo, hi),
                "sequence_group": sequence_group,
                "sequence_number": sequence_number,
            }
        )

    by_group: dict[str, list[int]] = {}
    for i, item in enumerate(raw_lines):
        by_group.setdefault(item["sequence_group"], []).append(i)

    plausible = [False] * len(raw_lines)
    for indices in by_group.values():
        numbers = [
            raw_lines[i]["sequence_number"]
            for i in indices
            if raw_lines[i]["sequence_number"] is not None
        ]
        # 同一系列内で数値部分に重複がなければ「連番として妥当」とみなす
        # (間隔が飛んでいても構わない。厳密な連続性はフィルタ条件にしない)
        looks_sequential = len(numbers) >= 2 and len(set(numbers)) == len(numbers)
        for i in indices:
            plausible[i] = looks_sequential

    return [
        GridLine(
            label=item["label"],
            axis=item["axis"],
            coordinate=item["coordinate"],
            segment_indices=item["segment_indices"],
            span=item["span"],
            sequence_group=item["sequence_group"],
            sequence_number=item["sequence_number"],
            sequence_plausible=plausible[i],
        )
        for i, item in enumerate(raw_lines)
    ]
