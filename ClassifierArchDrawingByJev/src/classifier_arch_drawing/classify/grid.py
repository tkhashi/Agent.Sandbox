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
import statistics
from dataclasses import dataclass
from typing import Literal

from ..schema import VectorRecord

_LABEL_PATTERN = re.compile(r"^[A-Za-z]{0,3}[0-9]{0,3}$")
_SEQUENCE_PATTERN = re.compile(r"^([A-Za-z]*)([0-9]*)$")

_CIRCLE_MIN_POINTS = 8
_CIRCLE_RELATIVE_STD_MAX = 0.05
_LABEL_SEARCH_RADIUS_FACTOR = 1.5
_AXIS_ALIGN_TOLERANCE = 0.5  # ptの高さ/幅がこれ以下なら軸並行とみなす
_COORDINATE_TOLERANCE = 0.75  # pt。共線とみなす座標差の許容値
_MIN_CLUSTER_SEGMENTS = 5
_MIN_SPAN_RATIO = 0.1  # ページ寸法に対する最低スパン比


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


def _record_center(record: VectorRecord) -> tuple[float, float] | None:
    x0, x1 = record.get("x0"), record.get("x1")
    top, bottom = record.get("top"), record.get("bottom")
    if None in (x0, x1, top, bottom):
        return None
    return ((x0 + x1) / 2, (top + bottom) / 2)


def _find_circles(records: list[VectorRecord]) -> list[tuple[int, tuple[float, float], float]]:
    circles: list[tuple[int, tuple[float, float], float]] = []
    for index, record in enumerate(records):
        if record["object_type"] != "curve":
            continue
        pts = record.get("pts")
        if not pts or len(pts) < _CIRCLE_MIN_POINTS:
            continue
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        distances = [((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in pts]
        mean_d = sum(distances) / len(distances)
        if mean_d <= 0:
            continue
        std_d = statistics.pstdev(distances)
        if std_d / mean_d <= _CIRCLE_RELATIVE_STD_MAX:
            circles.append((index, (cx, cy), mean_d))
    return circles


def _find_labels(records: list[VectorRecord]) -> list[GridLabel]:
    labels: list[GridLabel] = []
    for circle_index, center, radius in _find_circles(records):
        cx, cy = center
        search_radius = radius * _LABEL_SEARCH_RADIUS_FACTOR
        nearby: list[tuple[int, float, str]] = []
        for index, record in enumerate(records):
            if record["object_type"] != "char":
                continue
            char_center = _record_center(record)
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


def _find_collinear_cluster(
    records: list[VectorRecord], coordinate: float, axis: Literal["horizontal", "vertical"]
) -> tuple[list[int], float, float] | None:
    matched: list[int] = []
    lo, hi = None, None

    for index, record in enumerate(records):
        if record["object_type"] != "line":
            continue
        x0, x1 = record.get("x0"), record.get("x1")
        top, bottom = record.get("top"), record.get("bottom")
        if None in (x0, x1, top, bottom):
            continue

        if axis == "horizontal":
            if abs(top - bottom) > _AXIS_ALIGN_TOLERANCE:
                continue
            line_coord = (top + bottom) / 2
            span_lo, span_hi = min(x0, x1), max(x0, x1)
        else:
            if abs(x0 - x1) > _AXIS_ALIGN_TOLERANCE:
                continue
            line_coord = (x0 + x1) / 2
            span_lo, span_hi = min(top, bottom), max(top, bottom)

        if abs(line_coord - coordinate) > _COORDINATE_TOLERANCE:
            continue

        matched.append(index)
        lo = span_lo if lo is None else min(lo, span_lo)
        hi = span_hi if hi is None else max(hi, span_hi)

    if not matched or lo is None or hi is None:
        return None
    return matched, lo, hi


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
            cluster = _find_collinear_cluster(records, coordinate, axis)
            if cluster is None:
                continue
            matched, lo, hi = cluster
            if len(matched) < _MIN_CLUSTER_SEGMENTS:
                continue
            if page_dim is not None and (hi - lo) < page_dim * _MIN_SPAN_RATIO:
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
