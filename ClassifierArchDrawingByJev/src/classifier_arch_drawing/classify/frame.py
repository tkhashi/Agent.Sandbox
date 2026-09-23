"""図面枠・図面メタ情報の分類。

判定方針(詳細はdocs/adr/0009-drawing-frame-classification.mdを参照):

1. ページ最外周にある、同一線種(線幅)の軸並行な直線4本(水平2本+垂直2本)が
   閉じた長方形を構成していれば、それを「図面枠」の外枠とする。複数の候補が
   ある場合は面積最大のものを採用する(最外周=最大の長方形とみなす)
2. 図面枠の外枠(4辺)に端点が接する軸並行の直線は、すべて「図面枠」に属する
   ものとして扱う(表題欄とその他を仕切る線など)
3. 外枠の全幅または全高にわたって伸びる、外枠に接する直線(区画線)を使って
   外枠の内側を格子状に分割する。分割後の領域のうち最大面積のものを
   「図面(本体)」、それ以外を「図面メタ情報」(案件名・スケール・図面番号
   等を記載する表題欄)とする
"""

from __future__ import annotations

from dataclasses import dataclass

from ..schema import VectorRecord
from .geometry import collect_axis_aligned_lines, record_center

_COORDINATE_TOLERANCE = 0.75  # pt。辺同士の座標が一致するとみなす許容値
_TOUCH_TOLERANCE = 1.0  # pt。点が線分上にあるとみなす許容値
_MIN_BORDER_SPAN_RATIO = 0.5  # 図面枠候補とみなす、ページ寸法に対する最低スパン比
# (図面枠は「最外周」の線であり、ページの大部分を占める長さを持つはずなので、
# これ未満の短い線分は候補から除外して組み合わせ爆発を防ぐ)


@dataclass(frozen=True)
class DrawingFrame:
    bounds: tuple[float, float, float, float]  # (x_lo, x_hi, y_lo, y_hi)
    border_line_indices: tuple[int, ...]
    attached_line_indices: tuple[int, ...]
    drawing_bounds: tuple[float, float, float, float]
    meta_bounds: tuple[tuple[float, float, float, float], ...]
    meta_record_indices: tuple[int, ...]


def _point_on_horizontal(px: float, py: float, item: dict) -> bool:
    return abs(py - item["coord"]) <= _TOUCH_TOLERANCE and item["lo"] - _TOUCH_TOLERANCE <= px <= item["hi"] + _TOUCH_TOLERANCE


def _point_on_vertical(px: float, py: float, item: dict) -> bool:
    return abs(px - item["coord"]) <= _TOUCH_TOLERANCE and item["lo"] - _TOUCH_TOLERANCE <= py <= item["hi"] + _TOUCH_TOLERANCE


def _find_frame_rectangle(
    aligned: dict[str, list[dict]],
    page_width: float | None,
    page_height: float | None,
) -> tuple[float, dict, dict, dict, dict] | None:
    """同一線幅の水平2本+垂直2本からなる、面積最大の閉じた長方形を探す。"""
    min_h_span = page_width * _MIN_BORDER_SPAN_RATIO if page_width is not None else 0.0
    min_v_span = page_height * _MIN_BORDER_SPAN_RATIO if page_height is not None else 0.0

    by_linewidth_h: dict[float | None, list[dict]] = {}
    for item in aligned["horizontal"]:
        if item["hi"] - item["lo"] < min_h_span:
            continue
        by_linewidth_h.setdefault(item["linewidth"], []).append(item)
    by_linewidth_v: dict[float | None, list[dict]] = {}
    for item in aligned["vertical"]:
        if item["hi"] - item["lo"] < min_v_span:
            continue
        by_linewidth_v.setdefault(item["linewidth"], []).append(item)

    best: tuple[float, dict, dict, dict, dict] | None = None
    for linewidth, hs in by_linewidth_h.items():
        vs = by_linewidth_v.get(linewidth)
        if not vs or len(hs) < 2 or len(vs) < 2:
            continue
        for i in range(len(hs)):
            for j in range(i + 1, len(hs)):
                h1, h2 = hs[i], hs[j]
                if abs(h1["lo"] - h2["lo"]) > _COORDINATE_TOLERANCE:
                    continue
                if abs(h1["hi"] - h2["hi"]) > _COORDINATE_TOLERANCE:
                    continue
                y_lo, y_hi = sorted((h1["coord"], h2["coord"]))
                for p in range(len(vs)):
                    for q in range(p + 1, len(vs)):
                        v1, v2 = vs[p], vs[q]
                        if abs(v1["lo"] - y_lo) > _COORDINATE_TOLERANCE:
                            continue
                        if abs(v1["hi"] - y_hi) > _COORDINATE_TOLERANCE:
                            continue
                        if abs(v2["lo"] - y_lo) > _COORDINATE_TOLERANCE:
                            continue
                        if abs(v2["hi"] - y_hi) > _COORDINATE_TOLERANCE:
                            continue
                        x_lo, x_hi = sorted((v1["coord"], v2["coord"]))
                        if abs(x_lo - h1["lo"]) > _COORDINATE_TOLERANCE:
                            continue
                        if abs(x_hi - h1["hi"]) > _COORDINATE_TOLERANCE:
                            continue
                        area = (x_hi - x_lo) * (y_hi - y_lo)
                        if best is None or area > best[0]:
                            best = (area, h1, h2, v1, v2)
    return best


def classify_drawing_frame(
    records: list[VectorRecord],
    page_width: float | None = None,
    page_height: float | None = None,
) -> DrawingFrame | None:
    aligned = collect_axis_aligned_lines(records)
    found = _find_frame_rectangle(aligned, page_width, page_height)
    if found is None:
        return None
    _area, h1, h2, v1, v2 = found

    x_lo, x_hi = sorted((v1["coord"], v2["coord"]))
    y_lo, y_hi = sorted((h1["coord"], h2["coord"]))
    border_indices = {h1["index"], h2["index"], v1["index"], v2["index"]}

    # 図面枠の外枠(4辺)に端点が接する軸並行の直線を、すべて図面枠に含める。
    attached_indices: set[int] = set()
    full_width_cuts: list[float] = []
    full_height_cuts: list[float] = []
    for item in aligned["horizontal"]:
        if item["index"] in border_indices:
            continue
        touches_left = _point_on_vertical(item["lo"], item["coord"], v1) or _point_on_vertical(
            item["lo"], item["coord"], v2
        )
        touches_right = _point_on_vertical(item["hi"], item["coord"], v1) or _point_on_vertical(
            item["hi"], item["coord"], v2
        )
        if not (touches_left or touches_right):
            continue
        if not (y_lo - _TOUCH_TOLERANCE <= item["coord"] <= y_hi + _TOUCH_TOLERANCE):
            continue
        attached_indices.add(item["index"])
        if touches_left and touches_right:
            full_width_cuts.append(item["coord"])

    for item in aligned["vertical"]:
        if item["index"] in border_indices:
            continue
        touches_top = _point_on_horizontal(item["coord"], item["lo"], h1) or _point_on_horizontal(
            item["coord"], item["lo"], h2
        )
        touches_bottom = _point_on_horizontal(item["coord"], item["hi"], h1) or _point_on_horizontal(
            item["coord"], item["hi"], h2
        )
        if not (touches_top or touches_bottom):
            continue
        if not (x_lo - _TOUCH_TOLERANCE <= item["coord"] <= x_hi + _TOUCH_TOLERANCE):
            continue
        attached_indices.add(item["index"])
        if touches_top and touches_bottom:
            full_height_cuts.append(item["coord"])

    # 外枠全幅/全高にわたる区画線で、外枠の内側を格子状に分割する
    rows = sorted({y_lo, y_hi, *full_width_cuts})
    cols = sorted({x_lo, x_hi, *full_height_cuts})

    cells: list[tuple[float, float, float, float]] = []
    for r0, r1 in zip(rows, rows[1:]):
        for c0, c1 in zip(cols, cols[1:]):
            cells.append((c0, c1, r0, r1))

    if not cells:
        cells = [(x_lo, x_hi, y_lo, y_hi)]

    drawing_cell = max(cells, key=lambda cell: (cell[1] - cell[0]) * (cell[3] - cell[2]))
    meta_cells = tuple(cell for cell in cells if cell != drawing_cell)

    meta_indices: list[int] = []
    for index, record in enumerate(records):
        center = record_center(record)
        if center is None:
            continue
        cx, cy = center
        for mx0, mx1, my0, my1 in meta_cells:
            if mx0 - _TOUCH_TOLERANCE <= cx <= mx1 + _TOUCH_TOLERANCE and my0 - _TOUCH_TOLERANCE <= cy <= my1 + _TOUCH_TOLERANCE:
                meta_indices.append(index)
                break

    return DrawingFrame(
        bounds=(x_lo, x_hi, y_lo, y_hi),
        border_line_indices=tuple(sorted(border_indices)),
        attached_line_indices=tuple(sorted(attached_indices)),
        drawing_bounds=drawing_cell,
        meta_bounds=meta_cells,
        meta_record_indices=tuple(sorted(meta_indices)),
    )
